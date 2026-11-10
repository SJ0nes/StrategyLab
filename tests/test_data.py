"""Tests for the price data layer.

Every frame here is constructed by hand with a known defect, so each test knows
what it is asserting rather than comparing the implementation against itself.
No test makes a network call: the recorded fixture in ``tests/fixtures`` stands
in for the vendor.
"""

from __future__ import annotations

import math
import sys
import types
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from strategylab.data.source import (
    REQUIRED_COLUMNS,
    CacheEntry,
    DataQualityError,
    YFinanceSource,
    cache_read,
    cache_write,
    content_hash,
    is_cached,
    validate_bars,
)

# Anchored to this file, not to the process working directory. A cwd-relative
# path plus a skipif marker means these tests - including the one that detects
# fixture corruption - silently vanish when pytest runs from anywhere else.
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_TICKER = "SPY"


def clean_frame(days: int = 10) -> pd.DataFrame:
    """Build a small, valid OHLCV frame on consecutive business days.

    Prices rise by 1.0 a day from 100.0 so every bar is distinct and every
    invariant holds: positive prices, high above both open and close, low below
    both, and non-zero volume.

    Args:
        days: Number of bars to generate.

    Returns:
        A frame that :func:`validate_bars` accepts.
    """
    # Rebuilt from the values so the inferred BusinessDay frequency is dropped.
    # Real vendor data never carries one - exchange holidays make trading days
    # irregular - and parquet does not store the attribute. Keeping it would
    # force the round-trip test to compare loosely, which is the wrong
    # direction to relax a test.
    index = pd.DatetimeIndex(pd.bdate_range("2020-01-01", periods=days).to_numpy(), name="date")
    base = [100.0 + i for i in range(days)]
    return pd.DataFrame(
        {
            "open": base,
            "high": [p + 1.0 for p in base],
            "low": [p - 1.0 for p in base],
            "close": [p + 0.5 for p in base],
            "volume": [1_000_000 + i for i in range(days)],
        },
        index=index,
    )


# --------------------------------------------------------------------------
# validate_bars - each test breaks exactly one rule.
# --------------------------------------------------------------------------


def test_clean_frame_passes() -> None:
    validate_bars(clean_frame())  # must not raise


def test_duplicate_timestamp_raises_naming_that_date() -> None:
    df = clean_frame(5)
    df = pd.concat([df, df.iloc[[2]]]).sort_index()
    with pytest.raises(DataQualityError) as excinfo:
        validate_bars(df)
    message = str(excinfo.value)
    assert "duplicate" in message.lower()
    # The offending date itself must appear, not merely a count.
    assert df.index[2].date().isoformat() in message


def test_high_below_low_raises_naming_that_date() -> None:
    df = clean_frame(5)
    df.loc[df.index[3], "high"] = df.loc[df.index[3], "low"] - 5.0
    with pytest.raises(DataQualityError) as excinfo:
        validate_bars(df)
    assert "high below low" in str(excinfo.value)
    assert df.index[3].date().isoformat() in str(excinfo.value)


def test_zero_volume_raises_naming_that_date() -> None:
    df = clean_frame(5)
    df.loc[df.index[1], "volume"] = 0
    with pytest.raises(DataQualityError) as excinfo:
        validate_bars(df)
    assert "volume" in str(excinfo.value)
    assert df.index[1].date().isoformat() in str(excinfo.value)


def test_negative_price_raises_naming_that_date() -> None:
    df = clean_frame(5)
    df.loc[df.index[2], "low"] = -1.0
    with pytest.raises(DataQualityError) as excinfo:
        validate_bars(df)
    assert "non-positive" in str(excinfo.value)
    assert df.index[2].date().isoformat() in str(excinfo.value)


def test_zero_price_raises() -> None:
    df = clean_frame(5)
    df.loc[df.index[2], "low"] = 0.0
    with pytest.raises(DataQualityError, match="non-positive"):
        validate_bars(df)


def test_nan_price_raises() -> None:
    df = clean_frame(5)
    df.loc[df.index[2], "close"] = float("nan")
    with pytest.raises(DataQualityError, match="non-finite"):
        validate_bars(df)


def test_high_not_bracketing_close_raises() -> None:
    # high above low, so the cruder check passes; but close trades above high,
    # which is impossible and would otherwise pass unnoticed.
    df = clean_frame(5)
    df.loc[df.index[2], "close"] = df.loc[df.index[2], "high"] + 5.0
    with pytest.raises(DataQualityError, match="bracket"):
        validate_bars(df)


def test_close_equal_to_high_one_ulp_over_is_accepted() -> None:
    # Real SPY history contains a bar where the adjusted close exceeds the
    # adjusted high by 1.4e-16 relative - one unit in the last place - because
    # the vendor multiplies raw prices by an adjustment factor and rounds the
    # two differently. The day simply closed at its high. An exact `<` rejects
    # it; a relative tolerance does not.
    df = clean_frame(3)
    high = float(df.loc[df.index[1], "high"])
    df.loc[df.index[1], "close"] = math.nextafter(high, math.inf)
    validate_bars(df)  # must not raise


def test_a_real_price_error_is_still_caught() -> None:
    # The counterpart: one penny on a hundred-dollar share is 1e-4 relative,
    # five orders of magnitude above the tolerance, and must still fail.
    df = clean_frame(3)
    df.loc[df.index[1], "close"] = float(df.loc[df.index[1], "high"]) + 0.01
    with pytest.raises(DataQualityError, match="bracket"):
        validate_bars(df)


def test_missing_trading_days_raise() -> None:
    df = clean_frame(10)
    # Drop a fortnight from the middle, leaving a gap no weekend explains.
    df = pd.concat([df.iloc[:3], df.iloc[8:]])
    with pytest.raises(DataQualityError, match="missing trading days"):
        validate_bars(df)


def test_normal_weekend_gap_does_not_raise() -> None:
    # The counterpart to the test above: a Friday-to-Monday gap is three
    # calendar days and must be accepted, or the rule is useless.
    df = clean_frame(10)
    gaps = df.index.to_series().diff().dt.days.dropna()
    assert gaps.max() == 3, "fixture should contain a weekend"
    validate_bars(df)  # must not raise


@pytest.mark.parametrize(
    ("last_bar", "next_bar", "why"),
    [
        # Fri 2006-12-29 -> Wed 2007-01-03. Mon was New Year's Day and Tue was
        # the national day of mourning for President Ford: US markets shut.
        ("2006-12-29", "2007-01-03", "Ford day of mourning"),
        # Fri 2012-10-26 -> Wed 2012-10-31. Hurricane Sandy closed US markets
        # on the Monday and Tuesday.
        ("2012-10-26", "2012-10-31", "Hurricane Sandy"),
    ],
)
def test_real_multi_day_market_closures_are_accepted(
    last_bar: str, next_bar: str, why: str
) -> None:
    # These are verified historical closures, not vendor errors. A calendar-day
    # threshold flags both; counting absent weekday sessions does not. If this
    # test starts failing, the gap rule was tightened without checking it
    # against what actually happened.
    index = pd.DatetimeIndex([pd.Timestamp(last_bar), pd.Timestamp(next_bar)], name="date")
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1_000_000, 1_000_000],
        },
        index=index,
    )
    validate_bars(df)  # must not raise, because of: why


def test_a_dropped_working_week_still_raises() -> None:
    # The counterpart: five absent sessions is a vendor problem, not a holiday.
    index = pd.DatetimeIndex([pd.Timestamp("2020-01-06"), pd.Timestamp("2020-01-14")], name="date")
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [1_000_000, 1_000_000],
        },
        index=index,
    )
    with pytest.raises(DataQualityError, match="missing trading days"):
        validate_bars(df)


def test_unsorted_index_raises() -> None:
    df = clean_frame(5).iloc[::-1]
    with pytest.raises(DataQualityError, match="sorted"):
        validate_bars(df)


def test_empty_frame_raises() -> None:
    with pytest.raises(DataQualityError, match="empty"):
        validate_bars(clean_frame(0))


@pytest.mark.parametrize("column", REQUIRED_COLUMNS)
def test_missing_column_raises(column: str) -> None:
    df = clean_frame(5).drop(columns=[column])
    with pytest.raises(DataQualityError, match="missing column"):
        validate_bars(df)


def test_validate_bars_does_not_modify_the_frame() -> None:
    # It is a gate, not a transform. Silent repair is the failure mode this
    # whole module exists to avoid.
    df = clean_frame(5)
    before = df.copy(deep=True)
    validate_bars(df)
    pd.testing.assert_frame_equal(df, before)


# --------------------------------------------------------------------------
# The cache and its provenance.
# --------------------------------------------------------------------------


def test_cache_round_trip_is_identical(tmp_path: Path) -> None:
    df = clean_frame(20)
    cache_write(df, "TEST", tmp_path, source="unit-test")
    restored, _ = cache_read("TEST", tmp_path)
    pd.testing.assert_frame_equal(restored, df)


def test_provenance_records_what_was_written(tmp_path: Path) -> None:
    df = clean_frame(20)
    entry = cache_write(df, "TEST", tmp_path, source="unit-test")
    assert entry.ticker == "TEST"
    assert entry.source == "unit-test"
    assert entry.rows == 20
    assert entry.start == df.index[0].date()
    assert entry.end == df.index[-1].date()
    assert len(entry.content_hash) == 64


def test_provenance_hash_changes_when_the_data_changes(tmp_path: Path) -> None:
    # The whole point of the hash: a vendor revising one price must be
    # detectable, because otherwise a result changes with no explanation.
    df = clean_frame(20)
    first = cache_write(df, "TEST", tmp_path, source="unit-test")

    revised = df.copy()
    revised.loc[revised.index[7], "close"] += 0.01
    second = cache_write(revised, "TEST", tmp_path, source="unit-test")

    assert first.content_hash != second.content_hash


def test_provenance_hash_is_stable_for_identical_data(tmp_path: Path) -> None:
    df = clean_frame(20)
    assert content_hash(df) == content_hash(df.copy(deep=True))


def test_tampered_cache_is_detected(tmp_path: Path) -> None:
    df = clean_frame(20)
    entry = cache_write(df, "TEST", tmp_path, source="unit-test")

    # Rewrite the parquet without touching the provenance, as an edit outside
    # this system would.
    tampered = df.copy()
    tampered.loc[tampered.index[0], "close"] = 999.0
    tampered.to_parquet(entry.path, index=True)

    with pytest.raises(DataQualityError, match="does not match its provenance"):
        cache_read("TEST", tmp_path)


def test_cache_read_without_provenance_refuses(tmp_path: Path) -> None:
    df = clean_frame(5)
    entry = cache_write(df, "TEST", tmp_path, source="unit-test")
    (tmp_path / f"TEST{'.provenance.json'}").unlink()
    with pytest.raises(FileNotFoundError, match="provenance"):
        cache_read("TEST", tmp_path)
    assert entry.path.exists()


def test_cache_read_missing_ticker_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no cached data"):
        cache_read("ABSENT", tmp_path)


def test_cache_write_refuses_an_empty_frame(tmp_path: Path) -> None:
    with pytest.raises(DataQualityError, match="empty"):
        cache_write(clean_frame(0), "TEST", tmp_path)


def test_is_cached_requires_both_files(tmp_path: Path) -> None:
    assert not is_cached("TEST", tmp_path)
    cache_write(clean_frame(5), "TEST", tmp_path, source="unit-test")
    assert is_cached("TEST", tmp_path)


def test_ticker_with_a_dot_is_written_to_a_safe_filename(tmp_path: Path) -> None:
    # VUAG.L is the execution instrument; a bare dot in a filename would
    # collide with the extension.
    entry = cache_write(clean_frame(5), "VUAG.L", tmp_path, source="unit-test")
    assert entry.path.name == "VUAG_L.parquet"
    restored, _ = cache_read("VUAG.L", tmp_path)
    assert len(restored) == 5


def test_provenance_json_round_trips() -> None:
    entry = CacheEntry(
        ticker="TEST",
        source="unit-test",
        fetched_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        rows=7,
        start=date(2020, 1, 1),
        end=date(2020, 1, 9),
        content_hash="a" * 64,
        path=Path("data/TEST.parquet"),
    )
    assert CacheEntry.from_json(entry.to_json()) == entry


def test_unreadable_provenance_raises() -> None:
    with pytest.raises(DataQualityError, match="unreadable"):
        CacheEntry.from_json("{not json")


# --------------------------------------------------------------------------
# The recorded fixture. Stands in for the vendor so no test hits the network.
# --------------------------------------------------------------------------


def test_recorded_fixture_loads_and_is_valid() -> None:
    df, entry = cache_read(FIXTURE_TICKER, FIXTURE_DIR)
    validate_bars(df)
    # Compared against the class attribute rather than a literal: the vendor
    # name belongs to the adapter, and no test should hardcode it.
    assert entry.source == YFinanceSource.name
    assert list(df.columns) == list(REQUIRED_COLUMNS)
    # Roughly one year of trading days. A wildly different count means the
    # fixture was re-recorded over a different window.
    assert 240 <= len(df) <= 260


def test_recorded_fixture_matches_its_recorded_hash() -> None:
    # If this fails, the committed fixture changed. That is either a deliberate
    # re-record - which must be a commit of its own - or corruption.
    df, entry = cache_read(FIXTURE_TICKER, FIXTURE_DIR)
    assert content_hash(df) == entry.content_hash


def test_recorded_fixture_sits_inside_the_in_sample_window() -> None:
    # A fixture drawn from the held-back window would leak it into every test
    # run, which is the one thing the split exists to prevent.
    _, entry = cache_read(FIXTURE_TICKER, FIXTURE_DIR)
    assert entry.start >= date(2005, 1, 1)
    assert entry.end <= date(2018, 12, 31)


def test_nan_volume_raises() -> None:
    # `NaN <= 0` is False, so a NaN volume slips through a bare comparison and
    # passes validation entirely. validate_bars is the only gate between the
    # vendor and the cache, so a silent pass here reaches every later result.
    df = clean_frame(5)
    df["volume"] = df["volume"].astype(float)
    df.loc[df.index[2], "volume"] = float("nan")
    with pytest.raises(DataQualityError, match="non-finite volume"):
        validate_bars(df)


def test_infinite_volume_raises() -> None:
    df = clean_frame(5)
    df["volume"] = df["volume"].astype(float)
    df.loc[df.index[1], "volume"] = float("inf")
    with pytest.raises(DataQualityError, match="non-finite volume"):
        validate_bars(df)


# --------------------------------------------------------------------------
# The fetch path, against a stand-in for the vendor library.
# --------------------------------------------------------------------------
#
# NO TEST MAY CALL THE VENDOR, so `yfinance` is replaced in `sys.modules` by an
# object with one function. `YFinanceSource.fetch` imports the library inside
# its own body, so it picks the stand-in up; the real one is never imported.
#
# WHAT THIS PROVES is this side of the call: what the code asks the vendor for,
# and what it does with a frame shaped the way the vendor shapes them. It proves
# nothing about the vendor. Whether Yahoo still answers in these shapes is
# established by running `strategylab fetch`, and by nothing here.


class _Vendor(types.ModuleType):
    """A stand-in for the `yfinance` module that records what it was asked."""

    def __init__(self, answer: pd.DataFrame | None) -> None:
        super().__init__("yfinance")
        self.answer = answer
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def download(self, ticker: str, **kwargs: Any) -> pd.DataFrame | None:
        self.calls.append((ticker, kwargs))
        return self.answer


def _vendor_frame(tickered: bool = False) -> pd.DataFrame:
    """Three bars the way the vendor returns them: capitalised, zoned, newest first."""
    index = pd.DatetimeIndex(
        ["2020-01-06", "2020-01-03", "2020-01-02"], tz="America/New_York", name="Date"
    )
    frame = pd.DataFrame(
        {
            "Open": [103.0, 102.0, 100.0],
            "High": [104.0, 103.5, 101.0],
            "Low": [102.5, 101.0, 99.5],
            "Close": [103.5, 102.5, 100.5],
            "Adj Close": [1.0, 1.0, 1.0],
            "Volume": [3000, 2000, 1000],
        },
        index=index,
    )
    if tickered:
        frame.columns = pd.MultiIndex.from_product([frame.columns, ["SPY"]])
    return frame


def _fetch_with(vendor: _Vendor, monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    """Run a fetch for the first week of 2020 with the stand-in in place."""
    monkeypatch.setitem(sys.modules, "yfinance", vendor)
    return YFinanceSource().fetch("SPY", date(2020, 1, 2), date(2020, 1, 6))


def test_fetch_asks_for_adjusted_daily_bars_through_the_last_day_inclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vendor = _Vendor(_vendor_frame())

    _fetch_with(vendor, monkeypatch)

    ((ticker, asked),) = vendor.calls
    assert ticker == "SPY"
    assert asked["start"] == "2020-01-02"
    # The vendor treats its end as EXCLUSIVE, so the request names the day after
    # the last day wanted. Asking for the 6th would return bars to the 3rd.
    assert asked["end"].startswith("2020-01-07")
    assert asked["interval"] == "1d"
    # Adjusted, because an unadjusted series has a cliff at every split and a
    # moving average across one is measuring the split.
    assert asked["auto_adjust"] is True


@pytest.mark.parametrize("tickered", [False, True], ids=["flat-columns", "field-and-ticker"])
def test_fetch_returns_the_vendors_bars_in_this_projects_shape(
    monkeypatch: pytest.MonkeyPatch, tickered: bool
) -> None:
    bars = _fetch_with(_Vendor(_vendor_frame(tickered)), monkeypatch)

    assert list(bars.columns) == list(REQUIRED_COLUMNS)
    assert bars.index.name == "date"
    assert bars.index.tz is None
    # Oldest first, though the vendor handed them over newest first.
    assert [stamp.date() for stamp in bars.index] == [
        date(2020, 1, 2),
        date(2020, 1, 3),
        date(2020, 1, 6),
    ]
    # Each bar kept its own row through the reordering.
    assert bars.loc["2020-01-02", "open"] == 100.0
    assert bars.loc["2020-01-02", "volume"] == 1000
    assert bars.loc["2020-01-06", "close"] == 103.5
    validate_bars(bars)


@pytest.mark.parametrize("answer", [None, pd.DataFrame()], ids=["nothing", "an-empty-frame"])
def test_a_vendor_that_returns_no_rows_is_an_error_not_an_empty_cache(
    monkeypatch: pytest.MonkeyPatch, answer: pd.DataFrame | None
) -> None:
    with pytest.raises(DataQualityError, match="no rows"):
        _fetch_with(_Vendor(answer), monkeypatch)


def test_a_column_the_vendor_names_twice_is_kept_once_and_the_first_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # "Close" and "close" are one name once lowercased. Kept twice, `close`
    # would be two columns and every later read of it a frame, not a series.
    doubled = _vendor_frame()
    doubled["close"] = 999.0

    bars = _fetch_with(_Vendor(doubled), monkeypatch)

    assert list(bars.columns).count("close") == 1
    assert bars.loc["2020-01-06", "close"] == 103.5


def test_a_vendor_frame_missing_a_column_is_refused_naming_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DataQualityError, match="volume"):
        _fetch_with(_Vendor(_vendor_frame().drop(columns=["Volume"])), monkeypatch)


def test_fetching_does_not_validate_so_a_bad_bar_reaches_the_check_that_names_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `fetch` reshapes and `validate_bars` judges, so the same rules apply to a
    # fresh download and to a cached one. A bar whose high is under its low
    # comes back from `fetch` untouched and is refused one step later.
    broken = _vendor_frame()
    broken.loc[broken.index[0], "High"] = 1.0

    bars = _fetch_with(_Vendor(broken), monkeypatch)

    with pytest.raises(DataQualityError):
        validate_bars(bars)

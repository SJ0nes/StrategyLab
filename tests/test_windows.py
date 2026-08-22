"""Tests for the shared window accessor and the cost of a held-back read.

THE ASYMMETRY IS THE DESIGN, and it is what these tests are mostly about. The
in-sample window is free and repeatable. The walk-forward window costs a touch,
recorded permanently in `protocol.yaml`, and there is no way to obtain those
bars without paying - no dry run, no read-only mode, no exception for a caller
who means well. A counter a caller can decline to increment measures nothing.

**No test here touches the repository's own `protocol.yaml`.** Every one writes
a temporary copy and increments that. A counter polluted by test noise could not
be read as evidence of anything, and this repository's whole argument is that
its numbers can be.

**What no test here claims.** Nothing about whether the held-back window has
ever been read for real. It has not; `protocol.yaml` still reads zero, and
`tests/test_protocol.py` covers the counter's own arithmetic.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from strategylab.backtest.costs import CostModel
from strategylab.protocol import (
    Instruments,
    Protocol,
    Validation,
    Window,
    Windows,
    load_protocol,
)
from strategylab.windows import (
    WindowError,
    in_sample_bars,
    walk_forward_bars,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_PROTOCOL = REPO_ROOT / "protocol.yaml"

IN_SAMPLE = Window(start=date(2010, 1, 1), end=date(2010, 6, 30))
HELD_BACK = Window(start=date(2010, 7, 1), end=date(2011, 12, 31))


def _protocol() -> Protocol:
    """A protocol whose two windows split the recorded fixture in half."""
    return Protocol(
        protocol_version=1,
        created=date(2026, 8, 21),
        instruments=Instruments(research="SPY", execution="VUAG.L"),
        windows=Windows(in_sample=IN_SAMPLE, walk_forward=HELD_BACK, fold_months=12),
        costs=CostModel(
            spread_frac=0.0003,
            slippage_frac=0.0005,
            commission_frac=0.0005,
        ),
        validation=Validation(
            primary_metric="sharpe",
            min_sharpe=1.0,
            min_trades_per_fold=10,
            min_trades_pooled=30,
            max_drawdown_ceiling_pct=25.0,
            pass_margin=0.2,
        ),
        walk_forward_touches=0,
    )


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    """The recorded SPY bars, spanning both windows. No network call."""
    return pd.read_parquet(Path(__file__).resolve().parent / "fixtures" / "SPY.parquet")


@pytest.fixture
def protocol_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the real protocol, safe to increment."""
    path = tmp_path / "protocol.yaml"
    path.write_bytes(REPO_PROTOCOL.read_bytes())
    return path


# --------------------------------------------------------------------------
# In-sample: free, and the same every time.
# --------------------------------------------------------------------------


def test_the_in_sample_window_holds_only_its_own_dates(bars: pd.DataFrame) -> None:
    window = in_sample_bars(bars, _protocol())
    stamps = pd.DatetimeIndex(window.index)

    assert stamps.min().date() >= IN_SAMPLE.start
    assert stamps.max().date() <= IN_SAMPLE.end
    assert len(window) < len(bars), "the fixture must straddle the split to test this"


def test_reading_the_in_sample_window_costs_nothing(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    # Free and unlimited: that is what it is for. Ten reads, counter untouched.
    protocol = _protocol()
    for _ in range(10):
        in_sample_bars(bars, protocol)
    assert load_protocol(protocol_copy).walk_forward_touches == 0


def test_the_in_sample_window_is_the_same_every_time(bars: pd.DataFrame) -> None:
    protocol = _protocol()
    pd.testing.assert_frame_equal(in_sample_bars(bars, protocol), in_sample_bars(bars, protocol))


# --------------------------------------------------------------------------
# Walk-forward: it costs, and it cannot be had for free.
# --------------------------------------------------------------------------


def test_reading_the_held_back_window_charges_a_touch(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    assert load_protocol(protocol_copy).walk_forward_touches == 0

    window, touches = walk_forward_bars(bars, _protocol(), protocol_copy)

    assert touches == 1
    assert load_protocol(protocol_copy).walk_forward_touches == 1
    stamps = pd.DatetimeIndex(window.index)
    assert stamps.min().date() >= HELD_BACK.start


def test_every_read_costs_another_touch(bars: pd.DataFrame, protocol_copy: Path) -> None:
    # The split degrades with USE. The first candidate tested gets a clean test
    # and the twentieth does not, so the count must track reads and not runs,
    # callers, or candidates.
    counts = [walk_forward_bars(bars, _protocol(), protocol_copy)[1] for _ in range(3)]
    assert counts == [1, 2, 3]
    assert load_protocol(protocol_copy).walk_forward_touches == 3


def test_the_touch_is_charged_even_when_the_slice_then_fails(protocol_copy: Path) -> None:
    # Charged FIRST, deliberately. Over-counting a read that did not happen
    # overstates the erosion; understating it is the error that matters.
    empty = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=pd.DatetimeIndex(["2005-01-03"]),
    )
    with pytest.raises(WindowError, match="no bar falls inside the walk-forward window"):
        walk_forward_bars(empty, _protocol(), protocol_copy)

    assert load_protocol(protocol_copy).walk_forward_touches == 1


def test_there_is_no_way_to_get_held_back_bars_without_paying() -> None:
    # THE PROPERTY THE WHOLE MODULE EXISTS FOR, asserted against the module's
    # surface rather than trusted. Every public name that could hand back
    # walk-forward bars must require the protocol path it will increment.
    import inspect

    from strategylab import windows

    # Defined here, not merely imported here: `increment_walk_forward_touches`
    # is in this namespace and would otherwise be counted as an accessor.
    public = [
        name
        for name, value in vars(windows).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        and value.__module__ == windows.__name__
    ]
    # Exactly one public accessor reaches the held-back window.
    held_back = [name for name in public if "walk_forward" in name]
    assert held_back == ["walk_forward_bars"]

    signature = inspect.signature(windows.walk_forward_bars)
    assert "protocol_path" in signature.parameters
    # And no flag exists that could switch the charging off.
    assert not any(
        word in name for name in signature.parameters for word in ("dry", "count", "charge")
    )


def test_an_overlapping_pair_of_windows_cannot_be_constructed() -> None:
    # THE HOLE THIS CLOSES. `in_sample_bars` trusts the protocol it is handed,
    # so a protocol built in code with overlapping windows returned held-back
    # bars for free - measured at 129 of them, counter untouched. `load_protocol`
    # refuses an overlapping pair from a FILE; nothing refused one from a
    # constructor.
    #
    # It is now an invariant of the type: there is no overlapping `Windows` to
    # hand anybody, so no accessor has to remember to check.
    with pytest.raises(ValueError, match="overlap"):
        Windows(
            in_sample=Window(start=date(2010, 1, 1), end=date(2011, 12, 31)),
            walk_forward=Window(start=date(2010, 7, 1), end=date(2011, 12, 31)),
            fold_months=12,
        )


def test_windows_touching_on_a_single_day_still_overlap() -> None:
    # The boundary. Sharing one date is sharing data.
    with pytest.raises(ValueError, match="overlap"):
        Windows(
            in_sample=Window(start=date(2010, 1, 1), end=date(2010, 7, 1)),
            walk_forward=Window(start=date(2010, 7, 1), end=date(2010, 12, 31)),
            fold_months=12,
        )


def test_consecutive_windows_are_accepted() -> None:
    # And the accepting side, so an off-by-one in the comparison would show.
    windows = Windows(
        in_sample=Window(start=date(2010, 1, 1), end=date(2010, 6, 30)),
        walk_forward=Window(start=date(2010, 7, 1), end=date(2010, 12, 31)),
        fold_months=12,
    )
    assert windows.in_sample.end < windows.walk_forward.start


# --------------------------------------------------------------------------
# Refusals, shared by both windows.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("accessor", [in_sample_bars, "walk_forward"])
def test_a_frame_not_indexed_by_timestamp_is_refused(accessor: object, protocol_copy: Path) -> None:
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=["not-a-date"],
    )
    with pytest.raises(WindowError, match="not a DatetimeIndex"):
        if accessor == "walk_forward":
            walk_forward_bars(frame, _protocol(), protocol_copy)
        else:
            in_sample_bars(frame, _protocol())


def test_an_integer_index_is_refused_rather_than_read_as_epoch_nanoseconds() -> None:
    # pandas would read those integers as nanoseconds since the epoch and date
    # the whole frame to 1970 rather than refusing it.
    frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=[0],
    )
    with pytest.raises(WindowError, match="nanoseconds since the epoch"):
        in_sample_bars(frame, _protocol())


def test_a_timezone_aware_index_is_refused(bars: pd.DataFrame) -> None:
    aware = bars.copy()
    aware.index = pd.DatetimeIndex(aware.index).tz_localize("America/New_York")
    with pytest.raises(WindowError, match="timezone-aware"):
        in_sample_bars(aware, _protocol())


def test_a_frame_with_no_bar_in_the_window_is_refused(bars: pd.DataFrame) -> None:
    protocol = Protocol(
        protocol_version=1,
        created=date(2026, 8, 21),
        instruments=Instruments(research="SPY", execution="VUAG.L"),
        windows=Windows(
            in_sample=Window(start=date(1990, 1, 1), end=date(1990, 12, 31)),
            walk_forward=Window(start=date(1991, 1, 1), end=date(1991, 12, 31)),
            fold_months=12,
        ),
        costs=_protocol().costs,
        validation=_protocol().validation,
        walk_forward_touches=0,
    )
    with pytest.raises(WindowError, match="no bar falls inside the in-sample window"):
        in_sample_bars(bars, protocol)


def test_the_two_windows_do_not_overlap_on_real_bars(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    # The split is only a split if the two slices are disjoint. `load_protocol`
    # refuses overlapping windows AS DECLARED; this checks the bars that come
    # back really are disjoint, which is the property a reader cares about.
    protocol = _protocol()
    early = in_sample_bars(bars, protocol)
    late, _ = walk_forward_bars(bars, protocol, protocol_copy)

    assert set(early.index).isdisjoint(set(late.index))
    assert pd.DatetimeIndex(early.index).max() < pd.DatetimeIndex(late.index).min()

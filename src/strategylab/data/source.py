"""Price data: fetching, quality validation, and a cache that carries provenance.

Data comes first because everything downstream inherits its errors. A wrong
price does not crash anything; it quietly invalidates every result computed on
top of it, and no test further down the pipeline will notice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

# Suffix for the JSON sidecar written next to each cached parquet file.
PROVENANCE_SUFFIX = ".provenance.json"

# How many weekday sessions may be absent between two consecutive bars before
# the gap is treated as missing data.
#
# Counted in BUSINESS days, not calendar days, so weekends contribute nothing
# and the threshold means what it says. Three accommodates every ordinary
# exchange closure and the multi-day ones that actually occurred in the window
# this project uses: the 2007-01-02 national day of mourning for President Ford
# (2 sessions missing alongside New Year's Day) and the Hurricane Sandy closure
# of 2012-10-29 and 2012-10-30 (2 sessions). A vendor dropping a working week
# is 5 and still trips it.
#
# This is a heuristic and NOT an exchange calendar - see the note in
# validate_bars.
MAX_MISSING_SESSIONS = 3

# Relative tolerance for the ordering comparisons between open, high, low, and
# close. Adjusted prices are raw prices multiplied by a split/dividend factor,
# so a bar that genuinely closed at its high can come back with the two values
# differing by one unit in the last place - about 1e-16 relative. This sits far
# above that and far below any error a human would care about.
PRICE_REL_TOLERANCE = 1e-9


class DataQualityError(Exception):
    """Raised when price data fails a quality or integrity check.

    Covers the rules enforced by :func:`validate_bars`, an empty response from a
    vendor, an empty frame handed to :func:`cache_write`, and a cached frame
    whose content hash no longer matches its provenance.

    Where the failure concerns particular bars, the message names the offending
    dates. A count alone is useless: the operator needs to look at the bars in
    question and decide whether the vendor is wrong or the market genuinely did
    that.
    """


@dataclass(frozen=True)
class CacheEntry:
    """Provenance for one cached price frame.

    Vendors revise history - splits and dividend adjustments change past values
    - so "same code, same dates" does not mean same numbers six months from
    now. Results are reproducible against this snapshot, not against the vendor.

    Attributes:
        ticker: The symbol the frame holds.
        source: Name of the adapter that fetched it, such as ``yfinance``.
        fetched_at: UTC timestamp of the fetch, as supplied by the caller that
            performed it. When absent, the write time is recorded instead and
            that substitution is the caller's to make honestly.
        rows: Number of bars in the frame.
        start: Date of the first bar.
        end: Date of the last bar.
        content_hash: Hex SHA-256 over the frame's values and index, so a silent
            vendor revision shows up as a changed hash rather than as a changed
            result with no explanation.
        path: Location of the parquet file itself, relative to the working
            directory.
    """

    ticker: str
    source: str
    fetched_at: datetime
    rows: int
    start: date
    end: date
    content_hash: str
    path: Path

    def to_json(self) -> str:
        """Render the provenance as indented JSON.

        Returns:
            The record as a JSON object string, dates and timestamps in
            ISO-8601 and the path in POSIX form so the sidecar is identical on
            every platform.
        """
        return json.dumps(
            {
                "ticker": self.ticker,
                "source": self.source,
                "fetched_at": self.fetched_at.isoformat(),
                "rows": self.rows,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "content_hash": self.content_hash,
                "path": self.path.as_posix(),
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> CacheEntry:
        """Rebuild a provenance record from its JSON form.

        Args:
            text: JSON produced by :meth:`to_json`.

        Returns:
            The parsed record.

        Raises:
            DataQualityError: If the JSON is malformed or omits a field.
        """
        try:
            raw: dict[str, Any] = json.loads(text)
            return cls(
                ticker=raw["ticker"],
                source=raw["source"],
                fetched_at=datetime.fromisoformat(raw["fetched_at"]),
                rows=int(raw["rows"]),
                start=date.fromisoformat(raw["start"]),
                end=date.fromisoformat(raw["end"]),
                content_hash=raw["content_hash"],
                path=Path(raw["path"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise DataQualityError(f"cache provenance is unreadable: {exc}") from exc


class PriceSource(Protocol):
    """A source of daily OHLCV bars.

    Implementations differ only in where the bytes come from. Anything that
    needs to know which vendor produced a frame reads the frame's
    :class:`CacheEntry`, not the source object.
    """

    name: str

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily bars for ``ticker`` between ``start`` and ``end``.

        Args:
            ticker: Vendor symbol to fetch.
            start: First date to request, inclusive.
            end: Last date to request, inclusive.

        Returns:
            Bars indexed by ascending timestamp, with the columns named in
            :data:`REQUIRED_COLUMNS`, lowercased.

        Raises:
            DataQualityError: If the vendor returns no rows for the request.
        """
        ...


def _normalise(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Reduce a vendor frame to this project's canonical bar shape.

    Lowercases and de-duplicates column names, drops everything outside
    :data:`REQUIRED_COLUMNS`, names the index ``date``, and sorts ascending.
    Performs no quality checks - that is :func:`validate_bars`, deliberately
    separate so the same rules apply to fresh and cached data alike.

    Args:
        df: The vendor's frame.
        ticker: Symbol being normalised, used in error messages.

    Returns:
        The canonical frame.

    Raises:
        DataQualityError: If the frame is empty or lacks a required column.
    """
    if df.empty:
        raise DataQualityError(f"{ticker}: vendor returned no rows")

    frame = df.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        # yfinance returns a (field, ticker) MultiIndex for some requests.
        frame.columns = frame.columns.get_level_values(0)
    frame.columns = [str(c).lower().replace(" ", "_") for c in frame.columns]
    frame = frame.loc[:, ~frame.columns.duplicated()]

    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise DataQualityError(f"{ticker}: vendor frame is missing column(s) {missing}")

    frame = frame.loc[:, list(REQUIRED_COLUMNS)]
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame.index.name = "date"
    return frame.sort_index()


def _dates(index: pd.Index, positions: Any) -> list[str]:
    """Render selected index positions as ISO date strings for a message.

    Args:
        index: The frame's index.
        positions: Boolean mask or index selection identifying offending rows.

    Returns:
        Up to ten ISO-8601 dates, so a message stays readable when a whole
        frame is bad.
    """
    offending = pd.DatetimeIndex(index[positions])
    shown = [d.date().isoformat() for d in offending[:10]]
    if len(offending) > 10:
        shown.append(f"... and {len(offending) - 10} more")
    return shown


def validate_bars(df: pd.DataFrame) -> None:
    """Check a price frame against every rule the rest of the system assumes.

    Raises on: an empty frame, an index that is not a ``DatetimeIndex``, a
    missing required column, a non-numeric price or volume column, duplicate
    timestamps, an unsorted index, non-finite or non-positive prices, ``high``
    below ``low``, ``high`` or ``low`` inconsistent with ``open``/``close``,
    non-finite or non-positive volume, and more than
    :data:`MAX_MISSING_SESSIONS` absent weekday sessions between consecutive
    bars.

    Never interpolates. A filled-in bar is a fabricated observation, and
    fabricated observations are indistinguishable from real ones by the time
    they reach a result.

    The missing-day rule counts WEEKDAY SESSIONS, not calendar days, so a
    weekend contributes nothing to the count. It is a HEURISTIC, not an
    exchange calendar.
    It catches a vendor dropping a run of days; it does not know that a
    particular Thursday was Thanksgiving. A real exchange calendar would need a
    dependency and the instrument's venue, neither of which this function has.
    Treat a raised gap as "look at these dates", not as proof of vendor error.

    Args:
        df: Bars indexed by timestamp, carrying the columns named in
            :data:`REQUIRED_COLUMNS`. Prices are in the instrument's own
            currency; volume is in shares.

    Returns:
        None. This function is a gate, not a transform; it never modifies or
        returns the frame.

    Raises:
        DataQualityError: On any violation, naming the offending dates.
    """
    if df.empty:
        raise DataQualityError("price frame is empty")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataQualityError(f"price frame is missing column(s) {missing}")

    if not isinstance(df.index, pd.DatetimeIndex):
        raise DataQualityError(
            f"price frame index must be a DatetimeIndex, got {type(df.index).__name__}"
        )

    duplicated = df.index.duplicated(keep=False)
    if bool(duplicated.any()):
        raise DataQualityError(f"duplicate timestamps at {_dates(df.index, duplicated)}")

    if not df.index.is_monotonic_increasing:
        raise DataQualityError("price frame index is not sorted ascending")

    prices = df[["open", "high", "low", "close"]]

    try:
        values = prices.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise DataQualityError(f"price column is not numeric: {exc}") from exc
    non_finite = ~np.isfinite(values).all(axis=1)
    if bool(non_finite.any()):
        raise DataQualityError(f"non-finite price at {_dates(df.index, non_finite)}")

    non_positive = (prices <= 0).any(axis=1)
    if bool(non_positive.any()):
        raise DataQualityError(f"non-positive price at {_dates(df.index, non_positive)}")

    # Compared with a relative tolerance rather than exactly. A day that closes
    # at its high is ordinary, and vendors compute adjusted prices by
    # multiplying raw prices by a split/dividend factor - which can round the
    # high and the close differently in the last bit. Real SPY history contains
    # such a bar, off by 1.4e-16 relative, and an exact `<` rejects it. The
    # tolerance is seven orders of magnitude above that noise and five below a
    # one-cent error on a hundred-dollar share, so it cannot mask a real defect.
    lower = 1.0 - PRICE_REL_TOLERANCE
    upper = 1.0 + PRICE_REL_TOLERANCE

    inverted = df["high"] < df["low"] * lower
    if bool(inverted.any()):
        raise DataQualityError(f"high below low at {_dates(df.index, inverted)}")

    outside = (
        (df["high"] < df["open"] * lower)
        | (df["high"] < df["close"] * lower)
        | (df["low"] > df["open"] * upper)
        | (df["low"] > df["close"] * upper)
    )
    if bool(outside.any()):
        raise DataQualityError(f"high/low do not bracket open/close at {_dates(df.index, outside)}")

    # Volume is checked for finiteness FIRST and separately. `NaN <= 0` is
    # False, so a NaN volume would otherwise slip through the comparison below
    # and pass validation entirely - and this function is the only gate between
    # the vendor and the cache.
    try:
        volume = df["volume"].to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise DataQualityError(f"volume column is not numeric: {exc}") from exc
    bad_volume = ~np.isfinite(volume)
    if bool(bad_volume.any()):
        raise DataQualityError(f"non-finite volume at {_dates(df.index, bad_volume)}")

    zero_volume = volume <= 0
    if bool(zero_volume.any()):
        raise DataQualityError(f"zero or negative volume at {_dates(df.index, zero_volume)}")

    if len(df.index) > 1:
        # Business days strictly between each pair of consecutive bars.
        # busday_count spans [begin, end), and the earlier bar is itself a
        # weekday session, so subtracting one leaves only the absent sessions.
        days = df.index.to_numpy(dtype="datetime64[D]")
        absent_sessions = np.busday_count(days[:-1], days[1:]) - 1
        gapped = np.zeros(len(days), dtype=bool)
        gapped[1:] = absent_sessions > MAX_MISSING_SESSIONS
        if bool(gapped.any()):
            raise DataQualityError(
                f"missing trading days before {_dates(df.index, gapped)}; "
                f"more than {MAX_MISSING_SESSIONS} weekday sessions absent"
            )


def content_hash(df: pd.DataFrame) -> str:
    """Compute a stable SHA-256 over a price frame's index and values.

    Stable across platforms and pandas versions: the frame is rendered to CSV
    with a fixed float format and a fixed date format, then hashed as UTF-8
    bytes. Hashing the in-memory buffer directly would make the digest depend
    on dtype and memory layout, which change without the data changing.

    Args:
        df: The frame to hash.

    Returns:
        The digest as a lowercase hex string.
    """
    payload = df.to_csv(float_format="%.10f", date_format="%Y-%m-%dT%H:%M:%S", lineterminator="\n")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _paths(ticker: str, path: Path) -> tuple[Path, Path]:
    """Return the parquet and provenance paths for a ticker in a cache directory.

    Args:
        ticker: Symbol being cached. Characters awkward in filenames are
            replaced, so ``VUAG.L`` becomes ``VUAG_L``.
        path: Cache directory.

    Returns:
        The parquet path and the provenance sidecar path.
    """
    stem = ticker.replace(".", "_").replace("/", "_").replace("^", "_")
    return path / f"{stem}.parquet", path / f"{stem}{PROVENANCE_SUFFIX}"


def cache_write(
    df: pd.DataFrame,
    ticker: str,
    path: Path,
    source: str = "unknown",
    fetched_at: datetime | None = None,
) -> CacheEntry:
    """Write a price frame to parquet alongside its provenance.

    The provenance sidecar records fetch timestamp, source name, row count, and
    a content hash. Vendors revise history, so the hash is what makes a result
    reproducible: it pins the answer to this snapshot rather than to whatever
    the vendor serves next time.

    Args:
        df: Bars to write. Not validated here - call :func:`validate_bars`
            first if the frame has not already been checked.
        ticker: Symbol the frame holds, used to name the file.
        path: Directory to write into, relative to the working directory.
            Created if absent.
        source: Name of the adapter that fetched the data. Defaults to
            ``unknown`` rather than being guessed, since this function cannot
            observe where the frame came from.
        fetched_at: UTC timestamp of the fetch. Defaults to now, which is the
            write time; when the two differ the caller should pass the real
            fetch time.

    Returns:
        The provenance record for what was written.

    Raises:
        DataQualityError: If ``df`` is empty.
    """
    if df.empty:
        raise DataQualityError(f"{ticker}: refusing to cache an empty frame")

    path.mkdir(parents=True, exist_ok=True)
    parquet_path, provenance_path = _paths(ticker, path)

    df.to_parquet(parquet_path, index=True)

    index = pd.DatetimeIndex(df.index)
    entry = CacheEntry(
        ticker=ticker,
        source=source,
        fetched_at=fetched_at if fetched_at is not None else datetime.now(UTC),
        rows=len(df),
        start=index[0].date(),
        end=index[-1].date(),
        content_hash=content_hash(df),
        path=parquet_path,
    )
    # Trailing newline so the sidecar is a well-formed POSIX text file and
    # end-of-file fixers have nothing to correct.
    provenance_path.write_text(entry.to_json() + "\n", encoding="utf-8")
    return entry


def cache_read(ticker: str, path: Path) -> tuple[pd.DataFrame, CacheEntry]:
    """Read a cached price frame and its provenance back.

    Args:
        ticker: Symbol to read, used to name the file.
        path: Directory to read from, relative to the working directory.

    Returns:
        The cached bars and the provenance recorded when they were written.

    Raises:
        FileNotFoundError: If no cache entry exists for ``ticker``.
        DataQualityError: If the provenance is unreadable, or the frame's
            content hash no longer matches the one recorded in it - which means
            the file was altered after it was written.
    """
    parquet_path, provenance_path = _paths(ticker, path)
    if not parquet_path.exists():
        raise FileNotFoundError(f"{ticker}: no cached data at '{parquet_path}'")
    if not provenance_path.exists():
        raise FileNotFoundError(
            f"{ticker}: cached data at '{parquet_path}' has no provenance sidecar; "
            "a frame without provenance is not reproducible and is refused"
        )

    entry = CacheEntry.from_json(provenance_path.read_text(encoding="utf-8"))
    df = pd.read_parquet(parquet_path)
    actual = content_hash(df)
    if actual != entry.content_hash:
        raise DataQualityError(
            f"{ticker}: cached frame does not match its provenance hash "
            f"(recorded {entry.content_hash[:12]}..., found {actual[:12]}...); "
            "the file was altered after it was written"
        )
    return df, entry


def is_cached(ticker: str, path: Path) -> bool:
    """Report whether a complete cache entry exists for a ticker.

    Args:
        ticker: Symbol to look for.
        path: Cache directory.

    Returns:
        ``True`` only if both the parquet file and its provenance sidecar are
        present. A parquet file without provenance is not a usable cache entry.
    """
    parquet_path, provenance_path = _paths(ticker, path)
    return parquet_path.exists() and provenance_path.exists()


class YFinanceSource:
    """A :class:`PriceSource` backed by Yahoo Finance via ``yfinance``.

    The only component that fetches PRICES over the network, reached from two
    commands - `fetch` for the research instrument and `run` for the execution
    instrument's recent bars; the venue adapter is the other network user in
    the project. No test may call the vendor, because a CI badge that goes red
    on a vendor rate limit is worse than no badge - and none does:
    ``tests/test_data.py`` puts a stand-in in the library's place, which works
    only because the import below is inside the method. That covers what this
    class asks for and what it does with the answer, not what the vendor sends.
    """

    name: str = "yfinance"

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily bars for ``ticker`` between ``start`` and ``end``.

        Requests split- and dividend-adjusted daily bars, normalises the
        vendor's column names to lowercase, and returns them sorted ascending.
        Performs no quality validation - that is :func:`validate_bars`, kept
        separate so the same checks apply to cached and freshly fetched data
        alike.

        Args:
            ticker: Yahoo Finance symbol, such as ``SPY``.
            start: First date to request, inclusive.
            end: Last date to request, inclusive. yfinance treats its ``end``
                as exclusive, so one day is added before the request.

        Returns:
            Bars indexed by ascending timestamp with the columns named in
            :data:`REQUIRED_COLUMNS`.

        Raises:
            DataQualityError: If the vendor returns no rows for the request.
        """
        import yfinance  # noqa: PLC0415 - imported here so no test can pull in the vendor

        raw = yfinance.download(
            ticker,
            start=start.isoformat(),
            end=(end + pd.Timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            actions=False,
        )
        if raw is None or raw.empty:
            raise DataQualityError(
                f"{ticker}: vendor returned no rows for {start} to {end}. "
                "Check the symbol and the dates before assuming an outage."
            )
        return _normalise(raw, ticker)

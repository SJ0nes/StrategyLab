"""Price data: fetching, quality validation, and a cache that carries provenance.

Data comes first because everything downstream inherits its errors. A wrong
price does not crash anything; it quietly invalidates every result computed on
top of it, and no test further down the pipeline will notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd

REQUIRED_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class DataQualityError(Exception):
    """Raised when a price frame violates a rule in :func:`validate_bars`.

    The message always names the offending dates. A count alone is useless: the
    operator needs to look at the bars in question and decide whether the
    vendor is wrong or the market genuinely did that.
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
        fetched_at: UTC timestamp of the fetch.
        rows: Number of bars in the frame.
        start: Date of the first bar.
        end: Date of the last bar.
        content_hash: Hex SHA-256 over the frame's values, so a silent vendor
            revision shows up as a changed hash rather than as a changed result
            with no explanation.
        path: Location of the parquet file, relative to the repository root.
    """

    ticker: str
    source: str
    fetched_at: datetime
    rows: int
    start: date
    end: date
    content_hash: str
    path: Path


class PriceSource(Protocol):
    """A source of daily OHLCV bars.

    Implementations differ only in where the bytes come from. Anything that
    needs to know which vendor produced a frame reads the frame's
    :class:`CacheEntry`, not the source object.
    """

    def fetch(self, ticker: str, start: date, end: date) -> pd.DataFrame:
        """Fetch daily bars for ``ticker`` between ``start`` and ``end``.

        Args:
            ticker: Vendor symbol to fetch.
            start: First date to request, inclusive.
            end: Last date to request, inclusive.

        Returns:
            Bars indexed by ascending timestamp, with the columns named in
            :data:`REQUIRED_COLUMNS`, lowercased.
        """
        ...


class YFinanceSource:
    """A :class:`PriceSource` backed by Yahoo Finance via ``yfinance``.

    The only component in the project that touches the network, and it is
    reached from exactly one command. No test may call it: the fetch path is to
    be tested against recorded fixtures instead, because a CI badge that goes
    red on a vendor rate limit is worse than no badge.
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
            end: Last date to request, inclusive.

        Returns:
            Bars indexed by ascending timestamp with the columns named in
            :data:`REQUIRED_COLUMNS`.

        Raises:
            DataQualityError: If the vendor returns no rows for the request.
        """
        raise NotImplementedError


def validate_bars(df: pd.DataFrame) -> None:
    """Check a price frame against every rule the rest of the system assumes.

    Raises on: missing trading days, zero volume, duplicate timestamps,
    negative or zero prices, and ``high < low``. Never interpolates silently -
    a filled-in bar is a fabricated observation, and fabricated observations
    are indistinguishable from real ones by the time they reach a result.

    Args:
        df: Bars indexed by timestamp, carrying the columns named in
            :data:`REQUIRED_COLUMNS`.

    Returns:
        None. This function is a gate, not a transform; it never modifies or
        returns the frame.

    Raises:
        DataQualityError: On any violation, naming the offending dates.
    """
    raise NotImplementedError


def cache_write(df: pd.DataFrame, ticker: str, path: Path) -> CacheEntry:
    """Write a price frame to parquet alongside its provenance.

    The provenance sidecar records fetch timestamp, source name, row count, and
    a content hash. Vendors revise history, so the hash is what makes a result
    reproducible: it pins the answer to this snapshot rather than to whatever
    the vendor serves next time.

    Args:
        df: Bars to write, already validated.
        ticker: Symbol the frame holds, used to name the file.
        path: Directory to write into, relative to the repository root. Created
            if absent.

    Returns:
        The provenance record for what was written.

    Raises:
        DataQualityError: If ``df`` is empty.
    """
    raise NotImplementedError


def cache_read(ticker: str, path: Path) -> tuple[pd.DataFrame, CacheEntry]:
    """Read a cached price frame and its provenance back.

    Args:
        ticker: Symbol to read, used to name the file.
        path: Directory to read from, relative to the repository root.

    Returns:
        The cached bars and the provenance recorded when they were written.

    Raises:
        FileNotFoundError: If no cache entry exists for ``ticker``.
        DataQualityError: If the frame's content hash no longer matches the one
            recorded in its provenance, which means the file was altered after
            it was written.
    """
    raise NotImplementedError

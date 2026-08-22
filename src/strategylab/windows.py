"""Which bars a component may see, and what looking at them costs.

``strategylab fetch`` caches 2005-2024 in one parquet file and nothing
partitions it. The in-sample/walk-forward split - this project's central
methodological claim - is therefore not a property of the data on disk. It is a
property of how components ask for bars, and this module is where they ask.

WHY THIS EXISTS RATHER THAN A PRIVATE HELPER PER COMPONENT. The search loop had
one, ``_in_sample_window``, and both ``docs/architecture.md`` and
``LIMITATIONS.md`` recorded the same warning about it: it guarded one consumer,
and "the right fix when the second consumer arrives is to lift that function
somewhere both can reach, not to copy it". The validator is that second
consumer. A copied slice is a slice that can drift, and the failure mode of a
drifted window guard is silent - a result computed on data the component was
not allowed to read, reported with the same confidence as a clean one.

THE TWO WINDOWS ARE NOT SYMMETRICAL, and the asymmetry is the whole design.
Reading the in-sample window is free and unlimited: that is what it is for.
Reading the walk-forward window costs a TOUCH, recorded permanently in
``protocol.yaml``, because the split degrades with use - the first candidate
tested against held-back data gets a genuinely clean test and the twentieth does
not. So :func:`in_sample_bars` is a plain slice and :func:`walk_forward_bars`
cannot be called without the counter moving.

THERE IS NO DRY RUN AND THERE MUST NEVER BE ONE. A mode that returns held-back
bars without incrementing would let someone look at the data for free, which is
exactly the thing the counter exists to make impossible. If a caller wants to
know what validation would do without paying for it, the answer is to run it
against a protocol whose windows point somewhere else - which is what the tests
do, and what development should do.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from strategylab.protocol import (
    DEFAULT_PROTOCOL_PATH,
    Protocol,
    Window,
    increment_walk_forward_touches,
)


class WindowError(Exception):
    """Bars could not be cut to a pre-registered window.

    Distinct from a component's own failures. This means the frame it was handed
    cannot be sliced at all, or holds nothing inside the window it asked for.
    """


def _cut(bars: pd.DataFrame, window: Window, name: str) -> pd.DataFrame:
    """Cut bars to one window, refusing anything that cannot be cut.

    Timestamps are normalised to midnight before comparison, so a bar carrying
    an intraday time on the window's last date is inside the window rather than
    silently dropped by a ``<=`` against that date at midnight.

    Args:
        bars: OHLCV bars indexed by a timezone-naive
            :class:`pandas.DatetimeIndex`. May span any range, including
            windows the caller is not entitled to.
        window: The pre-registered window to cut to.
        name: Which window this is, for the error messages. Callers see
            "in-sample" or "walk-forward" rather than a pair of dates.

    Returns:
        The rows dated within ``[window.start, window.end]`` inclusive, in their
        original order. Boolean-mask indexing materialises a new frame, so this
        is a copy rather than a view - and nothing here writes to either.

    Raises:
        WindowError: If ``bars`` is not indexed by a timezone-naive
            ``DatetimeIndex``, or if no bar falls inside the window.
    """
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise WindowError(
            f"bars is indexed by {type(bars.index).__name__}, not a DatetimeIndex, so the "
            f"{name} window cannot be cut from it. An integer index is the trap here: "
            "pandas would read those integers as nanoseconds since the epoch and date the "
            "whole frame to 1970 rather than refusing it"
        )
    if bars.index.tz is not None:
        raise WindowError(
            f"bars carries a timezone-aware index ({bars.index.tz}) and the {name} window "
            "is a plain date, so which bars fall inside it depends on a conversion nobody "
            "has chosen. Localise to naive before asking for a window"
        )

    stamps = bars.index.normalize()
    inside = (stamps >= pd.Timestamp(window.start)) & (stamps <= pd.Timestamp(window.end))
    selected = bars.loc[inside]
    if selected.empty:
        span = (
            f"the frame spans {stamps.min().date()} to {stamps.max().date()}"
            if len(stamps)
            else "the frame is empty"
        )
        raise WindowError(
            f"no bar falls inside the {name} window {window.start} to {window.end}; {span}"
        )
    return selected


def in_sample_bars(bars: pd.DataFrame, protocol: Protocol) -> pd.DataFrame:
    """Return the bars a search may read. Free, and repeatable.

    THE ONLY PLACE A SEARCH DECIDES WHICH BARS IT MAY SEE, and it decides from
    ``protocol.windows.in_sample`` alone - not from the frame it was handed, not
    from an argument a caller could widen.

    Args:
        bars: OHLCV bars indexed by a timezone-naive
            :class:`pandas.DatetimeIndex`, spanning any range. The frame may
            include the held-back window; what is read is decided here.
        protocol: The protocol in force, read for its in-sample window.

    Returns:
        The bars dated inside the in-sample window.

    Raises:
        WindowError: If the frame cannot be cut, or holds no bar in the window.
    """
    return _cut(bars, protocol.windows.in_sample, "in-sample")


def walk_forward_bars(
    bars: pd.DataFrame,
    protocol: Protocol,
    protocol_path: Path = DEFAULT_PROTOCOL_PATH,
) -> tuple[pd.DataFrame, int]:
    """Return the held-back bars, and charge the protocol a touch for them.

    THE COUNTER MOVES BEFORE THE BARS ARE HANDED OVER, and there is no way to
    get one without the other. That is the point: the split degrades with use,
    and a counter a caller can decline to increment measures nothing. Every
    return from this function is a permanent, recorded read of data that was
    supposed to be untouched.

    THE TOUCH IS CHARGED EVEN IF THE CALLER THROWS THE BARS AWAY. Looking is the
    cost, not concluding. A validation that ends in ``insufficient_data`` has
    still consumed some of the window's independence, and
    ``strategylab validate``'s contract says so explicitly.

    THE COUNTER IS INCREMENTED FIRST, so a failure while slicing still costs a
    touch. That is deliberate and it is the conservative order: over-counting a
    read that did not happen overstates the erosion, and understating it is the
    error that matters.

    Args:
        bars: OHLCV bars indexed by a timezone-naive
            :class:`pandas.DatetimeIndex`, spanning any range.
        protocol: The protocol in force, read for its walk-forward window.
        protocol_path: The protocol FILE to increment. Defaults to the
            repository's own. Tests and development must pass a temporary copy -
            not because the counter is a nuisance, but because a counter
            polluted by development noise cannot be read as evidence of
            anything, and this repository's whole argument is that its numbers
            can be.

    Returns:
        The bars dated inside the walk-forward window, and the touch count AFTER
        this read. The count is returned rather than left to be looked up so a
        caller can report it, and so the cost of the call is visible at the call
        site rather than buried in a file.

    Raises:
        WindowError: If the frame cannot be cut, or holds no bar in the window.
            The touch has already been charged by then.
        ProtocolError: Propagated from
            :func:`~strategylab.protocol.increment_walk_forward_touches` if the
            protocol file cannot be read or rewritten. No bars are returned and
            no touch is recorded.
    """
    touches = increment_walk_forward_touches(protocol_path)
    return _cut(bars, protocol.windows.walk_forward, "walk-forward"), touches

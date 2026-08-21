"""Strategy evaluation. Used by BOTH the backtester and the live worker.

Written once. Never duplicated elsewhere: two implementations of the same
strategy drift, and when they drift the backtest silently stops describing the
thing that is actually trading.
"""

from __future__ import annotations

import pandas as pd

from strategylab.core.types import Intent, StrategyDef


def evaluate(definition: StrategyDef, bars: pd.DataFrame, params: dict[str, int]) -> Intent:
    """Return the strategy's intent for the LATEST bar in ``bars``.

    Pure. Given a strategy definition, a window of bars, and resolved
    parameters, this returns the intent for the latest bar and nothing else. It
    has no knowledge of wall-clock time, of execution, or of where the bars came
    from, which is what allows the backtester and the live worker to share it.

    LOOKAHEAD CONSTRAINT: may read bars up to and including the latest bar, and
    NOTHING after it. The implementation ships with a test that corrupts every
    bar after ``t`` to absurd values and asserts the intent at ``t`` is
    unchanged. That test will be the single most important one in this project -
    lookahead bias does not crash, it just prints a better number. Neither the
    implementation nor that test exists yet; this function is a stub.

    Args:
        definition: The parsed strategy, whose ``entry`` and ``exit`` condition
            lists are each combined with AND.
        bars: OHLCV bars indexed by timestamp in ascending order. The last row
            is the bar being evaluated. Must hold enough history to satisfy the
            longest lookback in ``params``.
        params: Resolved values for the strategy's ``$placeholder`` parameters,
            keyed without the leading ``$``, in bars.

    Returns:
        The intent for the latest bar, carrying which conditions passed and
        which did not.

    Raises:
        ValueError: If ``bars`` is empty, is not sorted ascending, lacks a
            required column, holds fewer rows than the longest lookback, or if
            ``params`` omits a placeholder the definition references.
    """
    raise NotImplementedError

"""The backtest engine: ``core.evaluate`` run over a series, with costs applied.

The engine owns time and execution. It does not own strategy logic - that is
``core.strategy``, shared with the live worker - and it does not own cost
arithmetic, which is ``backtest.costs``. Keeping those three apart is what makes
each of them testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategylab.backtest.costs import CostModel, Fill
from strategylab.core.types import StrategyDef


@dataclass(frozen=True)
class BacktestResult:
    """Everything one backtest produced, including the rejected decisions.

    Attributes:
        equity: Account equity by timestamp, ascending, in account currency
            units.
        returns: Simple per-bar returns as fractions, aligned to ``equity``.
        fills: Every execution, in chronological order.
        decisions: One record per bar, INCLUDING bars on which nothing traded.
            The rejections are the part worth keeping: a strategy that did not
            trade for two years is telling you something, and a fills-only log
            cannot say what.
        sharpe: Annualised Sharpe ratio of ``returns``.
        max_drawdown_pct: Largest peak-to-trough decline of ``equity``, as a
            positive percentage.
        trade_count: Number of round-trip trades completed.
        params: The resolved parameters this run used, so a result carries the
            settings that produced it.
    """

    equity: pd.Series
    returns: pd.Series
    fills: tuple[Fill, ...]
    decisions: pd.DataFrame
    sharpe: float
    max_drawdown_pct: float
    trade_count: int
    params: dict[str, int]


def run_backtest(
    definition: StrategyDef,
    params: dict[str, int],
    bars: pd.DataFrame,
    costs: CostModel,
    starting_equity: float = 10_000.0,
) -> BacktestResult:
    """Simulate one parameter set over one window of bars.

    Runs :func:`strategylab.core.strategy.evaluate` bar by bar, applies
    :func:`strategylab.backtest.costs.apply_costs` to every fill, emits a
    decision record per bar including rejections, and computes the metrics.

    Deterministic: same definition, same params, same bars, same costs must
    produce a byte-identical result. This is a requirement on the
    implementation, and the test that proves it lands in the same commit as the
    implementation. Not yet written — this function is still a stub.

    Args:
        definition: The parsed strategy.
        params: Resolved values for the strategy's ``$placeholder`` parameters,
            keyed without the leading ``$``, in bars.
        bars: OHLCV bars indexed by ascending timestamp, already validated.
        costs: Broker parameters from the protocol.
        starting_equity: Opening account equity in account currency units.

    Returns:
        The result, including the per-bar decision log.

    Raises:
        ValueError: If ``bars`` is empty or unsorted, if ``starting_equity`` is
            not positive, or if ``params`` omits a placeholder the definition
            references.
    """
    raise NotImplementedError

"""The backtest engine: ``core.evaluate`` run over a series, with costs applied.

The engine owns time and execution. It does not own strategy logic - that is
``core.strategy``, shared with the live worker - and it does not own cost
arithmetic, which is ``backtest.costs``. Keeping those three apart is what makes
each of them testable in isolation.

TWO CONVENTIONS THIS MODULE FIXES, both of which change every number the project
will ever report, and neither of which is stated in the signature it implements.

**A signal from bar t's close is filled at bar t+1's OPEN.** Filling at the same
close would transact at a price that was only known once that bar had closed.
Nothing in ``evaluate`` prevents it - ``evaluate`` cannot see the future, but an
engine that acts instantly on a closing price is trading on information it did
not have in time. The cost is that :func:`apply_costs` prices against a MID and
an open is a traded price, so using it as the mid is an approximation; it is a
smaller error than the one it avoids, and it is stated rather than hidden. The
final bar's intent is recorded and never filled, because there is no next bar.

**The equity curve starts at the first EVALUABLE bar, not at bar zero.** A
strategy with a 200-bar warm-up does not trade for 200 bars, and carrying those
bars into the curve puts a column of zero returns into the Sharpe denominator.
That is a methodological choice, not an implementation detail, so it is made
here deliberately and in the open.

WHERE THE LOOKAHEAD GUARANTEE NOW LIVES. ``evaluate`` can only read the window it
is handed, so it cannot look ahead - which means the entire guarantee rests on
this module choosing that window correctly. ``bars.iloc[: i + 1]`` is the whole
of it. That single expression is what the project's central claim reduces to, and
``tests/test_engine.py`` attacks it directly rather than by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from strategylab.backtest.costs import CostModel, Fill, apply_costs
from strategylab.backtest.metrics import max_drawdown, sharpe
from strategylab.core.strategy import evaluate, required_history
from strategylab.core.types import Action, Side, StrategyDef
from strategylab.data.source import REQUIRED_COLUMNS


@dataclass(frozen=True)
class BacktestResult:
    """Everything one backtest produced, including the rejected decisions.

    Attributes:
        equity: Account equity by timestamp, ascending, in account currency
            units. Begins at the first evaluable bar, so its first value is
            ``starting_equity``.
        returns: Simple per-bar returns as fractions, covering
            ``equity.index[1:]``. One shorter than ``equity`` because a return
            is a difference between two bars and the first has no predecessor;
            padding it with a zero would put a fabricated observation into every
            metric computed from it.
        fills: Every execution, in chronological order.
        decisions: One record per bar from the first evaluable bar onward,
            INCLUDING bars on which nothing traded. The rejections are the part
            worth keeping: a strategy that did not trade for two years is
            telling you something, and a fills-only log cannot say what.
        sharpe: Annualised Sharpe ratio of ``returns``, or ``0.0`` when there
            are fewer than two returns to take a deviation of.
        max_drawdown_pct: Largest peak-to-trough decline of ``equity``, as a
            positive percentage.
        trade_count: Number of round-trip trades completed. A position opened
            and never closed is not counted, because its result is not yet
            known.
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


def _validate(bars: pd.DataFrame, starting_equity: float) -> None:
    """Check the engine's own inputs before any bar is stepped.

    Args:
        bars: The candidate window of OHLCV bars.
        starting_equity: Opening account equity in account currency units.

    Raises:
        ValueError: If ``bars`` is empty, unsorted, carries duplicate
            timestamps or lacks a required column, or if ``starting_equity`` is
            not positive and finite.
    """
    if bars.empty:
        raise ValueError("bars is empty; there is nothing to backtest")
    missing = [column for column in REQUIRED_COLUMNS if column not in bars.columns]
    if missing:
        raise ValueError(f"bars lacks required column(s) {missing}; got {list(bars.columns)}")
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars is not sorted ascending by its index")
    if bars.index.has_duplicates:
        raise ValueError("bars carries duplicate timestamps; the bar order is ambiguous")
    if not (starting_equity > 0) or starting_equity == float("inf"):
        raise ValueError(f"starting_equity must be positive and finite, got {starting_equity}")


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

    Deterministic: same definition, same params, same bars, same costs produce
    an identical result. Nothing here consults a clock, a random source, or any
    state outside its arguments, and ``tests/test_engine.py`` asserts it.

    The bar loop is causal in the order a real day happens. On each bar the
    engine executes whatever the PREVIOUS bar decided, at this bar's open; then
    evaluates this bar's close to decide what the next bar will execute; then
    marks the account to this bar's close. A signal is therefore never filled at
    the price that produced it. See the module docstring.

    Position sizing is ``fixed.position_size_pct`` of equity at the moment of
    entry, in fractional units. Cash charges are deducted from cash, so a size
    of 100 percent plus a commission would overdraw - which is why the strategy
    config carries 95.

    Args:
        definition: The parsed strategy.
        params: Resolved values for the strategy's ``$placeholder`` parameters,
            keyed without the leading ``$``, in bars.
        bars: OHLCV bars indexed by ascending timestamp, already validated.
        costs: Broker parameters from the protocol, every rate a FRACTION.
        starting_equity: Opening account equity in account currency units.

    Returns:
        The result, including the per-bar decision log.

    Raises:
        ValueError: If ``bars`` is empty, unsorted, carries duplicate
            timestamps or lacks a required column; if ``starting_equity`` is not
            positive and finite; if ``bars`` holds fewer rows than the
            strategy's warm-up needs; or if ``params`` omits a placeholder the
            definition references.
    """
    _validate(bars, starting_equity)

    warmup = required_history(definition, params)
    if len(bars) < warmup:
        raise ValueError(
            f"bars holds {len(bars)} rows but this strategy's warm-up needs {warmup}; "
            "there is no bar it could be evaluated on"
        )

    opens = bars["open"].to_numpy(dtype="float64")
    closes = bars["close"].to_numpy(dtype="float64")

    cash = starting_equity
    units = 0.0
    fills: list[Fill] = []
    records: list[dict[str, Any]] = []
    equity_values: list[float] = []
    equity_index: list[Any] = []
    trade_count = 0
    pending: Action | None = None
    pending_size_pct = 0.0

    # The first evaluable bar is at index warmup - 1: that is the earliest bar
    # with `warmup` rows up to and including it.
    for i in range(warmup - 1, len(bars)):
        # 1. Execute what the PREVIOUS bar decided, at this bar's open. This is
        #    the whole of the no-same-bar-fill rule.
        if pending is Action.ENTER_LONG and units == 0.0:
            # Two calls, deliberately. The quantity depends on the executed
            # price, and the executed price depends on the mid and the rates but
            # NOT on the quantity - so the first call is how this module learns
            # the price without reimplementing the fill formula. Recomputing
            # `price * (spread/2 + slippage)` here would put a second copy of
            # that arithmetic in the codebase, which is the one thing ADR 0002
            # says must not happen. The quantity=0 call charges nothing.
            priced = apply_costs(opens[i], Side.BUY, costs, quantity=0.0)
            quantity = (cash * pending_size_pct / 100.0) / priced.executed_price
            fill = apply_costs(opens[i], Side.BUY, costs, quantity=quantity)
            cash -= fill.executed_price * quantity + fill.costs.commission + fill.costs.fx
            units = quantity
            fills.append(fill)
        elif pending is Action.EXIT_LONG and units > 0.0:
            fill = apply_costs(opens[i], Side.SELL, costs, quantity=units)
            cash += fill.executed_price * units - fill.costs.commission - fill.costs.fx
            units = 0.0
            trade_count += 1
            fills.append(fill)

        # 2. Decide, from this bar's close, what the NEXT bar will execute.
        intent = evaluate(definition, bars.iloc[: i + 1], params)
        pending = intent.action
        pending_size_pct = intent.size_pct

        # 3. Mark to this bar's close.
        equity = cash + units * closes[i]
        equity_values.append(equity)
        equity_index.append(bars.index[i])
        records.append(
            {
                "action": intent.action.value,
                "requested_size_pct": intent.size_pct,
                "conditions_met": " AND ".join(intent.conditions_met),
                "conditions_failed": " AND ".join(intent.conditions_failed),
                "position_units": units,
                "cash": cash,
                "equity": equity,
            }
        )

    index = pd.Index(equity_index, name=bars.index.name)
    equity_series = pd.Series(equity_values, index=index, dtype="float64")
    returns = equity_series.pct_change().iloc[1:]

    return BacktestResult(
        equity=equity_series,
        returns=returns,
        fills=tuple(fills),
        decisions=pd.DataFrame(records, index=index),
        # Fewer than two returns has no deviation to divide by. That is a window
        # too short to judge, not a Sharpe of zero, but the field has to hold a
        # number - the honest signal is trade_count and the length of returns.
        sharpe=sharpe(returns) if len(returns) >= 2 else 0.0,
        max_drawdown_pct=max_drawdown(equity_series),
        trade_count=trade_count,
        params=dict(params),
    )

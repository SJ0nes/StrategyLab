"""Tests for the backtest engine.

THE LOOKAHEAD TEST THAT HAS TEETH IS IN THIS FILE, not in `test_strategy.py`.

`evaluate` receives a window ending at the bar it evaluates, so it cannot read
the future and the test named in the project's rules cannot fail. The engine is
different: it holds the WHOLE frame and chooses what to hand over. That choice —
`bars.iloc[: i + 1]` — is the entire lookahead guarantee, and it is falsifiable.

So `test_corrupting_the_future_does_not_change_any_earlier_decision` corrupts
every bar after `k` to an absurd value and hands the engine the corrupted frame
INTACT. Nothing is sliced away before the call. An engine that peeked — by one
bar, by a `shift(-1)`, by a statistic over the whole column — produces different
decisions before `k`, and the test says so. That is the first test in this
project to actually carry the claim the whole thing rests on.

Expected values are hand-derived in the comments. The equity path was derived
twice: the first attempt disagreed with the implementation, and the error was in
the derivation, which is the reason the table below is spelled out bar by bar.
"""

from __future__ import annotations

import pandas as pd
import pytest

from strategylab.backtest.costs import CostModel
from strategylab.backtest.engine import run_backtest
from strategylab.core.types import Condition, IndicatorRef, StrategyDef

# closes 13..10 down, up to 14, back down to 10. On a 2-bar and 3-bar SMA this
# crosses ABOVE at i=5 and BELOW at i=9, and nowhere else.
CLOSES = [13.0, 12.0, 11.0, 10.0, 11.0, 12.0, 13.0, 14.0, 13.0, 12.0, 11.0, 10.0]
WOBBLE = [0.3, -0.7, 1.1]
PARAMS = {"fast": 2, "slow": 3}
WARMUP = 4  # longest lookback 3, plus one bar for the crossing operators

# Zero rates, so the hand-derived arithmetic is the strategy's and not the
# broker's. A separate test puts real costs back and checks they bite.
FREE = CostModel(
    spread_frac=0.0,
    slippage_frac=0.0,
    commission_frac=0.0,
    commission_min_flat=0.0,
    fx_conversion_frac=0.0,
)


def frame(closes: list[float]) -> pd.DataFrame:
    """Build an OHLCV frame whose open is a NON-constant offset of the close.

    The offset varies bar to bar so that an engine filling at the wrong price -
    the close instead of the next open, say - produces different numbers. A
    constant offset would cancel and hide it.

    Args:
        closes: Close price per bar, in order.

    Returns:
        The frame, indexed by consecutive business days.
    """
    opens = [c + WOBBLE[i % 3] for i, c in enumerate(closes)]
    index = pd.bdate_range("2020-01-01", periods=len(closes), name="date")
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(opens[i], c) + 2.0 for i, c in enumerate(closes)],
            "low": [min(opens[i], c) - 2.0 for i, c in enumerate(closes)],
            "close": closes,
            "volume": [1_000_000 + i for i in range(len(closes))],
        },
        index=index,
    )


def crossover() -> StrategyDef:
    """Build the fast-over-slow crossover used throughout this file.

    Returns:
        A definition entering on a fast-above-slow cross and exiting on the
        reverse, sized at 95 percent of equity.
    """
    fast = IndicatorRef(indicator="sma", period_param="fast")
    slow = IndicatorRef(indicator="sma", period_param="slow")
    return StrategyDef(
        name="crossover",
        version=1,
        description="Test fixture.",
        entry=(Condition(left=fast, operator="crosses_above", right=slow),),
        exit=(
            Condition(
                left=IndicatorRef(indicator="sma", period_param="fast"),
                operator="crosses_below",
                right=IndicatorRef(indicator="sma", period_param="slow"),
            ),
        ),
        search={},
        fixed={"position_size_pct": 95.0},
    )


# --------------------------------------------------------------------------
# THE LOOKAHEAD TEST. Read the module docstring for why this one can fail and
# the one in test_strategy.py cannot.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("k", [4, 5, 6, 7, 8, 9, 10])
def test_corrupting_the_future_does_not_change_any_earlier_decision(k: int) -> None:
    # The engine is handed the corrupted frame WHOLE. Nothing is sliced off
    # before the call, so if it reads bar k+1 while deciding bar k - by a
    # shift(-1), by a column-wide statistic, by an off-by-one in the slice -
    # every decision from the warm-up to k changes and this fails.
    clean = frame(CLOSES)
    corrupted = clean.copy()
    corrupted.iloc[k + 1 :] = 1e9

    reference = run_backtest(crossover(), PARAMS, clean, FREE)
    peeking = run_backtest(crossover(), PARAMS, corrupted, FREE)

    rows = k + 1 - (WARMUP - 1)  # decisions recorded from the first evaluable bar
    pd.testing.assert_frame_equal(
        peeking.decisions.iloc[:rows], reference.decisions.iloc[:rows], check_exact=True
    )
    pd.testing.assert_series_equal(
        peeking.equity.iloc[:rows], reference.equity.iloc[:rows], check_exact=True
    )


def test_truncating_the_future_does_not_change_any_earlier_decision() -> None:
    # The same property from the other side. A backtest stopped early must agree
    # bar for bar with the prefix of a longer one - which is what makes a
    # walk-forward fold mean anything, since a fold IS a truncated run.
    full = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)
    for stop in range(WARMUP, len(CLOSES) + 1):
        short = run_backtest(crossover(), PARAMS, frame(CLOSES).iloc[:stop], FREE)
        rows = len(short.decisions)
        pd.testing.assert_frame_equal(short.decisions, full.decisions.iloc[:rows], check_exact=True)
        pd.testing.assert_series_equal(short.equity, full.equity.iloc[:rows], check_exact=True)


def test_a_signal_is_never_filled_at_the_bar_that_produced_it() -> None:
    # The cross is at i=5. Filling at i=5's close (12.0) would be trading on a
    # price known only once that bar had closed. The fill must be at i=6's OPEN,
    # which is 13.0 + 0.3 = 13.3 - a worse price, which is the point.
    result = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)
    assert result.fills[0].mid_price == pytest.approx(13.3, rel=1e-12)
    assert result.fills[0].mid_price != pytest.approx(12.0, rel=1e-9)  # i=5 close
    assert result.fills[0].mid_price != pytest.approx(13.0, rel=1e-9)  # i=6 close


# --------------------------------------------------------------------------
# Determinism, which the contract requires of this function by name.
# --------------------------------------------------------------------------


def test_the_same_inputs_produce_the_same_result() -> None:
    bars = frame(CLOSES)
    first = run_backtest(crossover(), PARAMS, bars, FREE)
    second = run_backtest(crossover(), PARAMS, bars, FREE)
    pd.testing.assert_series_equal(first.equity, second.equity, check_exact=True)
    pd.testing.assert_series_equal(first.returns, second.returns, check_exact=True)
    pd.testing.assert_frame_equal(first.decisions, second.decisions, check_exact=True)
    assert first.fills == second.fills
    assert first.sharpe == second.sharpe
    assert first.max_drawdown_pct == second.max_drawdown_pct
    assert first.trade_count == second.trade_count
    assert first.params == second.params


def test_the_engine_does_not_modify_the_bars_it_is_given() -> None:
    bars = frame(CLOSES)
    before = bars.copy(deep=True)
    run_backtest(crossover(), PARAMS, bars, FREE)
    pd.testing.assert_frame_equal(bars, before, check_exact=True)


# --------------------------------------------------------------------------
# The trade itself, against a bar-by-bar hand derivation.
# --------------------------------------------------------------------------


def test_the_round_trip_matches_the_hand_derived_arithmetic() -> None:
    # Cross above at i=5, so BUY at i=6's open = 13 + 0.3 = 13.3.
    #   quantity = 10000 * 95% / 13.3 = 9500 / 13.3 = 714.285714...
    #   cash     = 10000 - 714.285714 * 13.3 = 500 exactly
    # Cross below at i=9, so SELL at i=10's open = 11 - 0.7 = 10.3.
    #   final equity = 500 + 714.285714 * 10.3 = 7857.142857...
    result = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)

    assert len(result.fills) == 2
    buy, sell = result.fills
    assert buy.mid_price == pytest.approx(13.3, rel=1e-12)
    assert buy.quantity == pytest.approx(9500.0 / 13.3, rel=1e-12)
    assert sell.mid_price == pytest.approx(10.3, rel=1e-12)
    assert sell.quantity == pytest.approx(buy.quantity, rel=1e-12)

    assert result.equity.iloc[-1] == pytest.approx(500.0 + (9500.0 / 13.3) * 10.3, rel=1e-12)
    assert result.trade_count == 1


def test_the_equity_curve_is_the_hand_derived_path() -> None:
    # bar  close  position       equity
    #   3   10.0  flat           10000
    #   4   11.0  flat           10000
    #   5   12.0  flat           10000     <- cross above decided here
    #   6   13.0  long 714.2857   9785.714286   = 500 + 714.2857*13
    #   7   14.0  long 714.2857  10500.0        = 500 + 714.2857*14   <- peak
    #   8   13.0  long 714.2857   9785.714286
    #   9   12.0  long 714.2857   9071.428571   <- cross below decided here
    #  10   11.0  flat            7857.142857   = 500 + 714.2857*10.3, sold at open
    #  11   10.0  flat            7857.142857
    quantity = 9500.0 / 13.3
    expected = [
        10000.0,
        10000.0,
        10000.0,
        500.0 + quantity * 13.0,
        500.0 + quantity * 14.0,
        500.0 + quantity * 13.0,
        500.0 + quantity * 12.0,
        500.0 + quantity * 10.3,
        500.0 + quantity * 10.3,
    ]
    result = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)
    assert list(result.equity) == pytest.approx(expected, rel=1e-12)


def test_the_drawdown_is_measured_from_the_peak_the_run_reached() -> None:
    # Peak 10500 at bar 7, trough 7857.142857 at bar 10.
    #   (10500 - 7857.142857) / 10500 * 100 = 25.170068027210885
    # Measured from the 10000 it started at, the answer would be 21.43 - which
    # is the flattering number, and is what a drawdown against the opening
    # balance rather than the running peak reports.
    result = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)
    assert result.max_drawdown_pct == pytest.approx(25.170068027210885, rel=1e-12)
    assert result.max_drawdown_pct != pytest.approx(21.428571, rel=1e-4)


# --------------------------------------------------------------------------
# Costs, warm-up, and the shape of the result.
# --------------------------------------------------------------------------


def test_costs_are_actually_charged_and_they_reduce_the_result() -> None:
    # This strategy loses money on this series, so costs make the loss bigger.
    # Asserting a DIRECTION rather than a value, because the value belongs to
    # test_costs.py - what this pins is that apply_costs is on the path at all.
    bars = frame(CLOSES)
    free = run_backtest(crossover(), PARAMS, bars, FREE)
    charged = run_backtest(
        crossover(),
        PARAMS,
        bars,
        CostModel(spread_frac=0.0006, slippage_frac=0.001, commission_frac=0.001),
    )
    assert charged.equity.iloc[-1] < free.equity.iloc[-1]
    # And the buy must be filled ABOVE the mid, the sell BELOW it: a buyer
    # crosses the spread upward. Getting this backwards pays the trader to trade.
    assert charged.fills[0].executed_price > charged.fills[0].mid_price
    assert charged.fills[1].executed_price < charged.fills[1].mid_price


def test_the_curve_starts_at_the_first_evaluable_bar() -> None:
    # Not at bar zero. The three warm-up bars would otherwise contribute zero
    # returns to the Sharpe denominator without the strategy having been able
    # to act on anything.
    bars = frame(CLOSES)
    result = run_backtest(crossover(), PARAMS, bars, FREE)
    assert result.equity.index[0] == bars.index[WARMUP - 1]
    assert len(result.equity) == len(bars) - (WARMUP - 1)
    assert result.equity.iloc[0] == 10_000.0


def test_returns_are_one_shorter_than_equity_and_are_differences() -> None:
    result = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)
    assert len(result.returns) == len(result.equity) - 1
    assert list(result.returns.index) == list(result.equity.index[1:])
    # First real move: 10000 -> 10000 is zero; the bar the position opens on is
    # 10000 -> 9785.714286, a fall of 214.285714/10000.
    assert result.returns.iloc[0] == pytest.approx(0.0, abs=1e-15)
    assert result.returns.iloc[2] == pytest.approx((9500.0 / 13.3 * 13.0 + 500.0) / 10000.0 - 1.0)


def test_a_decision_is_recorded_on_every_bar_including_the_quiet_ones() -> None:
    result = run_backtest(crossover(), PARAMS, frame(CLOSES), FREE)
    assert len(result.decisions) == len(result.equity)
    # Two of the nine bars carry a signal; the other seven are holds, and they
    # are in the log rather than absent from it.
    assert list(result.decisions["action"]).count("hold") == 7
    assert list(result.decisions["action"]).count("enter_long") == 1
    assert list(result.decisions["action"]).count("exit_long") == 1


def test_an_open_position_is_not_counted_as_a_completed_trade() -> None:
    # Stop the series before the exit cross at i=9. The position is still open,
    # its result is not yet known, and counting it would report a round trip
    # that never closed.
    result = run_backtest(crossover(), PARAMS, frame(CLOSES).iloc[:9], FREE)
    assert len(result.fills) == 1
    assert result.trade_count == 0


def test_params_are_carried_on_the_result_and_are_a_copy() -> None:
    params = dict(PARAMS)
    result = run_backtest(crossover(), params, frame(CLOSES), FREE)
    assert result.params == PARAMS
    params["fast"] = 99
    assert result.params == PARAMS, "the result aliased the caller's dict"


# --------------------------------------------------------------------------
# Refusals.
# --------------------------------------------------------------------------


def test_an_empty_frame_is_refused() -> None:
    with pytest.raises(ValueError, match="bars is empty"):
        run_backtest(crossover(), PARAMS, frame([]), FREE)


def test_a_frame_shorter_than_the_warmup_is_refused() -> None:
    with pytest.raises(ValueError, match="warm-up needs 4"):
        run_backtest(crossover(), PARAMS, frame(CLOSES).iloc[:3], FREE)


def test_an_unsorted_frame_is_refused() -> None:
    with pytest.raises(ValueError, match="not sorted ascending"):
        run_backtest(crossover(), PARAMS, frame(CLOSES).iloc[::-1], FREE)


def test_duplicate_timestamps_are_refused() -> None:
    bars = frame(CLOSES)
    bars.index = pd.DatetimeIndex([bars.index[0]] * len(bars), name="date")
    with pytest.raises(ValueError, match="duplicate timestamps"):
        run_backtest(crossover(), PARAMS, bars, FREE)


@pytest.mark.parametrize("column", ["open", "high", "low", "close", "volume"])
def test_a_missing_column_is_refused(column: str) -> None:
    with pytest.raises(ValueError, match="lacks required column"):
        run_backtest(crossover(), PARAMS, frame(CLOSES).drop(columns=[column]), FREE)


@pytest.mark.parametrize("equity", [0.0, -100.0, float("inf")])
def test_a_starting_equity_that_is_not_positive_and_finite_is_refused(equity: float) -> None:
    with pytest.raises(ValueError, match="starting_equity must be positive and finite"):
        run_backtest(crossover(), PARAMS, frame(CLOSES), FREE, starting_equity=equity)


def test_a_missing_parameter_is_refused() -> None:
    with pytest.raises(ValueError, match="params omits 'slow'"):
        run_backtest(crossover(), {"fast": 2}, frame(CLOSES), FREE)

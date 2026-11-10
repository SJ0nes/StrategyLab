"""Performance metrics.

Kept in their own module with their own tests. A Sharpe ratio computed slightly
wrong is the kind of error that survives review, because the number it produces
is always plausible.

The conventions this module fixes are recorded in
``docs/decisions/0005-sharpe-conventions.md``, outside the code that applies
them - an implementation and its own test agreeing with each other is not a
specification. Two of them are worth repeating here, because both are places
implementations silently differ:

- The standard deviation is the SAMPLE one, dividing by ``n - 1``. The
  population form divides by ``n`` and reports a Sharpe that is too high by
  ``sqrt(n / (n - 1))`` - about 0.2% on a year of daily bars, and much more on a
  short fold, which is exactly where a marginal candidate gets decided.
- Returns are SIMPLE per-period fractions, not log returns and not percentages.
  A series of percentages scales BOTH the mean and the standard deviation by a
  hundred, so the ratio is unchanged and the Sharpe is identical. That is what
  makes the mistake dangerous: it is invisible here and surfaces only in the
  equity curve, as a return a hundred times too large. Nothing in this module
  can detect it; the caller has to be right.

  (An earlier version of this paragraph said the numerator went a hundred times
  too small and the denominator a hundred times too large - which would be a
  ten-thousand-fold error - and then drew the correct conclusion from it anyway.
  A hundredfold error inside the warning about hundredfold errors. Recorded
  rather than quietly deleted, because it is the exact failure this project
  keeps writing rules about.)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

TRADING_DAYS_PER_YEAR: int = 252


def sharpe(returns: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualised Sharpe ratio of a return series, assuming a zero risk-free rate.

    Computed as ``mean(returns) / stdev(returns) * sqrt(periods_per_year)``,
    using the sample standard deviation. The risk-free rate is taken as zero
    and not configurable: a non-zero rate would have to be a time series over
    the same window to be correct, and a single scalar for it is a worse
    approximation than omitting it.

    Args:
        returns: Simple per-period returns as fractions, so ``0.01`` is one
            percent. Not log returns, and not percentages.
        periods_per_year: Periods per year used to annualise, ``252`` for daily
            bars.

    Returns:
        The annualised Sharpe ratio. Returns ``0.0`` when the series has zero
        variance, since a constant series has no risk-adjusted return to speak
        of rather than an infinite one.

    Raises:
        ValueError: If ``returns`` holds fewer than two observations, if any
            observation is not finite, or if ``periods_per_year`` is not
            positive.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    if len(returns) < 2:
        raise ValueError(
            f"sharpe needs at least two observations to have a standard deviation, "
            f"got {len(returns)}"
        )
    # Checked rather than allowed to propagate. A NaN here yields a NaN Sharpe,
    # and `nan >= min_sharpe` is False, so the candidate fails validation for a
    # data reason while looking as though it failed on merit.
    if not bool(returns.notna().all()) or not bool(returns.abs().ne(float("inf")).all()):
        raise ValueError("returns holds a non-finite value; a NaN Sharpe fails silently")

    deviation = float(returns.std(ddof=1))
    if deviation == 0.0:
        return 0.0
    return float(returns.mean()) / deviation * math.sqrt(periods_per_year)


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline in an equity curve.

    Measured against the RUNNING peak, not against the starting value: a curve
    that doubles and then halves has lost half of what it had, and reporting
    that as no drawdown because it ended where it began is the flattering error.

    Args:
        equity: Account equity by timestamp, ascending. Values are levels, not
            returns, and must be positive.

    Returns:
        The largest peak-to-trough decline as a POSITIVE percentage, so a fall
        from 100 to 75 returns ``25.0``. A curve that never declines returns
        ``0.0``.

    Raises:
        ValueError: If ``equity`` is empty, or holds a value that is not
            positive and finite.
    """
    if equity.empty:
        raise ValueError("equity is empty; there is no drawdown to measure")
    if not bool(equity.notna().all()):
        raise ValueError("equity holds a NaN; the drawdown would silently skip it")
    if not bool((equity > 0).all()):
        raise ValueError(
            "equity holds a value that is not positive; a drawdown is measured as a "
            "fraction of the running peak and cannot be taken against zero or less"
        )
    if not bool(equity.abs().ne(float("inf")).all()):
        raise ValueError("equity holds an infinite value")

    peak = equity.cummax()
    return float(((peak - equity) / peak).max()) * 100.0


@dataclass(frozen=True)
class BuyAndHold:
    """What simply owning the instrument did over a run of bars.

    Attributes:
        sharpe: Annualised Sharpe ratio of the close-to-close returns, a
            dimensionless ratio, by the same conventions as :func:`sharpe`.
        max_drawdown_pct: Largest peak-to-trough decline of the closes, as a
            positive PERCENTAGE, by the same conventions as
            :func:`max_drawdown`.
        start: Date of the first close held.
        end: Date of the last close held.
        bars: How many closes that is. One more than the number of returns.
    """

    sharpe: float
    max_drawdown_pct: float
    start: date
    end: date
    bars: int


def buy_and_hold(closes: pd.Series) -> BuyAndHold:
    """Measure holding the instrument from the first close to the last.

    The benchmark a long-only strategy has to beat to be worth running: buy at
    the first close, do nothing, and mark to every close after it.

    TWO THINGS FLATTER IT, both on purpose. It pays NO COSTS - not the one entry
    a real holder would pay, and nothing on the way out. And it is ALWAYS FULLY
    INVESTED, where the strategy keeps a cash buffer. Both make the benchmark a
    little better than a person could achieve, so a strategy compared against it
    is held to a bar set slightly too high rather than too low.

    WHAT THE CLOSES ARE DECIDES WHAT THIS MEASURES. On back-adjusted closes,
    which is what this project caches, the returns include reinvested
    dividends, so this is a total-return benchmark. On raw closes it would be
    price-only.

    Args:
        closes: Closing prices by timestamp, ascending, in any currency unit.
            Must be positive and finite. Give it exactly the bars the strategy
            was measured on, or the comparison is between two different periods.

    Returns:
        The Sharpe ratio and drawdown of holding, with the span they cover.

    Raises:
        ValueError: If ``closes`` holds fewer than three values - two returns
            are the least a standard deviation can be taken of - or a value
            that is not positive and finite.
    """
    if len(closes) < 3:
        raise ValueError(
            f"buy_and_hold needs at least three closes to have two returns, got {len(closes)}"
        )
    drawdown = max_drawdown(closes)
    returns = closes.pct_change().iloc[1:]
    return BuyAndHold(
        sharpe=sharpe(returns),
        max_drawdown_pct=drawdown,
        start=pd.Timestamp(closes.index[0]).date(),
        end=pd.Timestamp(closes.index[-1]).date(),
        bars=len(closes),
    )

"""Performance metrics.

Kept in their own module with their own tests. A Sharpe ratio computed slightly
wrong is the kind of error that survives review, because the number it produces
is always plausible.
"""

from __future__ import annotations

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
        ValueError: If ``returns`` holds fewer than two observations, or
            ``periods_per_year`` is not positive.
    """
    raise NotImplementedError


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline in an equity curve.

    Args:
        equity: Account equity by timestamp, ascending. Values are levels, not
            returns, and must be positive.

    Returns:
        The largest peak-to-trough decline as a POSITIVE percentage, so a fall
        from 100 to 75 returns ``25.0``. A curve that never declines returns
        ``0.0``.

    Raises:
        ValueError: If ``equity`` is empty or holds a non-positive value.
    """
    raise NotImplementedError

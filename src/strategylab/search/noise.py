"""The noise ceiling: what the same search achieves on data with no edge in it.

The best of ninety combinations on real data must be compared against the best
of ninety on noise. Without that number a winning Sharpe is not evidence of
anything, because the expected maximum across ninety draws of noise is well
above zero - and a search that reports only its winner is reporting that
maximum without saying so.
"""

from __future__ import annotations

import pandas as pd

from strategylab.backtest.costs import CostModel
from strategylab.core.types import StrategyDef


def noise_ceiling(
    definition: StrategyDef,
    search_spec: dict[str, dict[str, float]],
    bars: pd.DataFrame,
    costs: CostModel,
    n_trials: int,
    seed: int = 0,
) -> pd.Series:
    """Run the entire grid against surrogate series with no edge, and record the best.

    Re-runs the ENTIRE grid against block-resampled returns ``n_trials`` times,
    recording the best Sharpe achieved on each surrogate series. Block
    resampling rather than plain shuffling, so that volatility clustering
    survives and the surrogate is a fair opponent for a trend strategy; an
    i.i.d. shuffle destroys the autocorrelation that trend following trades and
    would set the bar far too low.

    Reported in every search result.

    Args:
        definition: The parsed strategy, searched exactly as on real data.
        search_spec: The ranges to walk, identical to the real search. Using a
            smaller grid here would understate the ceiling, since the ceiling
            is a property of how many combinations were tried.
        bars: The real bars, used as the source of the return distribution and
            for the calendar the surrogates are laid on.
        costs: Broker parameters, identical to the real search.
        n_trials: Number of surrogate series to generate. Must be positive.
        seed: Seed for the resampler, so the ceiling is reproducible. A ceiling
            that moves between runs cannot be compared against anything.

    Returns:
        The distribution of best-in-grid Sharpe across the trials, one value per
        trial, indexed by trial number.

    Raises:
        ValueError: If ``n_trials`` is not positive, or ``bars`` holds too few
            rows to block-resample.
    """
    raise NotImplementedError

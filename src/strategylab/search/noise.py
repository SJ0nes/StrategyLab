"""The noise ceiling: what the same search achieves without real history to help.

The best of ninety combinations on real data must be compared against the best
of ninety on something that is not the real series. Without that number a
winning Sharpe is not evidence of anything, because the expected maximum across
ninety draws is well above zero - and a search that reports only its winner is
reporting that maximum without saying so.

"NOISE" IS THE PROJECT'S WORD FOR IT AND IT IS NOT ACCURATE. The surrogates
:mod:`strategylab.search.surrogate` builds are block-resampled real history,
with blocks as long as the grid's longest lookback, so each is a verbatim
stretch of the real series at a rescaled price level. What this ceiling measures
is a REORDERING OF THE SAME MARKET'S REGIMES - a harder and narrower test than
noise, and not the one ``CLAUDE.md`` describes. The name is kept because it is
the name the protocol and the roadmap use; every place that reports a figure
says what it actually is. ``docs/decisions/0008`` argues out why no block length
avoids the trade.

WHAT THIS COSTS, because it decides how the component is used. One ceiling trial
runs the ENTIRE grid, so the work is ``n_trials`` times a full search. The
committed grid takes about four minutes, which puts a hundred trials near seven
hours. That is not a defect to be optimised away here: the ceiling is a property
of how many combinations were tried, so the surrogate grid cannot be shrunk
without understating it, and the trial count is what buys precision in the upper
tail. The number is the caller's to choose and
:func:`~strategylab.search.noise.trials_note` states plainly what a given
choice buys.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from strategylab.backtest.costs import CostModel
from strategylab.backtest.engine import run_backtest
from strategylab.core.types import StrategyDef
from strategylab.search.grid import expand_grid
from strategylab.search.surrogate import block_length_for, block_resample

#: Trials below which the upper percentiles of the ceiling are too noisy to
#: compare a winner against.
#:
#: The ceiling is used by reading its high quantiles - "is the winner above the
#: 95th percentile of what noise achieves". With twenty trials the 95th
#: percentile IS essentially the maximum of twenty draws, which moves a long way
#: between runs; the comparison then measures the resampler's luck rather than
#: the strategy. A hundred is where the tail settles enough to quote.
#:
#: Not enforced. A short run is a legitimate way to see the machinery work, and
#: refusing it would push people towards not running it at all. It is reported
#: instead, on every run and in the artifact.
RELIABLE_TRIALS = 100


def trials_note(n_trials: int) -> str:
    """Say plainly what a given trial count buys, for printing beside a ceiling.

    Args:
        n_trials: The number of surrogate series that were, or will be, run.

    Returns:
        One sentence. Below :data:`RELIABLE_TRIALS` it says the tail is unstable
        and must not be quoted as a threshold; at or above it says what the
        distribution supports.
    """
    if n_trials < RELIABLE_TRIALS:
        return (
            f"{n_trials} trials is below the {RELIABLE_TRIALS} at which the upper tail "
            "settles. Read this ceiling as an order of magnitude, not as a threshold: "
            "its 95th percentile is close to the maximum of a small sample and will move "
            "between runs."
        )
    return (
        f"{n_trials} trials supports reading the upper percentiles as a threshold, though "
        "the ceiling remains an estimate from one price series and one grid."
    )


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

    THE BLOCK LENGTH IS THE LONGEST LOOKBACK THE GRID REACHES, chosen by
    :func:`~strategylab.search.surrogate.block_length_for` and recorded in
    ``docs/decisions/0008``. Blocks shorter than the lookback destroy the
    structure the strategy trades, which is the same failure as shuffling
    arriving more quietly.

    A CELL THAT FAILS ON A SURROGATE IS SKIPPED, NOT SCORED ZERO. The engine
    raises for a cell whose warm-up exceeds the window and for an account driven
    to zero; scoring either as a Sharpe of zero would drag the best-in-grid
    figure around by an arbitrary constant. The best is taken over the cells
    that produced a number. A trial where NO cell produced one contributes
    ``NaN`` rather than being dropped, so the returned Series always holds
    ``n_trials`` values and a caller counting them is not silently misled.

    Deterministic given ``seed``: the same inputs produce the same ceiling. The
    generator is created once and drawn from in trial order, so trial ``k``
    depends on every trial before it - the ceiling is reproducible as a whole
    and individual trials are not independently addressable.

    Reported in every search result.

    Args:
        definition: The parsed strategy, searched exactly as on real data.
        search_spec: The ranges to walk, identical to the real search. Using a
            smaller grid here would understate the ceiling, since the ceiling
            is a property of how many combinations were tried.
        bars: The real bars, used as the source of the return distribution and
            for the calendar the surrogates are laid on. MUST be the in-sample
            window and nothing more: a ceiling computed partly on held-back data
            has read that data, and no later validation of it is clean.
        costs: Broker parameters, identical to the real search.
        n_trials: Number of surrogate series to generate. Must be positive. See
            :data:`RELIABLE_TRIALS` for what the number buys.
        seed: Seed for the resampler, so the ceiling is reproducible. A ceiling
            that moves between runs cannot be compared against anything.

    Returns:
        The distribution of best-in-grid Sharpe across the trials, one value per
        trial, indexed by trial number from zero. Named ``best_in_grid_sharpe``,
        so a frame built from several of these says what its columns are.

    Raises:
        ValueError: If ``n_trials`` is not positive, or ``bars`` holds too few
            rows to block-resample - which includes a block longer than the
            available returns, since the block length is derived from the grid
            rather than from the data and a short window cannot serve a long
            lookback. Propagated unchanged from
            :func:`~strategylab.search.grid.expand_grid` for a malformed spec
            and from
            :func:`~strategylab.search.surrogate.block_length_for` for one whose
            lookbacks cannot be read.
    """
    if n_trials <= 0:
        raise ValueError(f"n_trials is {n_trials}; a ceiling needs at least one surrogate")

    grid = expand_grid(search_spec)
    block_bars = block_length_for(search_spec)
    generator = np.random.default_rng(seed)

    best: list[float] = []
    for _ in range(n_trials):
        surrogate = block_resample(bars, block_bars, generator)
        scores: list[float] = []
        for cell in grid:
            try:
                result = run_backtest(definition, cell, surrogate, costs)
            except ValueError:
                # Cell-level failure on this surrogate. Skipped rather than
                # scored: a zero here is not a result, it is the absence of one,
                # and it would pull the maximum around by an arbitrary constant.
                continue
            scores.append(result.sharpe)
        best.append(max(scores) if scores else math.nan)

    return pd.Series(best, index=pd.RangeIndex(n_trials), name="best_in_grid_sharpe")

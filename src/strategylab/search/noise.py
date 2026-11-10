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
from dataclasses import dataclass

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
#: A short run is not refused. It is a legitimate way to see the machinery work,
#: and refusing it would push people towards not running it at all. What a short
#: run cannot do is produce a verdict: :func:`evaluate_gate` will call a
#: candidate neither passed nor failed on fewer SCOREABLE trials than this.
RELIABLE_TRIALS = 100

#: The share of scoreable surrogates a candidate must beat to clear the gate, as
#: a PERCENTAGE. Ninety-five means the candidate's own Sharpe has to be above
#: the best the whole grid achieved on at least ninety-five surrogates in every
#: hundred.
#:
#: IN CODE, NOT IN ``protocol.yaml``, and that is a decision rather than an
#: oversight. The protocol is written once and this rule arrived after it;
#: ``docs/decisions/0012`` records when it was fixed, what had been seen by
#: then, and why this number.
GATE_PERCENTILE = 95

#: No cell survived selection, so there is nothing to judge.
GATE_NO_CANDIDATE = "no_candidate"
#: The search ran without ``--trials``.
GATE_NOT_COMPUTED = "not_computed"
#: A ceiling exists but rests on fewer than :data:`RELIABLE_TRIALS` scoreable
#: surrogates, so it supports no verdict either way.
GATE_UNDETERMINED = "undetermined"
#: The candidate is inside what the same grid finds on resampled history.
GATE_FAILED = "failed"
#: The candidate beat at least :data:`GATE_PERCENTILE` percent of them.
GATE_PASSED = "passed"

#: Every status :func:`evaluate_gate` returns. Closed, so a reader meeting any
#: other string in an artifact can refuse it rather than guess which side of the
#: gate it means.
GATE_STATUSES = frozenset(
    {GATE_NO_CANDIDATE, GATE_NOT_COMPUTED, GATE_UNDETERMINED, GATE_FAILED, GATE_PASSED}
)


@dataclass(frozen=True)
class NoiseGate:
    """Whether a search's candidate cleared the noise ceiling, and on what count.

    The verdict is a comparison of COUNTS, not of a Sharpe against a percentile:
    how many surrogates the candidate beat, against how many it had to. A
    percentile of a hundred values is an interpolation, and two implementations
    of it disagree at the boundary; a count does not.

    Attributes:
        status: One of :data:`GATE_STATUSES`.
        candidate_sharpe: The candidate's own annualised Sharpe, a dimensionless
            ratio, or ``None`` when there is no candidate. NOT its plateau
            score: the surrogates record the best single cell's Sharpe, so the
            candidate is held to the same kind of number - and since it was
            chosen for its neighbourhood rather than for being the best cell,
            that makes the comparison harder for it, not easier.
        scoreable_trials: How many surrogates produced a number to compare
            against. Zero when no ceiling was computed.
        beaten: How many of those the candidate's Sharpe is strictly above, or
            ``None`` when there is no candidate or no scoreable surrogate. A tie
            is not a win.
        required: The smallest ``beaten`` that reaches :data:`GATE_PERCENTILE`
            percent of ``scoreable_trials``, or ``None`` under the same
            conditions as ``beaten``.
    """

    status: str
    candidate_sharpe: float | None
    scoreable_trials: int
    beaten: int | None
    required: int | None


def required_to_pass(scoreable_trials: int) -> int:
    """Return the smallest count of beaten surrogates that clears the gate.

    Args:
        scoreable_trials: How many surrogates produced a number.

    Returns:
        The smallest whole ``b`` with ``b * 100 >= GATE_PERCENTILE *
        scoreable_trials``. Integer arithmetic throughout, so the boundary does
        not depend on how a float rounds: 95 of 100, 96 of 101, 190 of 200.
    """
    return -(-GATE_PERCENTILE * scoreable_trials // 100)


def evaluate_gate(ceiling: pd.Series | None, candidate_sharpe: float | None) -> NoiseGate:
    """Decide whether a candidate cleared the noise ceiling.

    The rule: the candidate's own Sharpe must be strictly above the best-in-grid
    Sharpe of at least :data:`GATE_PERCENTILE` percent of the scoreable
    surrogates, and there must be at least :data:`RELIABLE_TRIALS` of those.
    Fewer than that gives no verdict in either direction - a candidate that
    beats nineteen of twenty has not passed, and one that beats three of twenty
    has not failed.

    This judges the SEARCH, not a cell. It is applied once, to the cell
    selection chose, and nothing walks on to the next-best cell when it fails:
    doing so would pick a cell for its own Sharpe, which is what ranking by
    plateau exists to avoid.

    Args:
        ceiling: Best-in-grid Sharpe per surrogate, as
            :func:`noise_ceiling` returns it, or ``None`` when none was
            computed. A value that is not finite is a surrogate on which no
            cell scored, and is not counted.
        candidate_sharpe: The candidate's own annualised Sharpe, a dimensionless
            ratio, or ``None`` when the search nominated nobody. A value that is
            not finite beats nothing.

    Returns:
        The verdict and the counts behind it. See :class:`NoiseGate`.
    """
    finite = (
        pd.Series(dtype="float64") if ceiling is None else ceiling[np.isfinite(ceiling.to_numpy())]
    )
    scoreable = len(finite)
    if candidate_sharpe is None:
        return NoiseGate(GATE_NO_CANDIDATE, None, scoreable, None, None)
    if ceiling is None:
        return NoiseGate(GATE_NOT_COMPUTED, candidate_sharpe, 0, None, None)
    if scoreable == 0:
        return NoiseGate(GATE_UNDETERMINED, candidate_sharpe, 0, None, None)

    beaten = int((finite < candidate_sharpe).sum()) if math.isfinite(candidate_sharpe) else 0
    required = required_to_pass(scoreable)
    if scoreable < RELIABLE_TRIALS:
        status = GATE_UNDETERMINED
    else:
        status = GATE_PASSED if beaten >= required else GATE_FAILED
    return NoiseGate(status, candidate_sharpe, scoreable, beaten, required)


def describe_gate(gate: NoiseGate) -> str:
    """Say what a gate verdict means, with the counts it rests on.

    Printed by the search, recorded in its artifact, and quoted by the reader
    that refuses to validate on it, so the three cannot drift apart.

    Args:
        gate: The verdict.

    Returns:
        One or two sentences. Every status short of a pass says the candidate
        cannot be validated, because that is the consequence a reader needs.
    """
    if gate.status == GATE_NO_CANDIDATE:
        return (
            "No candidate survived selection, so there is nothing for the noise ceiling to judge."
        )
    if gate.status == GATE_NOT_COMPUTED:
        return (
            "No noise ceiling was computed. A candidate never compared against what the same "
            "grid finds on resampled history is not evidence of an edge, and it cannot be "
            f"validated; re-run the search with --trials {RELIABLE_TRIALS} or more."
        )
    if gate.status == GATE_UNDETERMINED:
        beat = "none" if gate.beaten is None else str(gate.beaten)
        return (
            f"The ceiling rests on {gate.scoreable_trials} scoreable surrogate(s), below the "
            f"{RELIABLE_TRIALS} at which its upper tail settles. The candidate beat {beat} of "
            "them, which is not yet a verdict in either direction, and it cannot be validated "
            f"until a search with at least {RELIABLE_TRIALS} scoreable trials has run."
        )
    counts = (
        f"The candidate's Sharpe of {gate.candidate_sharpe:.3f} beat {gate.beaten} of "
        f"{gate.scoreable_trials} surrogates"
    )
    if gate.status == GATE_PASSED:
        return f"{counts}, at or above the {gate.required} the gate requires."
    return (
        f"{counts} and needed {gate.required}. It is INSIDE what the same grid finds on "
        "resampled history, which is what a search of this size finds on data with no edge "
        "in it, and it cannot be validated."
    )


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

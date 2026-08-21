"""Walk-forward validation against the held-back window.

This is the only component permitted to read the walk-forward window, and every
run increments the access counter. Repeated validation against overlapping
windows erodes independence: the tenth candidate tested against the same six
years is not being tested as cleanly as the first, and the counter is what makes
that erosion visible instead of forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from strategylab.core.types import StrategyDef
from strategylab.protocol import Protocol


class Outcome(Enum):
    """The three possible answers from a validation run.

    ``INSUFFICIENT_DATA`` is not a soft failure. It means get more history or
    trade more often; ``FAIL`` means the strategy is bad. Reporting the first
    as the second throws away a candidate that was never tested, and reporting
    the second as the first invites another look at something already refuted.
    """

    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class FoldResult:
    """What one walk-forward fold produced.

    Attributes:
        index: Zero-based position of the fold in the walk-forward window.
        start: First date of the fold, inclusive.
        end: Last date of the fold, inclusive.
        sharpe: Annualised Sharpe achieved in the fold.
        max_drawdown_pct: Largest peak-to-trough decline in the fold, as a
            positive percentage.
        trade_count: Round-trip trades completed in the fold.
        outcome: This fold's own verdict. Advisory only - the pass decision is
            made on the pooled result, not by counting folds.
    """

    index: int
    start: date
    end: date
    sharpe: float
    max_drawdown_pct: float
    trade_count: int
    outcome: Outcome


@dataclass(frozen=True)
class ValidationResult:
    """The verdict on one candidate, and the evidence behind it.

    Attributes:
        outcome: The pooled verdict.
        folds: Per-fold results in chronological order. Secondary to the pooled
            decision, but the shape across folds is what shows whether a pass
            came from one lucky year.
        pooled_sharpe: Sharpe over the folds' returns pooled together.
        pooled_max_drawdown_pct: Largest peak-to-trough decline across the
            pooled equity curve, as a positive percentage.
        total_trades: Round-trip trades across all folds.
        threshold: The Sharpe the candidate had to clear, being
            ``protocol.validation.effective_sharpe_bar`` — the pre-registered
            floor plus the additive pass margin. Recorded on the result so the
            bar is legible beside the number that met or missed it.
        reason: Plain-English statement of why this outcome was reached,
            including which criterion bound.
        walk_forward_touches: Value of the access counter AFTER this run.
    """

    outcome: Outcome
    folds: tuple[FoldResult, ...]
    pooled_sharpe: float
    pooled_max_drawdown_pct: float
    total_trades: int
    threshold: float
    reason: str
    walk_forward_touches: int


def walk_forward(
    definition: StrategyDef,
    params: dict[str, int],
    protocol: Protocol,
) -> ValidationResult:
    """Test one already-chosen candidate against the held-back window.

    Rolls through the walk-forward window in consecutive folds of
    ``protocol.windows.fold_months`` months EACH — a fold length, not a fold
    count. With the window 2019-01-01 to 2024-12-31 and ``fold_months: 12``,
    that is six folds of twelve months, not twelve folds.

    Results are POOLED across folds for the pass decision; per-fold consistency
    is reported but secondary, because a per-fold vote on six folds is a noisier
    statistic than one computed on all the trades together.

    To pass, pooled Sharpe must clear ``protocol.validation.min_sharpe`` by the
    ADDITIVE margin ``protocol.validation.pass_margin`` — that is, it must reach
    ``protocol.validation.effective_sharpe_bar``, which with the defaults is
    1.0 + 0.20 = 1.20. A marginal pass is a fail. Pooled drawdown must also stay
    within ``protocol.validation.max_drawdown_ceiling_pct``.

    The two trade floors are distinct and are not interchangeable. A fold with
    fewer than ``protocol.validation.min_trades_per_fold`` trades reports
    ``INSUFFICIENT_DATA`` for THAT FOLD. If the total across all folds is below
    ``protocol.validation.min_trades_pooled``, the WHOLE VALIDATION reports
    ``INSUFFICIENT_DATA`` rather than ``FAIL``, because the pooled figure is
    what the pass decision rests on.

    If folds routinely come back ``INSUFFICIENT_DATA``, that is a finding about
    whether this validation design can test this strategy at all, and belongs
    in ``LIMITATIONS.md``. It is never a reason to lower a threshold after
    seeing the counts.

    Increments the protocol's access counter on every run, including runs that
    end in ``INSUFFICIENT_DATA``. Looking at the held-back data costs a touch
    whether or not the look produced a verdict.

    Args:
        definition: The parsed strategy.
        params: The single parameter combination chosen by the search. Walk
            forward tests one candidate; passing a grid here would be searching
            the held-back window, which is the thing the split exists to
            prevent.
        protocol: The loaded protocol, supplying the window, the fold length,
            and every pass criterion including the Sharpe floor. Nothing about
            the verdict is passed in at run time, because a threshold chosen
            after seeing the result is not a threshold.

    Returns:
        The verdict and the per-fold evidence behind it.

    Raises:
        ValueError: If ``params`` omits a placeholder the definition
            references, or the walk-forward window is shorter than one fold.
    """
    raise NotImplementedError

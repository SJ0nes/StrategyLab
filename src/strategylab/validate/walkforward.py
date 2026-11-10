"""Walk-forward validation against the held-back window.

This is the only component permitted to read the walk-forward window, and every
run increments the access counter. Repeated validation against overlapping
windows erodes independence: the tenth candidate tested against the same six
years is not being tested as cleanly as the first, and the counter is what makes
that erosion visible instead of forgotten.

FOUR DECISIONS THIS MODULE FIXES. None of them is stated by the signature it
implements, and each changes the number the verdict rests on.

**ONE CONTINUOUS BACKTEST, SLICED INTO FOLDS - not one backtest per fold.** The
folds exist to show whether a pass came from one lucky year; they are a
measurement device, not a trading rule. Running each separately would force the
position flat at every boundary and reset equity six times, inventing exits
nobody would have taken and flattening the one drawdown that matters - the
peak-to-trough across the whole held-back period. So the engine runs once over
the window and each fold is the slice of that run falling inside its dates. The
pooled figures are therefore the run's own, not a re-aggregation of six sets of
per-fold figures, and cannot disagree with them.

**THE WARM-UP COMES FROM THE IN-SAMPLE WINDOW, AND THAT IS NOT A LEAK.** A
hundred-and-fifty-bar trend filter needs a hundred and fifty bars before it says
anything. Handing the engine only the held-back window would leave the first
seven months of it untradeable, so the LAST bars of the in-sample window are
prepended - taken through :func:`~strategylab.windows.in_sample_bars`, which is
free and repeatable. The direction of travel is what makes this safe: the rule
is that the SEARCH must never see the FUTURE. Using past bars to warm up a
future evaluation is what a live trader does on their first morning.

WHETHER THOSE BARS ARE ADJACENT TO THE WINDOW IS A PROPERTY OF THE PROTOCOL, NOT
OF THIS CODE. All that is enforced here is ORDER - the in-sample window must end
before the held-back window starts. A protocol leaving years between them would
warm up on stale bars, silently, and report the result at the same confidence as
a clean one. The committed protocol is adjacent (2018-12-31, then 2019-01-01),
so this is true of the file rather than guaranteed by the function.

AT MOST ``warmup - 1`` bars are prepended - exactly that many whenever the
in-sample window can supply them, which is the case that matters and the case
the committed protocol is in. Then the first bar the engine can evaluate is the
first bar of the held-back window and the equity curve covers the window and
nothing else. When the in-sample window holds FEWER than ``warmup - 1`` bars the
prefix is short, the curve starts inside the held-back window, and ``bars_used``
is smaller than the window's bar count - which is what that field is for.

The position starts FLAT at the window's first bar, which understates a strategy
that would already have been long on the window's first morning. That is a
stated approximation, not an oversight: carrying a position across the boundary
would mean an entry decided on in-sample data appearing as a held-back result.

THE RUN ALSO ENDS WHEREVER IT ENDS, and nothing liquidates. A position still
open on the window's last bar pays no exit cost, is absent from
``total_trades`` - which counts round trips COMPLETED - and yet its
mark-to-market is inside ``pooled_sharpe`` and ``pooled_max_drawdown_pct``,
because the engine marks equity to every close. ``LIMITATIONS.md`` records what
that came to on the committed candidate, with the provenance of the measurement;
no figure for it is repeated here, because a per-run count in a docstring is a
number with no command beside it. It is not corrected: forcing a liquidation at
the window edge would invent an exit nobody took, and it would change what
:func:`~strategylab.backtest.engine.run_backtest` means for every other caller.

**A TRAILING REMAINDER IS ABSORBED INTO THE FINAL FOLD, never dropped.** With
the committed protocol - 2019-01-01 to 2024-12-31, ``fold_months: 12`` - there
is no remainder and there are six folds of twelve months. When a window does not
divide evenly, the last fold runs long rather than a short seventh fold being
formed or the tail being discarded. A short fold's Sharpe is not comparable to
the others; discarding held-back data a touch has already been charged for is
worse still. Every fold's real dates are on its :class:`FoldResult`, so a long
one is visible rather than implied.

**A POOLED SHARPE THAT IS NOT A FINITE NUMBER IS ``INSUFFICIENT_DATA``, NEVER A
PASS.** ``nan < 1.20`` is False, so a naive threshold test passes a candidate
whose Sharpe could not be computed. That is the one failure mode here capable of
producing a false PASS, so it is checked before the thresholds rather than left
to the comparison.
"""

from __future__ import annotations

import calendar
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from strategylab.backtest.engine import run_backtest
from strategylab.backtest.metrics import max_drawdown, sharpe
from strategylab.core.strategy import required_history
from strategylab.core.types import StrategyDef
from strategylab.protocol import DEFAULT_PROTOCOL_PATH, Protocol, Validation, Window
from strategylab.search.loop import DEFAULT_RESULTS_DIR, STARTING_EQUITY
from strategylab.validate.candidate import CandidateRef
from strategylab.windows import in_sample_bars, walk_forward_bars

#: Schema version of a validation artifact. Bumped when the fields change, so a
#: reader meeting an unfamiliar version can say so rather than guess.
#: :func:`write_validation_result` enumerates the current fields.
#:
#: ``1`` was the first shape. ``2`` adds ``config_content_hash``, copied from the
#: candidate, so a verdict names what its config said and not only where it was.
VALIDATION_SCHEMA_VERSION = 2


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

    Every figure here is measured on the fold's own slice of one continuous
    run: the Sharpe is of the returns dated inside the fold, and the drawdown
    is against the running peak WITHIN the fold, not the peak of the whole
    window. A fold entered while already under water therefore reports its own
    decline, not the inherited one.

    Attributes:
        index: Zero-based position of the fold in the walk-forward window.
        start: First date of the fold, inclusive.
        end: Last date of the fold, inclusive. The final fold's end is the
            window's end, so it may run longer than ``fold_months``.
        sharpe: Annualised Sharpe achieved in the fold, a dimensionless ratio.
            ``NaN`` when the fold holds fewer than two returns, which is not
            measurable rather than zero.
        max_drawdown_pct: Largest peak-to-trough decline in the fold, as a
            positive percentage. ``NaN`` when the fold holds no bar at all.
        trade_count: Round-trip trades COMPLETED in the fold. A position opened
            in one fold and closed in the next counts once, in the fold that
            closed it, so the folds' counts sum to ``total_trades``.
        outcome: This fold's own verdict, against the same criteria as the
            pooled decision but with ``min_trades_per_fold`` as its trade
            floor. Advisory only - the pass decision is made on the pooled
            result, not by counting folds.
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
            came from one lucky year. Empty when the run ended before a
            backtest could be attempted.
        pooled_sharpe: Sharpe over the folds' returns pooled together, a
            dimensionless ratio. Being one continuous run, this is the Sharpe
            of every return in the window. ``NaN`` when fewer than two returns
            exist.
        pooled_max_drawdown_pct: Largest peak-to-trough decline across the
            pooled equity curve, as a positive percentage. ``NaN`` when no
            curve was produced.
        total_trades: Round-trip trades across all folds.
        bars_used: Bars of the held-back window the equity curve covers. Equal
            to the window's bar count when a full warm-up was available, and
            smaller when the in-sample window could not supply one.
        warmup_bars: The strategy's warm-up at these parameters, in bars. The
            number of in-sample bars prepended is one less than this, or fewer
            when the in-sample window could not supply that many.
        threshold: The Sharpe the candidate had to clear, being
            ``protocol.validation.effective_sharpe_bar`` — the pre-registered
            floor plus the additive pass margin. Recorded on the result so the
            bar is legible beside the number that met or missed it.
        reason: Plain-English statement of why this outcome was reached,
            including which criterion bound. When two criteria failed, both are
            named: a candidate that missed the Sharpe bar AND breached the
            drawdown ceiling has two problems, and reporting one hides the
            other from whoever tries to fix it.
        walk_forward_touches: Value of the access counter AFTER this run.
    """

    outcome: Outcome
    folds: tuple[FoldResult, ...]
    pooled_sharpe: float
    pooled_max_drawdown_pct: float
    total_trades: int
    bars_used: int
    warmup_bars: int
    threshold: float
    reason: str
    walk_forward_touches: int


def _add_months(anchor: date, months: int) -> date:
    """Advance a date by whole months, clamping to the target month's length.

    ``31 January`` plus one month is ``28 February``, or ``29`` in a leap year.
    Every fold boundary is computed from the window's start rather than from
    the previous boundary, so a clamped month cannot drag the rest of the
    sequence with it.

    Args:
        anchor: The date to advance from.
        months: Whole months to add. Every call site passes zero or more; a
            negative value is NOT rejected and moves the date backwards, which
            floor division handles correctly but which nothing here needs.

    Returns:
        The advanced date.
    """
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))


def _fold_bounds(window: Window, fold_months: int) -> tuple[tuple[date, date], ...]:
    """Split a window into consecutive folds of ``fold_months`` months each.

    Folds are cut by DATE, not by bar count, so a fold spanning a quiet stretch
    of the calendar holds fewer bars than its neighbours. That is the honest
    split: twelve months is twelve months whether or not the exchange was busy.

    The trailing remainder is absorbed into the final fold, which therefore
    ends on the window's last date and may run longer than ``fold_months``. See
    the module docstring for why it is neither dropped nor made a short fold of
    its own.

    Args:
        window: The held-back window, inclusive of both endpoints.
        fold_months: Length of each fold in whole months, at least one.

    Returns:
        One ``(start, end)`` pair per fold, in chronological order, together
        covering the window exactly and without overlap.

    Raises:
        ValueError: If ``fold_months`` is below one, or the window is shorter
            than a single fold - there is no walk-forward to do, and silently
            returning one short fold would report a verdict on a period the
            protocol never asked for.
    """
    if fold_months < 1:
        raise ValueError(f"fold_months must be at least 1, got {fold_months}")

    count = 0
    while _add_months(window.start, (count + 1) * fold_months) - timedelta(days=1) <= window.end:
        count += 1
    if count == 0:
        raise ValueError(
            f"the walk-forward window {window.start} to {window.end} is shorter than one "
            f"fold of {fold_months} month(s); there is no complete fold to validate over"
        )

    bounds: list[tuple[date, date]] = []
    for position in range(count):
        start = _add_months(window.start, position * fold_months)
        end = (
            window.end
            if position == count - 1
            else _add_months(window.start, (position + 1) * fold_months) - timedelta(days=1)
        )
        bounds.append((start, end))
    return tuple(bounds)


def _inside(index: pd.Index, start: date, end: date) -> np.ndarray:
    """Mask the rows of a timestamp index falling within a fold's dates.

    Timestamps are normalised to midnight before comparison, for the same
    reason :func:`strategylab.windows._cut` does it: a bar carrying an intraday
    time on the fold's last date belongs to that fold.

    Args:
        index: Bar timestamps. Annotated as :class:`pandas.Index` because that
            is what ``Series.index`` is typed as; it must in fact be a
            :class:`pandas.DatetimeIndex` or convertible to one.
        start: First date of the fold, inclusive.
        end: Last date of the fold, inclusive.

    Returns:
        A boolean array, one entry per row of ``index``.

    Raises:
        Exception: Whatever :class:`pandas.DatetimeIndex` raises for an index it
            cannot convert - a ``TypeError`` today. Not caught and not
            translated: both call sites pass the index of a frame the engine has
            already validated, so reaching this is an internal fault rather than
            a caller's error.
    """
    stamps = pd.DatetimeIndex(index).normalize()
    inside = (stamps >= pd.Timestamp(start)) & (stamps <= pd.Timestamp(end))
    return np.asarray(inside, dtype=bool)


def _fold_outcome(
    fold_sharpe: float, drawdown_pct: float, trades: int, criteria: Validation
) -> Outcome:
    """Judge one fold against the same criteria as the pooled decision.

    The only difference from the pooled judgement is the trade floor:
    ``min_trades_per_fold`` here, ``min_trades_pooled`` there. A non-finite
    Sharpe is ``INSUFFICIENT_DATA`` rather than being compared, because
    ``nan < bar`` is False and would read as a pass.

    Args:
        fold_sharpe: The fold's annualised Sharpe, a dimensionless ratio.
        drawdown_pct: The fold's largest decline, a positive percentage.
        trades: Round-trip trades completed in the fold.
        criteria: The protocol's pass criteria.

    Returns:
        This fold's advisory verdict.
    """
    if trades < criteria.min_trades_per_fold or not math.isfinite(fold_sharpe):
        return Outcome.INSUFFICIENT_DATA
    if math.isfinite(drawdown_pct) and drawdown_pct > criteria.max_drawdown_ceiling_pct:
        return Outcome.FAIL
    if fold_sharpe < criteria.effective_sharpe_bar:
        return Outcome.FAIL
    return Outcome.PASS


def _verdict(
    pooled_sharpe: float, drawdown_pct: float, trades: int, criteria: Validation
) -> tuple[Outcome, str]:
    """Reach the pooled verdict and say in words which criterion bound.

    THE ORDER IS THE DECISION. The trade floor is tested first, because a
    candidate that barely traded was not refuted by the held-back window - it
    was never tested by it, and calling that a FAIL discards a strategy on
    evidence nobody has. Only once there are enough trades to judge do the
    drawdown ceiling and the Sharpe bar apply, and both are reported when both
    were missed.

    Args:
        pooled_sharpe: Sharpe over every return in the window, a dimensionless
            ratio.
        drawdown_pct: Largest peak-to-trough decline of the pooled equity
            curve, a positive percentage.
        trades: Round-trip trades completed across the whole window.
        criteria: The protocol's pass criteria, every threshold pre-registered.

    Returns:
        The outcome and the sentence explaining it.
    """
    bar = criteria.effective_sharpe_bar
    if trades < criteria.min_trades_pooled:
        return Outcome.INSUFFICIENT_DATA, (
            f"{trades} round-trip trade(s) pooled across all folds, below the floor of "
            f"{criteria.min_trades_pooled}. The pooled figure carries the pass decision, so "
            "this is insufficient_data and not a fail: the candidate was not tested, not "
            "refuted. Get more history or trade more often - never lower the floor after "
            "seeing the count"
        )
    if not math.isfinite(pooled_sharpe):
        return Outcome.INSUFFICIENT_DATA, (
            "the pooled Sharpe is not a finite number, so there is nothing to compare "
            f"against the bar of {bar:.2f}. This is reported as insufficient_data rather "
            "than passed, because a comparison against a non-finite value silently succeeds"
        )

    missed: list[str] = []
    if math.isfinite(drawdown_pct) and drawdown_pct > criteria.max_drawdown_ceiling_pct:
        missed.append(
            f"pooled drawdown {drawdown_pct:.2f}% breaches the ceiling of "
            f"{criteria.max_drawdown_ceiling_pct:.2f}%"
        )
    if pooled_sharpe < bar:
        missed.append(
            f"pooled Sharpe {pooled_sharpe:.3f} is below the bar of {bar:.2f} "
            f"({criteria.min_sharpe:.2f} floor + {criteria.pass_margin:.2f} margin)"
        )
    if missed:
        return Outcome.FAIL, "; ".join(missed)

    return Outcome.PASS, (
        f"pooled Sharpe {pooled_sharpe:.3f} clears the bar of {bar:.2f} "
        f"({criteria.min_sharpe:.2f} floor + {criteria.pass_margin:.2f} margin) over {trades} "
        f"trade(s), and pooled drawdown {drawdown_pct:.2f}% stays inside the ceiling of "
        f"{criteria.max_drawdown_ceiling_pct:.2f}%"
    )


def _completed_exits(decisions: pd.DataFrame) -> np.ndarray:
    """Mark the bars on which a round trip was completed.

    The engine's decision log carries ``position_units`` as the state AFTER
    that bar's open was executed, so a round trip closed exactly where the
    holding falls from positive to zero. Counting those transitions rather than
    the fills is what lets a trade be dated: a
    :class:`~strategylab.backtest.costs.Fill` carries no timestamp, and the
    decision log is indexed by one.

    The first row's predecessor is taken as flat, which is what the engine
    starts at.

    Args:
        decisions: The engine's per-bar decision log.

    Returns:
        A boolean array, one entry per row, true where a round trip completed.
        Its sum is the engine's own ``trade_count``.
    """
    units = decisions["position_units"].to_numpy(dtype="float64")
    previous = np.concatenate(([0.0], units[:-1]))
    return np.asarray((units == 0.0) & (previous > 0.0), dtype=bool)


def _no_run(touches: int, warmup: int, criteria: Validation, reason: str) -> ValidationResult:
    """Build the result for a run that could not reach a backtest.

    The touch has already been charged by the time this is called, and it is
    reported here rather than swallowed: looking is the cost, not concluding.

    ``bars_used`` is zero because it counts the bars an equity curve COVERS, and
    there is no curve. How many bars were available is in ``reason``, where it
    belongs: it is an explanation, not a measurement.

    Args:
        touches: The access counter after this run.
        warmup: The strategy's warm-up in bars.
        criteria: The protocol's pass criteria, for the threshold.
        reason: Why no backtest was attempted.

    Returns:
        An ``INSUFFICIENT_DATA`` result with no folds and no metrics.
    """
    return ValidationResult(
        outcome=Outcome.INSUFFICIENT_DATA,
        folds=(),
        pooled_sharpe=float("nan"),
        pooled_max_drawdown_pct=float("nan"),
        total_trades=0,
        bars_used=0,
        warmup_bars=warmup,
        threshold=criteria.effective_sharpe_bar,
        reason=reason,
        walk_forward_touches=touches,
    )


def walk_forward(
    definition: StrategyDef,
    params: dict[str, int],
    bars: pd.DataFrame,
    protocol: Protocol,
    *,
    protocol_path: Path = DEFAULT_PROTOCOL_PATH,
    starting_equity: float = STARTING_EQUITY,
) -> ValidationResult:
    """Test one already-chosen candidate against the held-back window.

    Rolls through the walk-forward window in consecutive folds of
    ``protocol.windows.fold_months`` months EACH — a fold length, not a fold
    count. With the window 2019-01-01 to 2024-12-31 and ``fold_months: 12``,
    that is six folds of twelve months, not twelve folds. A window that does
    not divide evenly gives its remainder to the LAST fold; see the module
    docstring.

    Results are POOLED across folds for the pass decision; per-fold consistency
    is reported but secondary, because a per-fold vote on six folds is a noisier
    statistic than one computed on all the trades together. The pooling is not
    an aggregation step: the engine runs ONCE over the whole window and each
    fold is a slice of that run, so the pooled figures and the fold figures
    cannot disagree.

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

    THE CHEAP CHECKS RUN BEFORE THE TOUCH IS CHARGED, and the ordering is
    deliberate. A missing placeholder and a window shorter than one fold are
    properties of the arguments, knowable without reading a single held-back
    bar, so they raise for free. Everything after the read costs a touch,
    including a frame that turns out to hold too few bars to evaluate - by then
    the window has been looked at.

    Args:
        definition: The parsed strategy, with its ``$placeholder`` periods
            unresolved.
        params: The single parameter combination chosen by the search, keyed
            without the leading ``$``, in BARS. Walk forward tests one
            candidate; passing a grid here would be searching the held-back
            window, which is the thing the split exists to prevent.
        bars: OHLCV bars indexed by an ascending, timezone-naive
            :class:`pandas.DatetimeIndex`. MUST span both windows: the
            held-back window is what is judged and the in-sample window
            supplies the warm-up. What is read is decided here, from the
            protocol, and never from what was passed.
        protocol: The loaded protocol, supplying the window, the fold length,
            the cost model — every rate a FRACTION — and every pass criterion
            including the Sharpe floor. Nothing about the verdict is passed in
            at run time, because a threshold chosen after seeing the result is
            not a threshold.
        protocol_path: The protocol FILE whose access counter is incremented.
            Tests and development MUST pass a temporary copy: a counter polluted
            by development noise cannot be read as evidence of anything, and
            this repository's whole argument is that its numbers can be. There
            is deliberately no mode that returns the bars without charging.

            TWO THINGS ABOUT THE DEFAULT. It is ``Path("protocol.yaml")``, a
            RELATIVE path, so "the repository's own" holds only when the process
            is running from the repository root - which every documented command
            in this project does. And it is INDEPENDENT of ``protocol``: every
            threshold, window and cost comes from the in-memory object, while
            the touch is charged to this file. A caller that loads one file and
            charges another gets no warning, because nothing here can tell.
        starting_equity: Opening equity for the run, in account currency units.
            Defaults to :data:`~strategylab.search.loop.STARTING_EQUITY`, so a
            validation is run at the same scale as the search that nominated the
            candidate. Sharpe is a ratio of returns and ``max_drawdown_pct`` a
            percentage of a peak, so under the COMMITTED protocol this moves no
            verdict. That is a property of ``commission_min_flat`` being zero,
            not of the arithmetic: at a non-zero flat minimum the cost per trade
            stops scaling with equity, both metrics move, and the pass boundary
            moves with them. See that constant's own docstring, which says the
            same thing and says it belongs in the protocol if it ever bites.

    Returns:
        The verdict and the per-fold evidence behind it.

    Raises:
        ValueError: If any of ``min_sharpe``, ``pass_margin`` or
            ``max_drawdown_ceiling_pct`` is not finite, because a comparison
            against a non-finite threshold never binds and the criterion would
            be reported as applied while not applying; if ``params`` omits a
            placeholder the definition references; if
            ``protocol.windows.fold_months`` is below one, which
            ``load_protocol`` refuses from a FILE but ``Windows`` does not
            refuse from code; if the walk-forward window is shorter than one
            fold; or if the in-sample window does not lie entirely before the
            held-back one - a warm-up drawn from bars dated AFTER the window
            being judged would be lookahead. All five are raised before any
            touch.

            It also propagates, unchanged, the ValueErrors of the engine it
            drives once the touch HAS been charged. The consequential one is
            :func:`~strategylab.backtest.metrics.max_drawdown` refusing an
            equity curve driven to zero: a wiped-out account raises here rather
            than reporting a FAIL, which is a limitation of the engine recorded
            in ``LIMITATIONS.md`` and not a considered decision of this module.
        WindowError: Propagated from :mod:`strategylab.windows` if ``bars``
            cannot be cut, or holds no bar inside a window it is asked for.
            Failing on the in-sample cut costs nothing; failing on the
            held-back cut costs a touch, which has already been charged.

            THE IN-SAMPLE CUT IS SKIPPED ENTIRELY when the warm-up is one bar -
            a strategy with no crossing operator and no periodic indicator needs
            no history, so no prefix is taken and ``in_sample_bars`` is never
            called. A frame holding no in-sample bar at all then validates
            successfully, and costs its touch.
        ProtocolError: Propagated from
            :func:`~strategylab.protocol.increment_walk_forward_touches` if the
            protocol file is missing, unreadable, or carries no counter line. No
            bars are returned and no touch is recorded.
        OSError: Propagated from the same function if the REWRITE fails - a
            read-only file, a permission denial, a full disk. That write is not
            wrapped and is not atomic: the file is truncated and rewritten in
            place, so a failure between the two leaves the pre-registration
            damaged and the count neither its old value nor its new one. An
            earlier version of this clause said the rewrite raised
            ``ProtocolError`` and that no touch was recorded; both were wrong,
            and the second is the dangerous one. Restore ``protocol.yaml`` from
            version control, which is where its authoritative copy lives.
    """
    # A NON-FINITE THRESHOLD IS REFUSED BEFORE ANYTHING IS SPENT, because a
    # verdict measured against one is not a verdict. `load_protocol` checks
    # `max_drawdown_ceiling_pct` only for being positive and `min_sharpe` not at
    # all, so `.inf` loads happily; the drawdown test is then
    # `drawdown > inf`, which is never true, so the ceiling silently stops
    # applying and a PASS can be returned on a criterion nobody applied.
    #
    # Checked HERE rather than left to `write_validation_result`, where
    # `allow_nan=False` would eventually raise on the same figure. That happens
    # after the verdict has been computed, printed and endorsed - the loud
    # failure arrives too late to stop anyone reading the PASS.
    for name, value in (
        ("min_sharpe", protocol.validation.min_sharpe),
        ("pass_margin", protocol.validation.pass_margin),
        ("max_drawdown_ceiling_pct", protocol.validation.max_drawdown_ceiling_pct),
    ):
        if not math.isfinite(value):
            raise ValueError(
                f"protocol.validation.{name} is {value}, which is not finite; a comparison "
                "against it silently never binds, so the criterion would be reported as "
                "applied and would not be"
            )

    # Raises for a placeholder `params` does not supply, which is this
    # function's own contract and costs nothing to discover.
    warmup = required_history(definition, params)
    bounds = _fold_bounds(protocol.windows.walk_forward, protocol.windows.fold_months)
    if protocol.windows.in_sample.end >= protocol.windows.walk_forward.start:
        raise ValueError(
            f"the in-sample window ends {protocol.windows.in_sample.end}, which is not before "
            f"the held-back window's start of {protocol.windows.walk_forward.start}; the "
            "warm-up is drawn from in-sample bars and would otherwise be dated after the "
            "period being judged"
        )

    # Free, and taken FIRST so a frame with no in-sample bars costs no touch.
    prefix = in_sample_bars(bars, protocol).iloc[-(warmup - 1) :] if warmup > 1 else bars.iloc[:0]
    held_back, touches = walk_forward_bars(bars, protocol, protocol_path)

    window = pd.concat([prefix, held_back])
    if len(window) < warmup:
        return _no_run(
            touches,
            warmup,
            protocol.validation,
            f"{len(held_back)} held-back bar(s) and {len(prefix)} warm-up bar(s) before them, "
            f"against a warm-up of {warmup}; there is no bar this candidate could be "
            "evaluated on. The touch was charged anyway - looking is the cost",
        )

    result = run_backtest(definition, params, window, protocol.costs, starting_equity)
    # The first evaluable bar sits at offset `warmup - 1` of `window`, and the
    # prefix is at most `warmup - 1` long, so the curve never reaches back into
    # the in-sample window. It covers the held-back window and nothing else.
    equity = result.equity
    returns = result.returns
    exits = _completed_exits(result.decisions)

    folds: list[FoldResult] = []
    for position, (start, end) in enumerate(bounds):
        in_fold = _inside(equity.index, start, end)
        fold_equity = equity[in_fold]
        fold_returns = returns[_inside(returns.index, start, end)]
        fold_sharpe = sharpe(fold_returns) if len(fold_returns) >= 2 else float("nan")
        fold_drawdown = max_drawdown(fold_equity) if not fold_equity.empty else float("nan")
        fold_trades = int(exits[in_fold].sum())
        folds.append(
            FoldResult(
                index=position,
                start=start,
                end=end,
                sharpe=fold_sharpe,
                max_drawdown_pct=fold_drawdown,
                trade_count=fold_trades,
                outcome=_fold_outcome(fold_sharpe, fold_drawdown, fold_trades, protocol.validation),
            )
        )

    pooled_sharpe = result.sharpe if len(returns) >= 2 else float("nan")
    outcome, reason = _verdict(
        pooled_sharpe, result.max_drawdown_pct, result.trade_count, protocol.validation
    )
    return ValidationResult(
        outcome=outcome,
        folds=tuple(folds),
        pooled_sharpe=pooled_sharpe,
        pooled_max_drawdown_pct=result.max_drawdown_pct,
        total_trades=result.trade_count,
        bars_used=len(equity),
        warmup_bars=warmup,
        threshold=protocol.validation.effective_sharpe_bar,
        reason=reason,
        walk_forward_touches=touches,
    )


def _finite_or_none(value: float) -> float | None:
    """Render a metric for JSON, turning a non-finite value into ``None``.

    JSON has no ``NaN``. The same reasoning, and the same answer, as
    :func:`strategylab.search.loop._finite_or_none` - a bare ``NaN`` token makes
    the artifact unreadable by anything but Python.

    Args:
        value: The metric, in whatever unit that metric carries.

    Returns:
        The value as a float, or ``None`` when it is not finite.
    """
    number = float(value)
    return number if math.isfinite(number) else None


def write_validation_result(
    result: ValidationResult,
    candidate: CandidateRef,
    protocol: Protocol,
    generated_at: datetime,
    directory: Path = DEFAULT_RESULTS_DIR,
) -> Path:
    """Write one verdict to ``state/validation-<stamp>.json``.

    WHY A VERDICT IS PERSISTED AT ALL, when the search loop had to argue for its
    artifact and this one does not: re-running a validation costs a permanent
    touch. A verdict that lives only in a terminal scrollback is a verdict
    somebody will re-derive by spending the one resource this project treats as
    finite. Writing it is what makes the counter's arithmetic honest.

    ``generated_at`` is an argument rather than read from the clock here, for
    the same reason as :func:`~strategylab.search.loop.write_search_result`: a
    function that stamps itself cannot be tested for determinism.

    THE FIELDS, enumerated because :data:`VALIDATION_SCHEMA_VERSION` versions
    them and a version number is worthless without a list to compare against.

    - ``schema_version`` — :data:`VALIDATION_SCHEMA_VERSION`.
    - ``generated_at`` — ISO 8601, always at offset ``+00:00``. Any timezone-aware
      argument is converted to UTC first, so this is not necessarily the offset
      the caller passed.
    - ``protocol_version`` — schema version of the protocol, NOT its values.
    - ``strategy``, ``config``, ``config_content_hash``, ``source_search`` — the
      strategy's name, the config path, the digest of what that config meant
      when it was searched and still means now, and the search artifact that
      nominated this candidate.
    - ``instrument``, ``data_content_hash`` — the research ticker and the hash
      of the snapshot both runs used.
    - ``params`` — the parameter combination validated, values in BARS.
    - ``in_sample`` — the candidate's ``sharpe``, ``plateau_score`` and
      ``degenerate`` flag from the search, so the held-back figure can be read
      against the one it is expected to fall short of.
    - ``noise_ceiling_note`` — the search's own sentence, verbatim. A candidate
      never benchmarked against noise says so in its verdict too.
    - ``window`` — ``start``, ``end``, ``bars`` covered and ``fold_months``.
    - ``warmup_bars`` — the strategy's warm-up at these parameters.
    - ``criteria`` — every pre-registered threshold applied: ``min_sharpe``,
      ``pass_margin``, ``effective_sharpe_bar``, ``max_drawdown_ceiling_pct``,
      ``min_trades_per_fold`` and ``min_trades_pooled``.
    - ``costs_frac`` — ``spread``, ``slippage``, ``commission``,
      ``fx_conversion``, every one a FRACTION, not a percentage.
    - ``commission_min_flat`` — a cash amount, not a rate, which is why it sits
      outside ``costs_frac``.
    - ``outcome``, ``reason`` — the verdict and which criterion bound.
    - ``pooled`` — ``sharpe``, ``max_drawdown_pct`` and ``trades``. A
      non-finite metric is ``null``, never zero.
    - ``walk_forward_touches`` — the counter AFTER this run.
    - ``folds`` — one object per fold with ``index``, ``start``, ``end``,
      ``sharpe``, ``max_drawdown_pct``, ``trade_count`` and ``outcome``.

    Args:
        result: The verdict to record.
        candidate: The candidate validated, carrying the trail back to the
            search artifact and the snapshot.
        protocol: The protocol in force, for the instrument, the window, the
            criteria and the costs.
        generated_at: When the validation ran. Must be timezone-aware; any
            offset is accepted and CONVERTED to UTC, and the converted value is
            what appears both in the filename and in the payload, so the file's
            name and its contents cannot disagree. Converting rather than
            merely requiring UTC is what makes that promise true: the filename
            ends in a literal ``Z``, and a ``+05:00`` argument would otherwise
            produce a name asserting a UTC time the payload contradicts.
        directory: Where to write. Created along with its parents if absent. A
            relative path - and the default, :data:`DEFAULT_RESULTS_DIR`, is
            one - resolves against the WORKING DIRECTORY, which is the
            repository root for every documented command in this project.

    Returns:
        The path written.

    Raises:
        ValueError: If ``generated_at`` is naive - an unqualified timestamp in a
            file meant to be compared against another machine's is a
            reproducibility hazard, not a formatting preference.

            Also raised by :func:`json.dumps` under ``allow_nan=False`` if a
            figure copied straight from the protocol is not finite. Every
            METRIC has been through :func:`_finite_or_none`; the protocol's own
            thresholds have not, and ``load_protocol`` checks
            ``max_drawdown_ceiling_pct`` only for being positive, so an
            ``.inf`` ceiling loads and reaches this payload. Raising is the loud
            failure; writing a file with a bare ``NaN`` token in it is the quiet
            one.
        OSError: Propagated unchanged if the file cannot be written.
    """
    if generated_at.tzinfo is None:
        raise ValueError(
            "generated_at is timezone-naive; a validation artifact records when the "
            "held-back window was read and an unqualified timestamp cannot be compared "
            "against another machine's"
        )
    generated_at = generated_at.astimezone(UTC)

    criteria = protocol.validation
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "protocol_version": protocol.protocol_version,
        "strategy": candidate.strategy,
        "config": candidate.config_path.as_posix(),
        "config_content_hash": candidate.config_content_hash,
        "source_search": candidate.source.as_posix(),
        "instrument": protocol.instruments.research,
        "data_content_hash": candidate.data_content_hash,
        "params": dict(candidate.params),
        "in_sample": {
            "sharpe": _finite_or_none(candidate.search_sharpe),
            "plateau_score": _finite_or_none(candidate.search_plateau_score),
            "degenerate": candidate.degenerate,
        },
        "noise_ceiling_note": candidate.noise_ceiling_note,
        "window": {
            "start": protocol.windows.walk_forward.start.isoformat(),
            "end": protocol.windows.walk_forward.end.isoformat(),
            "bars": result.bars_used,
            "fold_months": protocol.windows.fold_months,
        },
        "warmup_bars": result.warmup_bars,
        "criteria": {
            "min_sharpe": criteria.min_sharpe,
            "pass_margin": criteria.pass_margin,
            "effective_sharpe_bar": criteria.effective_sharpe_bar,
            "max_drawdown_ceiling_pct": criteria.max_drawdown_ceiling_pct,
            "min_trades_per_fold": criteria.min_trades_per_fold,
            "min_trades_pooled": criteria.min_trades_pooled,
        },
        "costs_frac": {
            "spread": protocol.costs.spread_frac,
            "slippage": protocol.costs.slippage_frac,
            "commission": protocol.costs.commission_frac,
            "fx_conversion": protocol.costs.fx_conversion_frac,
        },
        "commission_min_flat": protocol.costs.commission_min_flat,
        "outcome": result.outcome.value,
        "reason": result.reason,
        "pooled": {
            "sharpe": _finite_or_none(result.pooled_sharpe),
            "max_drawdown_pct": _finite_or_none(result.pooled_max_drawdown_pct),
            "trades": result.total_trades,
        },
        "walk_forward_touches": result.walk_forward_touches,
        "folds": [
            {
                "index": fold.index,
                "start": fold.start.isoformat(),
                "end": fold.end.isoformat(),
                "sharpe": _finite_or_none(fold.sharpe),
                "max_drawdown_pct": _finite_or_none(fold.max_drawdown_pct),
                "trade_count": fold.trade_count,
                "outcome": fold.outcome.value,
            }
            for fold in result.folds
        ],
    }

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"validation-{generated_at:%Y%m%dT%H%M%SZ}.json"
    # allow_nan=False for the same reason the search artifact uses it: every
    # metric has been through `_finite_or_none`, but the protocol figures beside
    # them have not, and a bare `NaN` token produces a file a strict parser
    # refuses.
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path

"""The search loop: the piece that joins the grid, the engine and the scoring.

Everything this module drives already existed and none of it was connected.
:func:`~strategylab.search.grid.expand_grid` produced cells nothing backtested,
:func:`~strategylab.search.plateau.plateau_score` described a results frame
nothing built, :func:`~strategylab.search.plateau.is_degenerate` read a
``trade_fingerprint`` column no code anywhere wrote, and
:func:`~strategylab.search.graveyard.record_rejection` carried a
``backtest_failed`` reason with no caller. This module is their caller.

**THIS IS THE FIRST CODE IN THE PROJECT THAT READS BARS FOR A RESULT**, which
makes it the first place the in-sample/walk-forward split can be violated.
``strategylab fetch`` caches 2005-2024 in one parquet file; nothing partitions
it. Until now the split was enforced by the absence of any consumer. Here it is
enforced by :func:`~strategylab.windows.in_sample_bars`, which slices from
``protocol.windows.in_sample`` and by nothing else - not from the frame it was
handed, not from an argument a caller could widen. That accessor is shared with
every other component that reads bars, so the guard cannot drift between them.
``tests/test_search_loop.py`` corrupts every bar after the window's end, passes
the frame in intact, and asserts both the bar COUNT and that no cell's result
moves. Both halves are needed: a signal fills at the NEXT bar's open, so the
window's last bar never produces a fill, and a one-bar leak therefore moves no
metric on a fixture where every cell is flat there. The count is what fails on
that case.

## What this loop does NOT do, and must not be read as doing

**It computes a noise ceiling only when asked.** ``noise_trials`` defaults to
zero, because one trial costs a full grid and the committed search takes about
four minutes. Without a ceiling, a winning Sharpe from this loop is not
evidence of anything - the expected maximum across ninety draws of noise is
well above zero - and the artifact records ``"noise_ceiling": null`` explicitly
rather than omitting the key, because an absent field reads as an oversight and
a null reads as a statement.

**Selection never sees the ceiling, and the search as a whole is judged on it.**
``select_candidate`` ranks by plateau and knows nothing of the surrogates. Once
it has chosen, :func:`~strategylab.search.noise.evaluate_gate` compares that one
cell against the ceiling and the verdict is recorded as
:attr:`SearchResult.noise_gate`. A failure is written to the graveyard under
``below_noise_ceiling`` and the cell remains the winner: what the gate decides
is whether :mod:`strategylab.validate.candidate` will let it be validated, and
that reader refuses anything short of a pass. A search run without ``--trials``,
or with too few to settle the tail, has no verdict and cannot be validated
either. ``docs/decisions/0012`` records the rule and when it was fixed.

**It renders no report.** ``report/surface.py`` reads the JSON this loop writes
and hands ``report/render.py`` a context.

**It flags a degenerate plateau rather than rejecting it**, which is what
``CLAUDE.md`` specifies. ``degenerate_plateau`` is therefore the second
rejection reason in the graveyard's closed vocabulary that this loop never
writes. Both absences are deliberate and both are stated here so that a reader
counting rejection categories against the vocabulary does not read a gap as a
missing branch.

**It writes no candidate file, and now never will.** This module declined to
invent a ``state/candidate.json`` schema on the grounds that the validator owns
it. The validator has since settled the question the other way:
``strategylab validate`` takes a SEARCH ARTIFACT, because that file already
carries the winner together with the protocol version, snapshot hash, costs and
ceiling that make a verdict interpretable. A second file would duplicate those
or drop them, and this project has already paid for one duplicated schema.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from strategylab.backtest.costs import CostModel, Fill
from strategylab.backtest.engine import run_backtest
from strategylab.backtest.metrics import BuyAndHold, buy_and_hold
from strategylab.core.config import INDICATORS, OPERATORS
from strategylab.core.strategy import required_history
from strategylab.core.types import IndicatorRef, StrategyDef
from strategylab.data.source import REQUIRED_COLUMNS
from strategylab.protocol import Protocol
from strategylab.search.graveyard import DEFAULT_GRAVEYARD_PATH, record_rejection
from strategylab.search.grid import expand_grid
from strategylab.search.noise import (
    GATE_FAILED,
    GATE_PERCENTILE,
    RELIABLE_TRIALS,
    NoiseGate,
    describe_gate,
    evaluate_gate,
    noise_ceiling,
    trials_note,
)
from strategylab.search.plateau import (
    ADDED_COLUMNS,
    FINGERPRINT_COLUMN,
    METRIC_COLUMN,
    RESULT_COLUMNS,
    is_degenerate,
    is_edge_winner,
    plateau_score,
)
from strategylab.search.surrogate import block_length_for
from strategylab.windows import WindowError, in_sample_bars

#: Column names a results frame carries that are NOT searched parameters.
#: Imported from :mod:`strategylab.search.plateau` rather than re-listed, so a
#: column added there cannot silently become a searched parameter here. That
#: coupling is real: this module is the producer of the frame that module
#: consumes, and the two must agree on which columns are axes.
_NON_PARAMETER_COLUMNS = RESULT_COLUMNS | ADDED_COLUMNS

#: Schema version of a search artifact. Bumped when the fields change, so a
#: reader meeting an unfamiliar version can say so rather than guess.
#:
#: ``1`` was the shape written by commit ``db180ce``. ``2`` added
#: ``costs_frac`` and ``commission_min_flat``. ``3`` turned ``noise_ceiling``
#: from a permanent ``null`` into an object when one was computed. ``4`` adds
#: ``block_bars`` and ``seed`` to that object, without which a ceiling cannot be
#: reproduced from the artifact that reports it. ``5`` adds
#: ``config_content_hash``, without which a config edited after the search loads
#: silently under the winner's parameters. ``6`` adds ``noise_gate`` and, inside
#: ``noise_ceiling``, ``trial_best_sharpe``. ``7`` adds ``benchmark``.
#:
#: THE BUMP TO 4 IS THE INSTRUCTIVE ONE. Those two fields were added under
#: version 3 rather than bumping, so artifacts written hours apart both claimed
#: version 3 and carried different shapes - exactly the confusion this field
#: exists to prevent, made in the same session as the comment saying so. Bump on
#: every field change, including the ones that feel too small to matter.
#: :func:`write_search_result` enumerates the current fields.
SEARCH_SCHEMA_VERSION = 7

#: Where a search artifact is written, and - since ``validate/`` was built - the
#: default directory for a validation artifact too. ``cli.status`` counts
#: ``search-*.json`` and ``validation-*.json`` here to report how many of each
#: have been run. Named once and imported by
#: :func:`~strategylab.validate.walkforward.write_validation_result` rather than
#: redeclared there, so the two artifact kinds cannot end up in different
#: directories.
DEFAULT_RESULTS_DIR = Path("state")

#: The ``fixed`` key naming position size, checked before a search starts.
#:
#: A DUPLICATED STRING, and the alternative was worse. The name is owned by
#: :mod:`strategylab.core.strategy`, which keeps it private, and reaching into
#: another module's privates to avoid a literal trades one coupling for a worse
#: one. If the two ever disagree this check stops finding anything and the fault
#: it guards reappears as a scattering of failed cells - so it is named here
#: rather than left as a bare literal inside the function.
_SIZE_PARAM = "position_size_pct"

#: Opening account equity for every cell, in account currency units.
#:
#: A reporting choice, not a protocol one, and deliberately not in
#: ``protocol.yaml``: both metrics this loop ranks on are scale-free. Sharpe is
#: a ratio of returns and ``max_drawdown_pct`` is a percentage of a peak, so
#: doubling this number changes neither, and no cell's rank moves. What it does
#: change is the absolute cash in a fill, which matters only once
#: ``commission_min_flat`` is non-zero - it is zero in the committed protocol,
#: and if it ever is not, this constant starts affecting results and belongs in
#: the protocol instead.
STARTING_EQUITY = 10_000.0

#: Neighbourhood half-width, in grid steps, used for both plateau scoring and
#: the degeneracy check. A call-time default rather than pre-registered data;
#: see ``LIMITATIONS.md``.
DEFAULT_RADIUS = 1

#: Fingerprint of a cell that executed nothing at all.
#:
#: ONE SHARED VALUE, on purpose. Cells that took no trades did the same nothing,
#: so a region of them SHOULD read as degenerate - parameters that produce no
#: trades are not doing anything, which is exactly what
#: :func:`~strategylab.search.plateau.is_degenerate` exists to say.
NO_TRADES_FINGERPRINT = "no-trades"

#: Prefix on the fingerprint of a cell that executed at least one fill.
TRADED_PREFIX = "trades:"

#: Prefix on the fingerprint of a cell whose backtest raised.
#:
#: The remainder is the cell itself, so every failed cell carries a DIFFERENT
#: fingerprint. Sharing one - the empty string, or a single ``"failed"`` - would
#: make a region of failures compare equal and report as a degenerate plateau,
#: which says the parameters are doing nothing when in fact nothing ran.
FAILED_PREFIX = "failed:"

#: Recorded in every artifact beside the null ceiling, so a reader of the file
#: alone learns why the field is empty.
NOISE_CEILING_ABSENT_NOTE = (
    "Not computed: this search was run without --trials, so no Sharpe here has "
    "been compared against what the same grid achieves on resampled history. "
    "The best of ninety cells on a series with no exploitable structure is well "
    "above zero. Treat every figure in this file as unbenchmarked."
)


class SearchError(Exception):
    """A search could not be run at all, as opposed to running and finding nothing.

    Distinct from a cell failing. A cell that raises is a result - recorded with
    a ``NaN`` metric and a graveyard entry - whereas this means the search
    produced no surface at all.

    Raised from THREE places, and each covers what the others cannot.
    :func:`_check_inputs` refuses a bad definition or an unslicable frame before
    the first cell runs. :func:`_check_window` refuses a window whose values the
    engine cannot use, and :func:`~strategylab.windows.in_sample_bars` a frame
    with no bar inside the window at all - as a ``WindowError``, which
    :func:`run_search` translates. :func:`run_search` raises after the loop when EVERY cell
    failed, which catches the causes the checks do not know about and, unlike a
    list of checks, cannot fall behind the engine. Without that last one, a
    fault in the definition or the data returns ninety ordinary-looking failures
    and reads as a research outcome.
    """


@dataclass(frozen=True)
class Candidate:
    """A cell that survived every rejection rule, with the figures behind it.

    Attributes:
        cell: The parameter combination, mapping name to value in BARS.
        sharpe: The cell's own annualised Sharpe ratio, a dimensionless ratio.
        max_drawdown_pct: The cell's own largest peak-to-trough decline, as a
            positive PERCENTAGE.
        plateau_score: Median ``sharpe`` across the cell's neighbourhood, in
            the same units as ``sharpe``. THIS IS WHAT THE CELL WAS CHOSEN ON,
            not ``sharpe`` - see :func:`select_candidate`.
        neighbour_count: How many cells carried a finite metric into that
            median, the cell itself included. A small count on a large grid
            means the neighbourhood was mostly failures.
        degenerate: Whether at least
            :data:`~strategylab.search.plateau.DEGENERATE_SHARE` of the
            neighbourhood executed the same trades as this cell. FLAGGED, NOT
            REJECTED: a true here means the smooth surface around this cell is
            insensitivity rather than robustness, and a human decides what to
            do about it.
    """

    cell: dict[str, int]
    sharpe: float
    max_drawdown_pct: float
    plateau_score: float
    neighbour_count: int
    degenerate: bool


@dataclass(frozen=True)
class Rejection:
    """One candidate the loop refused, and why.

    Mirrors what was written to the graveyard, so the artifact is readable
    without opening a second file - with one exception. A winner the noise gate
    refused is written to the graveyard under ``below_noise_ceiling`` and has no
    ``Rejection``; see :attr:`SearchResult.rejections` for why.

    Attributes:
        cell: The rejected combination, mapping name to value in bars.
        reason: One of
            :data:`~strategylab.search.graveyard.REJECTION_REASONS`.
        detail: Human-readable elaboration - the exception message for a failed
            backtest, the offending figure for a breached ceiling. Always
            populated: every rejection this module makes has something to add
            beyond its reason, which is why the field carries no default.
            NOT written to the graveyard, whose schema has no field for it.
    """

    cell: dict[str, int]
    reason: str
    detail: str


@dataclass(frozen=True)
class SearchResult:
    """Everything one search produced.

    Attributes:
        strategy: ``definition.name``, the strategy that was searched.
        config_path: Repository-relative path of the config it was loaded from.
        config_content_hash: Digest of what that config MEANT when it was
            searched, from
            :func:`~strategylab.core.config.config_content_hash`. A path names a
            file; this names its contents, so a reader of the winner can refuse
            a config edited since.
        instrument: The research ticker, from the protocol.
        data_content_hash: Content hash of the price snapshot the search ran
            against. Results are reproducible against a snapshot, never against
            the vendor.
        protocol_version: Schema version of the protocol in force.
        window_start: First date the search was permitted to read.
        window_end: Last date the search was permitted to read. Taken from
            ``protocol.windows.in_sample``, never from the frame.
        bars_used: Rows inside that window. NOT the rows each cell had to trade
            on, which is smaller by that cell's warm-up.
        grid_size: Cells the grid expanded to.
        failed_cells: How many of them raised. Their rows are present with a
            ``NaN`` metric; a hole in the grid is worse than a visible failure.
        plateau_radius: Neighbourhood half-width used, in grid steps.
        starting_equity: Opening equity each cell was given, in account
            currency units.
        max_drawdown_ceiling_pct: The ceiling that was applied, as a
            PERCENTAGE, from the protocol. Recorded because a rejection is only
            interpretable against the bar it was made under, and the protocol
            file a reader has to hand may not be the one this search ran with.
        noise_block_bars: Resampling block length in BARS, or ``None`` when
            no ceiling was computed. Recorded so a ceiling can be reproduced
            from the artifact alone.
        noise_seed: The resampler seed that produced the ceiling.
        noise_ceiling: The best-in-grid Sharpe achieved on each surrogate
            series, or ``None`` when no ceiling was computed. ``None`` is not a
            ceiling of zero and must never be rendered as one: it means the
            comparison was not made.
        noise_gate: Whether ``winner`` cleared that ceiling, with the counts
            behind the answer. Always present, including when no ceiling was
            computed - that is a status, not an absence. THIS, NOT ``winner``,
            IS WHAT SAYS WHETHER A CANDIDATE MAY BE VALIDATED.
        benchmark: What holding the instrument did over the bars the winner
            was measured on - from its first evaluable bar to the window's end -
            or over the whole window when there is no winner. ``None`` only when
            that span holds fewer than three closes. It pays no costs and is
            always fully invested, which sets the bar slightly too high; see
            :func:`~strategylab.backtest.metrics.buy_and_hold`. REPORTED, NOT
            APPLIED: nothing is rejected for losing to it.
        benchmark_basis: ``"winner"`` or ``"window"``, saying which of those two
            spans ``benchmark`` covers.
        costs: The broker parameters every cell was charged, every rate a
            FRACTION. Recorded for the same reason as the ceiling, and with
            more force: costs are the single largest determinant of every
            Sharpe in the file, and the most effective dial there is for
            turning a losing strategy into a winning one. A surface whose costs
            a reader cannot see is a surface they cannot check.
        results: One row per cell: a column per searched parameter holding that
            cell's value in bars, then ``sharpe``, ``max_drawdown_pct``,
            ``trade_count``, ``trade_fingerprint``, ``plateau_score`` and
            ``neighbour_count``. The schema is
            :mod:`strategylab.search.plateau`'s, plus the two columns
            :func:`~strategylab.search.plateau.plateau_score` adds.
        winner: The cell selection chose, or ``None`` when every cell was
            rejected or every cell failed. ``None`` is a real outcome and not an
            error. A winner is NOT a candidate that may be validated: it is the
            best surviving plateau, and ``noise_gate`` says whether that is
            distinguishable from noise. A winner that failed the gate is still
            the winner, because the paper-trading worker tests the simulator
            rather than the strategy and has every use for it.
        rejections: Every refusal SELECTION made, in the order it made them,
            plus every cell whose backtest failed. Together with the winner and
            the cells selection never reached, these partition the grid, which
            is why a winner the noise gate refused is NOT listed here: it would
            be counted twice. That one refusal is in ``noise_gate``, and in the
            graveyard under ``below_noise_ceiling``.
    """

    strategy: str
    config_path: Path
    config_content_hash: str
    instrument: str
    data_content_hash: str
    protocol_version: int
    window_start: date
    window_end: date
    bars_used: int
    grid_size: int
    failed_cells: int
    plateau_radius: int
    starting_equity: float
    max_drawdown_ceiling_pct: float
    noise_ceiling: pd.Series | None
    noise_block_bars: int | None
    noise_seed: int
    noise_gate: NoiseGate
    benchmark: BuyAndHold | None
    benchmark_basis: str
    costs: CostModel
    results: pd.DataFrame
    winner: Candidate | None
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)


def trade_fingerprint(fills: Sequence[Fill]) -> str:
    """Render a cell's executed trades as a string standing for the trade SET.

    :func:`~strategylab.search.plateau.is_degenerate` fixes the property this
    string must have and leaves the content to this loop: two cells share a
    fingerprint exactly when they executed the same trades.

    QUALIFY THAT BEFORE RELYING ON IT. The property holds across cells that
    RAN, which is the population the degeneracy check is about. It is
    deliberately false for cells that did not: two failed cells executed the
    same trades - none - and :func:`failed_fingerprint` gives them different
    strings on purpose, so a region of failures cannot read as a region of
    agreement. A failed cell is outside the biconditional, not a counterexample
    to it.

    WHAT IS HASHED. Four of :class:`~strategylab.backtest.costs.Fill`'s five
    fields: the side, the mid it was priced against, the price paid and the
    quantity. The fifth, ``costs``, is dropped - and that is safe for a reason
    worth stating rather than by inspection. A ``CostBreakdown`` is a pure
    function of the other four fields and the ``CostModel``, and one search
    charges every cell the same ``CostModel``, so within a single search two
    fills agreeing on the four agree on the fifth. Across searches they need
    not, which is one more reason fingerprints from different runs must never
    be compared.

    WHY THE FILLS AND NOT THE DATES. Timestamps would read better in a file, and
    :class:`~strategylab.backtest.costs.Fill` does not carry one; recovering
    them means re-deriving the position transitions out of the decision log,
    which is a second implementation of something the engine already returned.
    The fills are what the engine actually produced, so hashing them cannot
    disagree with it.

    The identity holds in both directions on real bars. Same trade dates implies
    same fills, because the engine is deterministic and every fill price and
    quantity follows from the bars and the cash trajectory. Same fills implies
    same dates only up to a price collision - two different bars whose opens are
    bit-identical floats and which sit at the same point in an otherwise
    identical sequence. That is not impossible, merely vanishingly unlikely on
    price data. A SHA-256 collision would do the same and is more unlikely
    still. Both are soft edges rather than the single one an earlier version of
    this docstring claimed.

    A position opened and never closed contributes its entry fill. Two cells
    differing only in whether the last position was still open are therefore
    different, which is right: they held different things at the end.

    Args:
        fills: The cell's executions in chronological order, as
            :attr:`~strategylab.backtest.engine.BacktestResult.fills` reports
            them.

    Returns:
        :data:`NO_TRADES_FINGERPRINT` when ``fills`` is empty, otherwise
        :data:`TRADED_PREFIX` followed by the SHA-256 of the canonicalised
        sequence in hex. Never the empty string, and never a value
        :func:`failed_fingerprint` can produce.
    """
    if not fills:
        return NO_TRADES_FINGERPRINT
    canonical = "\n".join(
        f"{fill.side.value}|{fill.mid_price!r}|{fill.executed_price!r}|{fill.quantity!r}"
        for fill in fills
    )
    return TRADED_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def failed_fingerprint(cell: dict[str, int]) -> str:
    """Render the fingerprint of a cell whose backtest raised.

    UNIQUE PER CELL, which is the whole point. A shared value would make a
    region of failures compare equal and report as a degenerate plateau -
    "these parameters are doing nothing" - when what actually happened is that
    nothing ran. The two readings call for opposite responses, so they must not
    produce the same string.

    Args:
        cell: The failed combination, mapping name to value in bars.

    Returns:
        :data:`FAILED_PREFIX` followed by the cell rendered in sorted parameter
        order, so the same cell always renders identically regardless of the
        order its keys happen to be in.
    """
    rendered = ",".join(f"{name}={cell[name]}" for name in sorted(cell))
    return FAILED_PREFIX + rendered


def _check_inputs(
    definition: StrategyDef,
    bars: pd.DataFrame,
    protocol: Protocol,
    config_path: Path,
    starting_equity: float,
) -> None:
    """Refuse an input this loop cannot honestly search, before any cell runs.

    THIS EXISTS TO KEEP A BAD ARGUMENT FROM MASQUERADING AS NINETY FAILED CELLS.
    :func:`run_search` catches ``ValueError`` around each backtest and records
    the cell as failed, which is right for the things that are genuinely
    properties of a CELL — a warm-up longer than the window, an account driven
    to zero. But :func:`~strategylab.backtest.engine.run_backtest` raises the
    same type for properties of the INPUTS: an unsorted frame, a duplicate
    timestamp, a missing column, a non-positive ``starting_equity``. Those make
    every cell raise, and without this check the run would return a perfectly
    ordinary result reporting ``failed_cells == grid_size`` — which this
    module's own contract calls a legitimate finding — while appending one
    permanent record per cell to an append-only graveyard.

    So the uniformly-fatal conditions are checked ONCE, here, and raise
    :class:`SearchError` instead.

    THIS FUNCTION IS NOT THE GUARANTEE, and an earlier version of this docstring
    said it was - claiming that what reached the per-cell handler afterwards was
    "cell-specific by elimination". An audit disproved it by naming six causes
    this function does not see, from
    :func:`~strategylab.core.strategy.evaluate` and
    :func:`~strategylab.backtest.costs.apply_costs` rather than from
    :func:`~strategylab.backtest.engine.run_backtest`'s own frame validation.
    All six are checked now, here or in :func:`_check_window` - but that is a
    list catching up with one audit, not a guarantee, and the next such cause
    will be absent from it too. The backstop is what does not go stale.

    The guarantee is structural and lives in :func:`run_search`: if EVERY cell
    fails, there is no surface, and that is a :class:`SearchError` whatever
    caused it. This function exists for the diagnosis rather than the
    guarantee - it names the fault precisely, before four minutes of backtesting
    discover it the slow way. Two kinds of check live here, and they differ in
    how they can rot:

    - The frame checks MIRROR the engine's own private validation, and can drift
      from it. If the engine adds a frame-level check this function does not
      know about, the structural guarantee still catches it; only the quality of
      the message degrades.
    - The definition checks read the CONFIG vocabulary
      (:data:`~strategylab.core.config.INDICATORS`,
      :data:`~strategylab.core.config.OPERATORS`). An earlier version of this
      paragraph said they therefore "cannot drift from the engine without the
      engine already having drifted from the loader", which overstates it. What
      holds is narrower: a NAME-level divergence between this check and the
      engine implies a name-level divergence between the loader and the engine,
      which nothing in this repository detects and no test covers. It does not
      extend beyond names, which is why indicator ARITY and
      ``fixed.position_size_pct`` are checked explicitly here rather than
      assumed to follow from the vocabulary.

    THE STRUCTURAL GUARANTEE HAS A HOLE THIS FUNCTION HELPS FILL, and it is
    worth naming because it is the reason several of these checks exist at all.
    "Every cell failed" catches a fault that is uniformly fatal. A fault that
    fails MOST cells and not all slips through it: a missing
    ``position_size_pct`` fails only the cells that reach an entry, and a bad
    price fails only the cells that read that bar. Those arrive as a scattering
    of failed cells across the surface, which looks exactly like a legitimate
    result. The position-size check below exists for that class; the price
    checks do too, and live in :func:`_check_window` because they read values
    and must therefore see the in-sample slice and nothing else. What remains
    uncovered is any future fault of the same shape that nothing anticipates -
    the structural check cannot see it, and these functions only see what they
    have been told to look for.

    Args:
        definition: The parsed strategy, checked for a searchable ``search``
            block whose parameter names do not collide with result columns.
        bars: The bars as handed in, before any window is cut. NOTHING about
            them is checked here: the shape checks needed to cut a window belong
            to :func:`~strategylab.windows.in_sample_bars`, which owns the
            slicing, and everything that reads a bar's CONTENTS belongs in
            :func:`_check_window`, after the slice, so the held-back window
            cannot decide whether an in-sample search runs.
        protocol: The protocol in force, checked for a primary metric matching
            the one the scoring reads.
        config_path: Checked to be relative, so no username can reach the
            artifact.
        starting_equity: Opening equity in account currency units, checked to
            be positive and finite.

    Returns:
        None.

    Raises:
        SearchError: On any of the above. Every message says what was wrong and
            why this loop will not proceed with it.
    """
    if protocol.validation.primary_metric != METRIC_COLUMN:
        raise SearchError(
            f"the protocol's primary metric is '{protocol.validation.primary_metric}' but "
            f"plateau scoring reads '{METRIC_COLUMN}'; scoring the grid on a metric the "
            "pass decision is not made on would nominate a candidate against one bar and "
            "judge it against another"
        )

    if not definition.search:
        raise SearchError(
            f"strategy '{definition.name}' declares no searched parameters, so there is no "
            "grid to walk; a search needs at least one range in its 'search' block"
        )

    # The definition-level faults. Each of these makes EVERY cell raise
    # identically, so each would otherwise arrive as ninety failed cells. They
    # are checked against the config vocabulary rather than against the engine's
    # internals, so this does not add another copy of the engine to drift from.
    #
    # `load_strategy` already refuses all of them, and that is not sufficient:
    # `run_search` takes a `StrategyDef`, not a path, and a definition built in
    # code never passes the loader. The tests in this repository build them that
    # way, and so would a future caller.
    for name, conditions in (("entry", definition.entry), ("exit", definition.exit)):
        if not conditions:
            raise SearchError(
                f"strategy '{definition.name}' has an empty '{name}' condition list; "
                "evaluate refuses it, so every cell would fail identically"
            )

    supplied = set(definition.search) | set(definition.fixed)
    referenced: set[str] = set()
    unknown_indicators: set[str] = set()
    unknown_operators: set[str] = set()
    wrong_arity: list[str] = []
    for condition in (*definition.entry, *definition.exit):
        if condition.operator not in OPERATORS:
            unknown_operators.add(condition.operator)
        # `Condition.left` is typed `IndicatorRef` and `Condition.right` is
        # `IndicatorRef | float`. Nothing enforces that at run time, and this
        # function's whole reason for existing is that a `StrategyDef` can be
        # built in code without passing the loader.
        if not isinstance(condition.left, IndicatorRef):
            raise SearchError(
                f"strategy '{definition.name}' has {condition.left!r} on the left of a "
                "condition, where only an indicator is allowed; a literal there reaches "
                "the engine as an AttributeError, which this loop does not catch"
            )
        for side in (condition.left, condition.right):
            if not isinstance(side, IndicatorRef):
                # A bare float literal on the right. A non-finite one is
                # refused here for the MESSAGE, not because it would otherwise
                # escape: measured, it fails every cell, so the
                # every-cell-failed backstop does catch it. An earlier version
                # of this comment claimed the opposite - that it failed only
                # some cells and evaded the backstop - and that was wrong. What
                # this buys is a diagnosis naming the literal, instead of four
                # minutes of backtesting followed by a generic failure.
                if isinstance(side, float) and not math.isfinite(side):
                    raise SearchError(
                        f"strategy '{definition.name}' compares against the non-finite "
                        f"literal {side}; no comparison against it is meaningful"
                    )
                continue
            if side.indicator not in INDICATORS:
                unknown_indicators.add(side.indicator)
                continue
            # INDICATORS maps a name to its ARITY - whether it takes a lookback.
            # `IndicatorRef` alone cannot enforce this: it rejects a reference
            # carrying BOTH a period and a placeholder and permits one carrying
            # neither. `sma` with neither reaches an `assert period is not None`
            # in the engine, which is an AssertionError rather than a
            # ValueError, so it is not caught as a failed cell and escapes
            # `run_search` as a type its contract does not admit - and under
            # `python -O` the assert vanishes and a nonsense rolling window is
            # used instead.
            has_period = side.period is not None or side.period_param is not None
            if INDICATORS[side.indicator] and not has_period:
                wrong_arity.append(f"'{side.indicator}' needs a period and has none")
            elif not INDICATORS[side.indicator] and has_period:
                wrong_arity.append(f"'{side.indicator}' takes no period and was given one")
            if side.period_param is not None:
                referenced.add(side.period_param)

    if unknown_indicators:
        raise SearchError(
            f"strategy '{definition.name}' uses indicator(s) {sorted(unknown_indicators)} "
            f"that are not in the vocabulary {sorted(INDICATORS)}"
        )
    if unknown_operators:
        raise SearchError(
            f"strategy '{definition.name}' uses operator(s) {sorted(unknown_operators)} "
            f"that are not in the vocabulary {sorted(OPERATORS)}"
        )
    if wrong_arity:
        raise SearchError(
            f"strategy '{definition.name}' uses an indicator with the wrong arity: "
            + "; ".join(sorted(set(wrong_arity)))
        )
    unsupplied = sorted(referenced - supplied)
    if unsupplied:
        raise SearchError(
            f"strategy '{definition.name}' references placeholder(s) {unsupplied} that "
            "neither its 'search' nor its 'fixed' block supplies, so no cell could resolve "
            "them and every one would fail identically"
        )

    # Position size is read only when a cell actually reaches an ENTER_LONG, so a
    # definition that omits it fails SOME cells and not others - which the
    # all-cells-failed guarantee cannot see. Checked here for that reason: this
    # is one of the faults that would otherwise reach the operator as an
    # ordinary scattering of failed cells across the surface.
    size = definition.fixed.get(_SIZE_PARAM)
    if size is None:
        raise SearchError(
            f"strategy '{definition.name}' can enter a position but its 'fixed' block "
            f"omits '{_SIZE_PARAM}', so it never says how much to buy"
        )
    if not 0.0 < size <= 100.0:
        raise SearchError(
            f"'{_SIZE_PARAM}' is a percentage of equity and must be above 0 and at most "
            f"100, got {size}"
        )

    colliding = sorted(set(definition.search) & _NON_PARAMETER_COLUMNS)
    if colliding:
        raise SearchError(
            f"searched parameter(s) {colliding} collide with the result columns a results "
            f"frame carries ({sorted(_NON_PARAMETER_COLUMNS)}); the metric would overwrite "
            "the parameter in every row and the grid would lose an axis"
        )

    if config_path.is_absolute():
        raise SearchError(
            f"config_path '{config_path}' is absolute; it is recorded in the search "
            "artifact, and an absolute path carries the username of the machine it ran on "
            "into a file this project invites people to read"
        )

    if not (starting_equity > 0) or not math.isfinite(starting_equity):
        raise SearchError(f"starting_equity must be positive and finite, got {starting_equity}")


def _check_window(window: pd.DataFrame) -> None:
    """Refuse an in-sample window this loop cannot honestly search.

    SEPARATE FROM :func:`_check_inputs`, AND THE SEPARATION IS THE POINT. These
    checks read bar VALUES, so running them over the frame as handed in would
    make a search's success depend on the held-back window - a bad price in 2019
    would refuse a search of 2005-2018. That is the coupling this whole module
    exists to prevent, and an earlier version of these checks had it: they ran
    over ``bars`` rather than over the slice.

    Only what is needed to CUT the window is checked before the cut. Everything
    that reads a value is checked after it, here, so the held-back window can
    neither contribute to a result nor decide whether one is produced.

    The faults below all share a shape: each fails only the cells that actually
    read the offending bar, so each arrives as a scattering of failed cells
    across the surface rather than as a uniform failure. The
    every-cell-failed backstop in :func:`run_search` cannot see that, which is
    why they are caught here instead.

    Args:
        window: The bars inside ``protocol.windows.in_sample``, as
            :func:`~strategylab.windows.in_sample_bars` returns them.

    Returns:
        None.

    Raises:
        SearchError: If a required OHLCV column is absent, if the window is
            unsorted or carries duplicate timestamps, or if ``open`` or
            ``close`` holds a value that is not a positive, finite number.

            ``high`` and ``low`` must be PRESENT and their values are NOT
            checked - nothing on the backtest path reads them. See the comment
            beside the check.
    """
    missing = [column for column in REQUIRED_COLUMNS if column not in window.columns]
    if missing:
        raise SearchError(
            f"the in-sample window lacks required column(s) {missing}; got {list(window.columns)}"
        )
    if not window.index.is_monotonic_increasing:
        raise SearchError("the in-sample window is not sorted ascending by its index")
    if window.index.has_duplicates:
        raise SearchError(
            "the in-sample window carries duplicate timestamps; the bar order is ambiguous"
        )

    # ONLY THE COLUMNS THE BACKTEST ACTUALLY READS. The engine takes `open` (the
    # fill price) and `close` (the mark and every indicator's input). `high` and
    # `low` are required to be PRESENT - a bar without them is not an OHLCV bar
    # - but no code on the backtest path reads their values, verified by
    # grepping all of `src/`: the only reader anywhere is
    # `data.source.validate_bars`, which checks them at INGEST, where that
    # belongs. Re-checking them here would refuse a search over data that would
    # have run perfectly, duplicating in a second place the over-strictness this
    # project already documents about its ingest gate.
    prices = ["open", "close"]
    # COERCE RATHER THAN GATE ON DTYPE. An earlier version refused any column
    # that was not already a numeric dtype, which was stricter than the thing it
    # guards: the engine does `to_numpy(dtype="float64")` and handles an
    # object-dtype column of real floats perfectly, and `validate_bars` accepts
    # one at ingest. That check therefore declined searches that would have run.
    # `to_numeric` accepts everything they accept - object, nullable, int64,
    # float32 - and turns a genuine non-number into NaN, which the finiteness
    # test below then reports. Tolerant where the engine is tolerant, strict
    # where it must be.
    numeric = window[prices].apply(pd.to_numeric, errors="coerce")
    # `math.isfinite` rather than `notna`, because an infinity is neither NaN
    # nor caught by `> 0` - `inf > 0` is true. A test for this passed against
    # the weaker check and is the reason the stronger one is here.
    if not bool(numeric.map(math.isfinite).all().all()) or not bool((numeric > 0).all().all()):
        raise SearchError(
            f"the in-sample window carries a value in one of {prices} that is not a "
            "positive, finite number - a non-number, a NaN, an infinity or a value at or "
            "below zero. A bad one fails only the cells that read that bar rather than "
            "the whole search, which is why it is refused here rather than left to the "
            "every-cell-failed check"
        )


def _ranked(scored: pd.DataFrame, parameters: list[str]) -> list[dict[Hashable, Any]]:
    """Order the scoreable cells best-first, deterministically.

    A CELL WHOSE OWN BACKTEST FAILED IS NOT SCOREABLE, even though it may carry
    a perfectly finite ``plateau_score``. That score is the median of its
    NEIGHBOURS, so a failed cell sitting in a good region inherits a good
    number - and nominating it would nominate a cell that produced no result at
    all. Only cells with a finite own metric are ranked.

    Args:
        scored: The frame :func:`~strategylab.search.plateau.plateau_score`
            returned.
        parameters: The searched-parameter column names, used for the tie-break.

    Returns:
        One mapping per scoreable cell, ordered by descending ``plateau_score``,
        ties broken by ascending parameter values compared LEXICOGRAPHICALLY in
        the order ``parameters`` gives - which is the results frame's column
        order, which is the order the config's ``search`` block declares. So on
        the committed three-axis grid a tie is settled by ``fast`` first, then
        ``slow``, then ``trend``.

        The tie-break is not cosmetic: a grid plateau produces exact ties
        routinely - the committed 90-cell search produces several - and without
        it the winner would depend on row order, which would break the
        determinism guarantee the whole project rests on.
    """
    # `isfinite`, not `not isnan`: the contract above says finite, and an
    # infinity is not. No live path produces one - `metrics.sharpe` refuses
    # non-finite returns - so this is the word matching the test rather than a
    # bug fixed. Only the cell's OWN metric is tested: a cell with a finite
    # metric is its own neighbour, so its plateau score can never be NaN, and a
    # second clause checking that could never fire.
    rows = [
        row for row in scored.to_dict(orient="records") if math.isfinite(float(row[METRIC_COLUMN]))
    ]
    rows.sort(key=lambda row: tuple(int(row[name]) for name in parameters))
    rows.sort(key=lambda row: float(row["plateau_score"]), reverse=True)
    return rows


def select_candidate(
    scored: pd.DataFrame,
    search_spec: dict[str, dict[str, float]],
    max_drawdown_ceiling_pct: float,
    radius: int = DEFAULT_RADIUS,
) -> tuple[Candidate | None, tuple[Rejection, ...]]:
    """Walk the scored grid best-first and return the first cell that survives.

    THE RANKING IS BY ``plateau_score``, NOT BY ``sharpe``. The best single cell
    in a grid is usually the luckiest one; the project's rule is neighbourhood
    median, and this is where that rule is actually applied to a decision rather
    than merely computed.

    THE TWO REJECTION RULES ARE APPLIED IN A FIXED ORDER - the edge check
    first, the drawdown ceiling second - and the order is observable, not an
    implementation detail. A cell that is both on an edge and over the ceiling
    is recorded once, as ``edge_winner``, and the ceiling breach is never
    written down. The order is arbitrary but it must be stated, because
    otherwise which reason appears in the graveyard for such a cell cannot be
    derived from anything written.

    Two rules reject and one flags:

    - **Edge winner - rejected.** A winner on a boundary of the searched grid is
      a wall, not an optimum: the surface was still improving when the range ran
      out. The loop moves to the next-best cell rather than stopping, so a
      search reports something even when its best few cells are all on the edge.
    - **Drawdown ceiling - rejected.** Above
      ``protocol.validation.max_drawdown_ceiling_pct``, regardless of return.
    - **Degenerate plateau - flagged.** ``CLAUDE.md`` says degenerate plateaus
      are flagged, not rejected, so this sets
      :attr:`Candidate.degenerate` and returns the cell. Nothing is written to
      the graveyard for it, which is why ``degenerate_plateau`` is a rejection
      reason this loop never uses.

    Args:
        scored: The frame :func:`~strategylab.search.plateau.plateau_score`
            returned, one row per cell of a complete grid.
        search_spec: The ranges that were searched, in the shape
            :func:`~strategylab.search.grid.expand_grid` accepts. Needed because
            the edge check builds its axes by re-expanding them.
        max_drawdown_ceiling_pct: Rejection ceiling as a PERCENTAGE, so ``25.0``
            means twenty-five percent. Compared against ``max_drawdown_pct``,
            which carries the same units.
        radius: Neighbourhood half-width in grid steps, for the degeneracy
            check. Must match the radius the frame was scored at, or the flag
            describes a different neighbourhood from the score beside it.

    Returns:
        The surviving candidate and every rejection made on the way to it. The
        candidate is ``None`` when no cell survived - every scoreable cell sat
        on an edge or breached the ceiling, or no cell was scoreable at all.
        The rejections are returned either way, so a search that found nothing
        still says what it threw out.

    Raises:
        ValueError: Propagated unchanged from
            :func:`~strategylab.search.plateau.is_edge_winner` and
            :func:`~strategylab.search.plateau.is_degenerate` - a frame that is
            not a complete grid, a cell off the axes its range generates, a
            non-string fingerprint.

            Those divide into two kinds and an earlier version of this clause
            called them all one. A malformed ``search_spec`` is BAD USER YAML:
            ``is_edge_winner`` builds its axes by calling
            :func:`~strategylab.search.grid.expand_grid`, so every refusal that
            function makes - a bound that is not a whole number of bars, a step
            below one, a maximum under its minimum - reaches here unchanged.
            :func:`run_search` never surfaces those, because it expands the
            same spec first and they raise there; a direct caller of this
            function will. Everything else - an incomplete grid, an off-axis
            cell, a fingerprint that is not a string - is a producer bug in
            whatever built the frame.
    """
    parameters = [str(column) for column in scored.columns if column not in _NON_PARAMETER_COLUMNS]

    rejections: list[Rejection] = []
    for row in _ranked(scored, parameters):
        cell = {name: int(row[name]) for name in parameters}

        if is_edge_winner(cell, search_spec):
            rejections.append(
                Rejection(
                    cell,
                    "edge_winner",
                    "sits on a boundary of the searched grid; widen the range rather "
                    "than trade the wall",
                )
            )
            continue

        drawdown = float(row["max_drawdown_pct"])
        if drawdown > max_drawdown_ceiling_pct:
            rejections.append(
                Rejection(
                    cell,
                    "drawdown_ceiling",
                    f"drawdown {drawdown:.2f}% exceeds the ceiling of "
                    f"{max_drawdown_ceiling_pct:.2f}%",
                )
            )
            continue

        return (
            Candidate(
                cell=cell,
                sharpe=float(row[METRIC_COLUMN]),
                max_drawdown_pct=drawdown,
                plateau_score=float(row["plateau_score"]),
                neighbour_count=int(row["neighbour_count"]),
                degenerate=is_degenerate(scored, cell, radius=radius),
            ),
            tuple(rejections),
        )

    return None, tuple(rejections)


def run_search(
    definition: StrategyDef,
    bars: pd.DataFrame,
    protocol: Protocol,
    *,
    config_path: Path,
    config_content_hash: str,
    data_content_hash: str,
    radius: int = DEFAULT_RADIUS,
    graveyard_path: Path = DEFAULT_GRAVEYARD_PATH,
    starting_equity: float = STARTING_EQUITY,
    noise_trials: int = 0,
    noise_seed: int = 0,
) -> SearchResult:
    """Backtest every cell of a strategy's grid over the in-sample window and score it.

    The whole loop, in order: cut the bars to
    ``protocol.windows.in_sample``; expand the grid; backtest every cell;
    fingerprint each cell's trades; score the surface by neighbourhood median;
    take the best surviving cell. Every rejection is accumulated and written to
    the graveyard in one pass at the end, once the surface is known to exist.

    THE RETURN VALUE is deterministic: nothing computing it consults a clock or
    a random source, and ``tests/test_search_loop.py`` asserts that two runs
    over the same inputs produce an identical results frame, an identical
    winner and an identical rejection list. The artifact's ``generated_at`` is
    supplied by the caller rather than read here, for that reason - see
    :func:`write_search_result`.

    THE SIDE EFFECT is not deterministic, and the distinction matters to anyone
    diffing two runs. Every rejection this function records goes through
    :func:`~strategylab.search.graveyard.record_rejection`, which stamps
    ``recorded_at`` from the clock. Two identical searches produce identical
    results and non-identical graveyard lines.

    ONE ROW PER CELL, INCLUDING THE CELLS THAT FAILED. A backtest can raise: an
    account driven to zero reaches
    :func:`~strategylab.backtest.metrics.max_drawdown`, which refuses a
    non-positive equity, and a cell whose warm-up is longer than the window has
    no bar to be evaluated on. Such a cell keeps its row with a ``NaN`` metric
    and a fingerprint unique to it, because a dropped row is a hole in the GRID
    and makes cells two steps apart look adjacent. It is also recorded in the
    graveyard under ``backtest_failed``.

    EVERY CELL FAILING IS AN ERROR, NOT A FINDING, and an earlier version of
    this contract said the opposite - that it returned a result with no winner
    and ``failed_cells == grid_size``, describing a window too short for the
    ranges declared. That reading cannot be sustained: several causes of a
    uniformly failing grid are faults in the definition or the data rather than
    facts about the window, and a result file reporting ninety nulls made them
    indistinguishable from a research outcome. A grid in which nothing survived
    now raises :class:`SearchError` carrying the first failure's own message,
    which is what names the actual fault.

    THE GRAVEYARD IS WRITTEN ONCE, AT THE END, after the surface is known to
    exist. Rejections are accumulated in memory during the loop. A run that
    raises therefore records nothing, which is right: the graveyard is
    append-only, and rejections from a search that produced no result are
    permanent noise qualifying nothing.

    Args:
        definition: The parsed strategy, with its ``$placeholder`` periods
            unresolved. One parsed definition serves every cell;
            :func:`~strategylab.core.strategy.evaluate` resolves the
            placeholders against each cell's ``params`` mapping. Checked by
            :func:`_check_inputs` for a searchable ``search`` block, parameter
            names that do not collide with result columns, non-empty condition
            lists, a known indicator and operator vocabulary, correct indicator
            arity, placeholders something supplies, and a usable
            ``fixed.position_size_pct``.
        bars: OHLCV bars indexed by an ascending, timezone-naive
            :class:`pandas.DatetimeIndex`, carrying the required OHLCV columns
            with positive finite prices. May span any range, including the
            held-back window - what is read is decided here, not by what is
            passed.
        protocol: The protocol in force. Read for the in-sample window, the cost
            model - whose every rate is a FRACTION, never a percentage - the
            primary metric, the drawdown ceiling, the research instrument and
            the protocol version.
        config_path: Path the definition was loaded from, recorded in the
            result so a figure can be traced to the file that produced it. Must
            be repository-relative; an absolute path would carry a username into
            a file this project may publish.
        config_content_hash: Digest of what the config at ``config_path`` means,
            from :func:`~strategylab.core.config.config_content_hash`. Recorded,
            not checked: the caller holds the file and this function never opens
            it. :func:`~strategylab.validate.candidate.read_candidate` is what
            compares it against the file later.
        data_content_hash: Content hash of the price snapshot, from the cache
            entry. Recorded for the same reason: results are reproducible
            against a snapshot, and vendors revise history.
        radius: Neighbourhood half-width in GRID STEPS for both the plateau
            score and the degeneracy flag.
        graveyard_path: Where rejections are appended. Relative paths resolve
            against the working directory.
        starting_equity: Opening equity for every cell, in account currency
            units. See :data:`STARTING_EQUITY` for why this is not protocol
            data.
        noise_trials: Surrogate series to run for the noise ceiling. ZERO MEANS
            NO CEILING, which is the default because one trial costs a full
            grid: the committed search takes about four minutes, so a hundred
            trials is most of a day. A result without a ceiling is not evidence
            of an edge and says so throughout.
        noise_seed: Seed for the surrogate resampler, so a ceiling is
            reproducible.

    Returns:
        The complete result, including the rows of the cells that failed and
        every rejection made.

    Raises:
        SearchError: Raised from three places. :func:`_check_inputs` refuses a
            bad definition or an unslicable frame BEFORE any cell runs, and
            :func:`_check_window` refuses a window whose values the engine
            cannot use - see their docstrings for the lists.
            :func:`~strategylab.windows.in_sample_bars` refuses a frame with no
            bar in the window, as a ``WindowError`` this function translates.
            This function raises again AFTER the loop if every cell failed,
            which is the guarantee proper: it catches the causes the checks do
            not know about, at the cost of a less specific message.
        ValueError: Propagated unchanged from
            :func:`~strategylab.search.grid.expand_grid` for a malformed search
            range - which is bad user YAML, not an internal fault - and from
            the scoring functions for a frame this module built wrongly. A
            ``ValueError`` from
            :func:`~strategylab.backtest.engine.run_backtest` is NOT propagated:
            it is caught and recorded as a failed cell. Do NOT read that as
            "such an error is cell-specific" - an earlier version of this clause
            said so and it is false. Some are properties of the definition or
            the data; what makes them safe is that a fault failing EVERY cell
            raises after the loop, not that only cell-level faults arrive
            here.
        MemoryError: Propagated unchanged from
            :func:`~strategylab.search.grid.expand_grid`, which builds both the
            axes and their product eagerly and caps neither. A search block
            declaring a large enough product hangs or exhausts memory rather
            than being refused; that is the callee's stated behaviour and
            nothing here improves on it.
        OSError: Propagated unchanged if the graveyard cannot be written. A
            search whose rejections are silently lost is worse than one that
            stops.

            The graveyard is written in one pass at the end, so a run that
            raises anywhere before that point records nothing. An earlier
            version wrote each rejection as it was made and could leave a
            partial graveyard behind for a search that returned no result.
    """
    _check_inputs(definition, bars, protocol, config_path, starting_equity)
    try:
        window = in_sample_bars(bars, protocol)
    except WindowError as exc:
        # Re-typed, not swallowed. `windows.py` owns the slicing and raises its
        # own error; a caller of `run_search` should not have to catch two
        # exception types to handle "this search could not run".
        raise SearchError(str(exc)) from exc
    _check_window(window)
    grid = expand_grid(definition.search)

    rows: list[dict[str, Any]] = []
    rejections: list[Rejection] = []
    pending_records: list[tuple[dict[str, int], dict[str, float], str]] = []
    failed = 0
    first_failure = ""

    for cell in grid:
        try:
            result = run_backtest(definition, cell, window, protocol.costs, starting_equity)
        except ValueError as exc:
            failed += 1
            first_failure = first_failure or str(exc)
            metrics = {METRIC_COLUMN: math.nan, "max_drawdown_pct": math.nan}
            rows.append(
                {
                    **cell,
                    METRIC_COLUMN: math.nan,
                    "max_drawdown_pct": math.nan,
                    "trade_count": 0,
                    FINGERPRINT_COLUMN: failed_fingerprint(cell),
                }
            )
            pending_records.append((cell, metrics, "backtest_failed"))
            rejections.append(Rejection(cell, "backtest_failed", str(exc)))
            continue

        rows.append(
            {
                **cell,
                METRIC_COLUMN: result.sharpe,
                "max_drawdown_pct": result.max_drawdown_pct,
                "trade_count": result.trade_count,
                FINGERPRINT_COLUMN: trade_fingerprint(result.fills),
            }
        )

    # EVERY CELL FAILING IS AN ERROR, NOT A FINDING. An earlier version of this
    # function returned an ordinary result reporting `failed_cells ==
    # grid_size`, and documented that as a legitimate outcome meaning the window
    # was too short for the ranges declared. An audit showed why that cannot
    # stand: `run_backtest` raises ValueError for six causes that are properties
    # of the DEFINITION or the DATA rather than of a cell - an empty condition
    # list, a placeholder nothing supplies, an indicator the engine does not
    # implement, a non-finite bar, a non-positive price, a non-numeric column -
    # and each makes all ninety cells raise identically. Reported as ninety
    # ordinary failures, a malformed argument is indistinguishable from a
    # research outcome.
    #
    # Rather than mirror six more of the engine's internal checks in
    # `_check_inputs` - which is what produced the drift the last audit found -
    # the condition is caught structurally: if nothing survived, the search
    # produced no surface, and that is a SearchError whatever caused it. The
    # first failure's own message is carried out, because it names the actual
    # fault; a too-short window says so, and so does a missing placeholder.
    if failed == len(grid):
        raise SearchError(
            f"every one of the {len(grid)} cells failed to backtest, so there is no "
            f"surface to score. The first failure was: {first_failure}"
        )

    scored = plateau_score(pd.DataFrame(rows), radius=radius)
    winner, selection_rejections = select_candidate(
        scored,
        definition.search,
        protocol.validation.max_drawdown_ceiling_pct,
        radius=radius,
    )
    for rejection in selection_rejections:
        row = scored.loc[
            _row_mask(scored, rejection.cell), [METRIC_COLUMN, "max_drawdown_pct"]
        ].iloc[0]
        pending_records.append(
            (
                rejection.cell,
                {
                    METRIC_COLUMN: float(row[METRIC_COLUMN]),
                    "max_drawdown_pct": float(row["max_drawdown_pct"]),
                },
                rejection.reason,
            )
        )
    rejections.extend(selection_rejections)

    # THE CEILING IS COMPUTED ON THE SAME WINDOW THE SEARCH READ, and on
    # nothing else. Handing `bars` here instead of `window` would compute it
    # partly on held-back data, which is a read of that data whatever it is
    # used for.
    ceiling = None
    block_bars = None
    if noise_trials > 0:
        block_bars = block_length_for(definition.search)
        ceiling = noise_ceiling(
            definition, definition.search, window, protocol.costs, noise_trials, noise_seed
        )

    # THE GATE JUDGES THE CELL SELECTION CHOSE, ONCE. A failure is written to
    # the graveyard under `below_noise_ceiling` - the one reason in its
    # vocabulary this loop used never to write - but NOT to `rejections`, and
    # the cell stays the winner. Nothing walks on to the next-best cell: that
    # would choose a cell for its own Sharpe, which is what ranking by plateau
    # exists to avoid. See :class:`SearchResult` for why the two lists differ.
    gate = evaluate_gate(ceiling, None if winner is None else winner.sharpe)
    if winner is not None and gate.status == GATE_FAILED:
        pending_records.append(
            (
                winner.cell,
                {METRIC_COLUMN: winner.sharpe, "max_drawdown_pct": winner.max_drawdown_pct},
                "below_noise_ceiling",
            )
        )

    # THE GRAVEYARD IS WRITTEN LAST, in one pass, once the run is known to have
    # produced a surface. Writing each rejection as it was made left permanent
    # records behind for runs that then raised - a partial graveyard for a
    # search that returned nothing, which the previous contract audit named and
    # which no amount of input checking removes on its own. Deferring costs
    # nothing that matters: a run that does not complete has no rejections worth
    # keeping, because it has no result they qualify. That now includes a run
    # whose ceiling raised, hours in: it records nothing either.
    for cell, metrics, reason in pending_records:
        record_rejection(cell, metrics, reason, graveyard_path)

    # THE BENCHMARK COVERS THE BARS THE WINNER WAS MEASURED ON, not the whole
    # window. A cell's curve starts at its first evaluable bar, so its Sharpe
    # never saw the warm-up; holding the index through bars the strategy was not
    # scored on would compare two different periods. With no winner there is no
    # such span, and the whole window is the only honest one left.
    first = 0 if winner is None else required_history(definition, winner.cell) - 1
    held = window["close"].iloc[first:]
    benchmark = buy_and_hold(held) if len(held) >= 3 else None

    in_sample = protocol.windows.in_sample
    return SearchResult(
        strategy=definition.name,
        config_path=config_path,
        config_content_hash=config_content_hash,
        instrument=protocol.instruments.research,
        data_content_hash=data_content_hash,
        protocol_version=protocol.protocol_version,
        window_start=in_sample.start,
        window_end=in_sample.end,
        bars_used=len(window),
        grid_size=len(grid),
        failed_cells=failed,
        plateau_radius=radius,
        starting_equity=starting_equity,
        max_drawdown_ceiling_pct=protocol.validation.max_drawdown_ceiling_pct,
        noise_ceiling=ceiling,
        noise_block_bars=block_bars,
        noise_seed=noise_seed,
        noise_gate=gate,
        benchmark=benchmark,
        benchmark_basis="window" if winner is None else "winner",
        costs=protocol.costs,
        results=scored,
        winner=winner,
        rejections=tuple(rejections),
    )


def _row_mask(scored: pd.DataFrame, cell: dict[str, int]) -> pd.Series:
    """Build a boolean mask selecting the one row matching a cell.

    Args:
        scored: The scored results frame.
        cell: The combination to find, mapping parameter name to value in bars.

    Returns:
        A boolean Series, true on exactly the matching row.

    Raises:
        ValueError: If the cell matches no row, or more than one. Neither can
            happen on any path in this module - the cells come from
            :func:`_ranked` over the same frame, and
            :func:`~strategylab.search.plateau.plateau_score` has already
            refused a frame with duplicate combinations. It is checked because
            the alternative failure is an ``IndexError`` from an ``.iloc[0]``
            two lines away in the caller, which says nothing about what went
            wrong, and because "true on exactly one row" is the kind of
            invariant a later edit breaks silently.
    """
    mask = pd.Series(True, index=scored.index)
    for name, value in cell.items():
        mask &= scored[name] == value
    matched = int(mask.sum())
    if matched != 1:
        raise ValueError(
            f"cell {cell} matches {matched} rows of the results frame, expected exactly one"
        )
    return mask


def _ceiling_payload(
    ceiling: pd.Series | None, block_bars: int | None, seed: int
) -> dict[str, Any] | None:
    """Render a noise ceiling for the artifact, or ``None`` if there is none.

    THE WHOLE DISTRIBUTION IS SUMMARISED, NOT REDUCED TO ONE NUMBER. A single
    "the ceiling was 1.4" invites a reader to treat it as a threshold; the
    percentiles show how wide the thing is, which is the fact that decides
    whether a winner clears it.

    Trials that produced no scoreable cell are ``NaN`` and are excluded from the
    percentiles while still being counted in ``trials``, so a reader can see
    that the summary rests on fewer surrogates than were run.

    Args:
        ceiling: Best-in-grid Sharpe per surrogate, or ``None``.
        block_bars: Resampling block length in BARS, or ``None`` when there is
            no ceiling. Recorded because a ceiling cannot be reproduced without
            it and it is derived from the grid rather than fixed.
        seed: The resampler seed, for the same reason.

    Returns:
        ``None``, or the summary described in :func:`write_search_result`.
    """
    if ceiling is None:
        return None
    finite = ceiling.dropna()
    return {
        "trials": int(len(ceiling)),
        "scoreable_trials": int(len(finite)),
        "block_bars": block_bars,
        "seed": seed,
        "percentiles": {
            "p50": _finite_or_none(float(finite.quantile(0.50))) if len(finite) else None,
            "p90": _finite_or_none(float(finite.quantile(0.90))) if len(finite) else None,
            "p95": _finite_or_none(float(finite.quantile(0.95))) if len(finite) else None,
        },
        "max": _finite_or_none(float(finite.max())) if len(finite) else None,
        # Every trial, in the order it ran, so a reader can draw the
        # distribution and recount the gate rather than trust three percentiles.
        # An unscoreable trial is `null`, in its place, not dropped.
        "trial_best_sharpe": [_finite_or_none(float(value)) for value in ceiling],
    }


def _gate_payload(gate: NoiseGate) -> dict[str, Any]:
    """Render the noise gate's verdict for the artifact.

    The rule travels with the verdict: ``percentile`` and ``min_trials`` are
    constants today, and they are written into every artifact so that a verdict
    read after either constant has changed still says what it was measured
    against.

    Args:
        gate: The verdict.

    Returns:
        The object described in :func:`write_search_result`.
    """
    return {
        "status": gate.status,
        "percentile": GATE_PERCENTILE,
        "min_trials": RELIABLE_TRIALS,
        "candidate_sharpe": (
            None if gate.candidate_sharpe is None else _finite_or_none(gate.candidate_sharpe)
        ),
        "scoreable_trials": gate.scoreable_trials,
        "beaten": gate.beaten,
        "required": gate.required,
        "summary": describe_gate(gate),
    }


def _finite_or_none(value: float) -> float | None:
    """Render a metric for JSON, turning a non-finite value into ``None``.

    JSON has no ``NaN``. Python's :mod:`json` writes the bare token ``NaN``,
    which a strict parser refuses, so one failed cell would make the artifact
    unreadable by anything but Python. Same reasoning, and the same answer, as
    :mod:`strategylab.search.graveyard` applies to a graveyard record.

    Args:
        value: The metric, in whatever unit that metric carries.

    Returns:
        The value as a float, or ``None`` when it is not finite.
    """
    number = float(value)
    return number if math.isfinite(number) else None


def _cell_payload(row: dict[Hashable, Any], parameters: list[str]) -> dict[str, Any]:
    """Render one results row as a JSON object.

    Args:
        row: One record from the scored results frame.
        parameters: The searched-parameter column names.

    Returns:
        The row with its parameters nested under ``params`` and every metric
        made JSON-safe.
    """
    return {
        "params": {name: int(row[name]) for name in parameters},
        METRIC_COLUMN: _finite_or_none(row[METRIC_COLUMN]),
        "max_drawdown_pct": _finite_or_none(row["max_drawdown_pct"]),
        "trade_count": int(row["trade_count"]),
        FINGERPRINT_COLUMN: str(row[FINGERPRINT_COLUMN]),
        "plateau_score": _finite_or_none(row["plateau_score"]),
        "neighbour_count": int(row["neighbour_count"]),
    }


def write_search_result(
    result: SearchResult,
    generated_at: datetime,
    directory: Path = DEFAULT_RESULTS_DIR,
) -> Path:
    """Write one search result to ``state/search-<stamp>.json``.

    The whole surface, not just the winner. The output of this project is the
    SHAPE of the results across the parameter space - whether the good region is
    broad or a spike - and a file holding only the best cell cannot show that.
    It is also what ``report/surface.py`` reads, and what makes a later run
    comparable against this one.

    ``generated_at`` is an argument rather than read from the clock here, for
    the same reason ``cache_write`` takes ``fetched_at``: a function that stamps
    itself cannot be tested for determinism, and every other field in this file
    is reproducible from the inputs.

    THE FIELDS, enumerated because :data:`SEARCH_SCHEMA_VERSION` versions them
    and a version number is worthless without a list to compare against. Nothing
    should read this file positionally; every field is named.

    - ``schema_version`` — :data:`SEARCH_SCHEMA_VERSION`.
    - ``generated_at`` — ISO 8601, always at offset ``+00:00``. Any timezone-aware
      argument is converted to UTC first, so this is not necessarily the offset
      the caller passed.
    - ``protocol_version`` — schema version of the protocol, NOT its values.
    - ``strategy``, ``config``, ``config_content_hash`` — the strategy's name,
      the relative path it was loaded from, and the digest of what that file
      meant when it was searched.
    - ``instrument``, ``data_content_hash`` — the research ticker and the hash
      of the snapshot searched. Results are reproducible against a snapshot.
    - ``window`` — ``start``, ``end``, and ``bars``, the row count inside it.
    - ``primary_metric`` — the column ranked on, always ``sharpe`` today.
    - ``max_drawdown_ceiling_pct`` — the rejection ceiling applied, a PERCENTAGE.
    - ``costs_frac`` — ``spread``, ``slippage``, ``commission``,
      ``fx_conversion``, every one a FRACTION, not a percentage.
    - ``commission_min_flat`` — a cash amount in account currency units, not a
      rate, which is why it sits outside ``costs_frac``.
    - ``plateau_radius`` — neighbourhood half-width in GRID STEPS.
    - ``starting_equity`` — account currency units.
    - ``grid_size``, ``failed_cells`` — cells walked, and how many raised.
    - ``noise_ceiling`` — ``null`` when none was computed, otherwise an object
      with ``trials``, ``scoreable_trials``, ``block_bars``, ``seed``,
      ``percentiles`` (the 50th, 90th and 95th of the best-in-grid Sharpe across
      surrogates), ``max``, and ``trial_best_sharpe`` - every trial's value in
      the order it ran, ``null`` where a trial scored nothing. A ``null``
      ceiling is NOT a ceiling of zero; ``noise_ceiling_note`` says which case
      applies and, when a ceiling exists, what its trial count supports.
    - ``noise_gate`` — never ``null``. An object with ``status`` (one of
      :data:`~strategylab.search.noise.GATE_STATUSES`), the rule it was judged
      under as ``percentile`` and ``min_trials``, the counts ``scoreable_trials``,
      ``beaten`` and ``required``, the ``candidate_sharpe`` judged, and a
      ``summary`` sentence. ``beaten`` and ``required`` are ``null`` when there
      was no candidate or nothing scoreable to count against.
    - ``benchmark`` — what holding the instrument did: ``kind`` (always
      ``buy_and_hold``), ``instrument``, ``basis`` (``winner`` when it covers the
      winner's own bars, ``window`` when there was no winner), ``start``,
      ``end``, ``bars``, ``sharpe`` and ``max_drawdown_pct``, a PERCENTAGE. No
      costs, always fully invested. ``null`` only when the span is too short to
      have two returns.
    - ``winner`` — ``null``, or an object carrying ``params``, ``sharpe``,
      ``max_drawdown_pct``, ``plateau_score``, ``neighbour_count`` and
      ``degenerate``. The cell selection chose; ``noise_gate`` says whether it
      may be validated.
    - ``rejections`` — a list of ``params``, ``reason``, ``detail``, in the
      order selection made them. A winner the noise gate refused is not among
      them; see :attr:`SearchResult.rejections`.
    - ``cells`` — one object per grid cell, each with ``params``, ``sharpe``,
      ``max_drawdown_pct``, ``trade_count``, ``trade_fingerprint``,
      ``plateau_score`` and ``neighbour_count``. A metric that is not finite is
      ``null``. Every cell appears, including the ones that raised.

    Args:
        result: The search to record.
        generated_at: When the search ran. Must be timezone-aware; any offset is
            accepted and CONVERTED to UTC, and the converted value is what
            appears both in the filename and in the payload, so the file's name
            and its contents cannot disagree. Converting rather than merely
            requiring UTC is what makes that promise true: the filename ends in
            a literal ``Z``, and a ``+05:00`` argument would otherwise produce a
            name asserting a UTC time the payload contradicts.
        directory: Where to write. Created along with its parents if absent. A
            relative path resolves against the WORKING DIRECTORY, which is the
            repository root for every documented command in this project.

    Returns:
        The path written.

    Raises:
        ValueError: If ``generated_at`` is naive - an unqualified timestamp in a
            file meant to be compared against another machine's is a
            reproducibility hazard, not a formatting preference.
        OSError: Propagated unchanged if the file cannot be written.
    """
    if generated_at.tzinfo is None:
        raise ValueError(
            "generated_at is timezone-naive; a search artifact records when it ran and "
            "an unqualified timestamp cannot be compared against another machine's"
        )
    generated_at = generated_at.astimezone(UTC)

    parameters = [
        str(column) for column in result.results.columns if column not in _NON_PARAMETER_COLUMNS
    ]

    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "protocol_version": result.protocol_version,
        "strategy": result.strategy,
        "config": result.config_path.as_posix(),
        "config_content_hash": result.config_content_hash,
        "instrument": result.instrument,
        "data_content_hash": result.data_content_hash,
        "window": {
            "start": result.window_start.isoformat(),
            "end": result.window_end.isoformat(),
            "bars": result.bars_used,
        },
        "primary_metric": METRIC_COLUMN,
        "max_drawdown_ceiling_pct": result.max_drawdown_ceiling_pct,
        "costs_frac": {
            "spread": result.costs.spread_frac,
            "slippage": result.costs.slippage_frac,
            "commission": result.costs.commission_frac,
            "fx_conversion": result.costs.fx_conversion_frac,
        },
        "commission_min_flat": result.costs.commission_min_flat,
        "plateau_radius": result.plateau_radius,
        "starting_equity": result.starting_equity,
        "grid_size": result.grid_size,
        "failed_cells": result.failed_cells,
        "noise_ceiling": _ceiling_payload(
            result.noise_ceiling, result.noise_block_bars, result.noise_seed
        ),
        "noise_ceiling_note": (
            NOISE_CEILING_ABSENT_NOTE
            if result.noise_ceiling is None
            else trials_note(len(result.noise_ceiling))
        ),
        "noise_gate": _gate_payload(result.noise_gate),
        "benchmark": None
        if result.benchmark is None
        else {
            "kind": "buy_and_hold",
            "instrument": result.instrument,
            "basis": result.benchmark_basis,
            "start": result.benchmark.start.isoformat(),
            "end": result.benchmark.end.isoformat(),
            "bars": result.benchmark.bars,
            METRIC_COLUMN: _finite_or_none(result.benchmark.sharpe),
            "max_drawdown_pct": _finite_or_none(result.benchmark.max_drawdown_pct),
        },
        "winner": None
        if result.winner is None
        else {
            "params": result.winner.cell,
            METRIC_COLUMN: _finite_or_none(result.winner.sharpe),
            "max_drawdown_pct": _finite_or_none(result.winner.max_drawdown_pct),
            "plateau_score": _finite_or_none(result.winner.plateau_score),
            "neighbour_count": result.winner.neighbour_count,
            "degenerate": result.winner.degenerate,
        },
        "rejections": [
            {"params": rejection.cell, "reason": rejection.reason, "detail": rejection.detail}
            for rejection in result.rejections
        ],
        "cells": [
            _cell_payload(row, parameters) for row in result.results.to_dict(orient="records")
        ],
    }

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"search-{generated_at:%Y%m%dT%H%M%SZ}.json"
    # allow_nan=False, as the graveyard does. Every metric has already been
    # through `_finite_or_none`, but the protocol figures beside them have not -
    # nothing in `load_protocol` checks that the drawdown ceiling is finite - and
    # a bare `NaN` or `Infinity` token would produce a file that a strict parser
    # refuses. Raising here is the loud failure; writing invalid JSON is the
    # quiet one.
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path

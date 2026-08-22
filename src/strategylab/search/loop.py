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
enforced by :func:`_in_sample_window`, which slices from
``protocol.windows.in_sample`` and by nothing else - not from the frame it was
handed, not from an argument a caller could widen.
``tests/test_search_loop.py`` corrupts every bar after the window's end, passes
the frame in intact, and asserts no cell's result moves.

## What this loop does NOT do, and must not be read as doing

**It computes no noise ceiling.** :mod:`strategylab.search.noise` is still a
stub, so nothing here compares the winner against the best Sharpe the same
ninety cells reach on surrogate series with no edge in them. A winning Sharpe
from this loop is therefore not evidence of anything - the expected maximum
across ninety draws of noise is well above zero, and this loop reports that
maximum without a bar to hold it against. The artifact records
``"noise_ceiling": null`` explicitly rather than omitting the key, because an
absent field reads as an oversight and a null reads as a statement.
``below_noise_ceiling`` is consequently a rejection reason this loop never
writes.

**It renders no report.** ``report/render.py`` is a stub. This loop writes JSON.

**It flags a degenerate plateau rather than rejecting it**, which is what
``CLAUDE.md`` specifies. ``degenerate_plateau`` is therefore the second
rejection reason in the graveyard's closed vocabulary that this loop never
writes. Both absences are deliberate and both are stated here so that a reader
counting rejection categories against the vocabulary does not read a gap as a
missing branch.

**It writes no candidate file.** ``strategylab validate``'s help text names
``state/candidate.json`` as its argument. That schema belongs to the validator,
which does not exist, and guessing it here is how a project ends up with two
shapes of the same record - which has already happened once, with the decision
log. The winner is in the search artifact; the validator will decide what it
wants to be handed.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from strategylab.backtest.costs import Fill
from strategylab.backtest.engine import run_backtest
from strategylab.core.types import StrategyDef
from strategylab.protocol import Protocol
from strategylab.search.graveyard import DEFAULT_GRAVEYARD_PATH, record_rejection
from strategylab.search.grid import expand_grid
from strategylab.search.plateau import (
    FINGERPRINT_COLUMN,
    METRIC_COLUMN,
    is_degenerate,
    is_edge_winner,
    plateau_score,
)

#: Schema version of a search artifact. Bumped when the fields change, so a
#: reader meeting an unfamiliar version can say so rather than guess.
SEARCH_SCHEMA_VERSION = 1

#: Where a search artifact is written. ``cli.status`` counts ``search-*.json``
#: here to report how many searches have been run.
DEFAULT_RESULTS_DIR = Path("state")

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
NOISE_CEILING_NOTE = (
    "Not computed: search/noise.py is a stub. Until it exists, no Sharpe here "
    "has been compared against what this same grid achieves on surrogate "
    "series with no edge in them, and the best of ninety cells on noise is "
    "well above zero. Treat every figure in this file as unbenchmarked."
)


class SearchError(Exception):
    """A search could not be run at all, as opposed to running and finding nothing.

    Distinct from a cell failing. A cell that raises is a result - recorded with
    a ``NaN`` metric and a graveyard entry - whereas this means the search never
    started: no bars in the window, or a protocol whose primary metric is not
    the one the scoring reads.
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
    without opening a second file.

    Attributes:
        cell: The rejected combination, mapping name to value in bars.
        reason: One of
            :data:`~strategylab.search.graveyard.REJECTION_REASONS`.
        detail: Human-readable elaboration - the exception message for a failed
            backtest, the offending figure for a breached ceiling. Empty when
            ``reason`` says everything there is to say. NOT written to the
            graveyard, whose schema has no field for it.
    """

    cell: dict[str, int]
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class SearchResult:
    """Everything one search produced.

    Attributes:
        strategy: ``definition.name``, the strategy that was searched.
        config_path: Repository-relative path of the config it was loaded from.
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
        results: One row per cell: a column per searched parameter holding that
            cell's value in bars, then ``sharpe``, ``max_drawdown_pct``,
            ``trade_count``, ``trade_fingerprint``, ``plateau_score`` and
            ``neighbour_count``. The schema is
            :mod:`strategylab.search.plateau`'s, plus the two columns
            :func:`~strategylab.search.plateau.plateau_score` adds.
        winner: The surviving candidate, or ``None`` when every cell was
            rejected or every cell failed. ``None`` is a real outcome and not an
            error.
        rejections: Every refusal, in the order they were made. A cell whose
            backtest failed appears here too.
    """

    strategy: str
    config_path: Path
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
    results: pd.DataFrame
    winner: Candidate | None
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)


def trade_fingerprint(fills: Sequence[Fill]) -> str:
    """Render a cell's executed trades as a string standing for the trade SET.

    :func:`~strategylab.search.plateau.is_degenerate` fixes the property this
    string must have and leaves the content to this loop: two cells share a
    fingerprint exactly when they executed the same trades. The fills carry
    that, and carry nothing else - a fill is a side, the mid it was priced
    against, the price paid and the quantity, so an identical ordered sequence
    of them IS an identical set of executions.

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
    price data, and it is the one soft edge in this function.

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


def _in_sample_window(bars: pd.DataFrame, protocol: Protocol) -> pd.DataFrame:
    """Cut the bars down to the pre-registered in-sample window.

    THE ONLY PLACE A SEARCH DECIDES WHICH BARS IT MAY SEE, and it decides from
    ``protocol.windows.in_sample`` alone. The frame it is handed may span the
    held-back window - ``fetch`` caches 2005-2024 in one file and nothing
    partitions it - and the search must be unable to tell.

    Timestamps are normalised to midnight before comparison, so a bar carrying
    an intraday time on the window's last date is inside the window rather than
    silently dropped by a ``<=`` against that date at midnight.

    Args:
        bars: OHLCV bars indexed by ascending timestamp, spanning any range.
        protocol: The protocol in force, read for its in-sample window.

    Returns:
        The rows dated within ``[in_sample.start, in_sample.end]`` inclusive, in
        their original order. A view-independent copy is not taken; the frame is
        only ever read.

    Raises:
        SearchError: If ``bars`` is not indexed by timestamp, or if no bar falls
            inside the window.
    """
    try:
        stamps = pd.DatetimeIndex(bars.index).normalize()
    except (TypeError, ValueError) as exc:
        raise SearchError(
            f"bars is not indexed by timestamp, so the in-sample window cannot be cut "
            f"from it: {exc}"
        ) from exc

    window = protocol.windows.in_sample
    inside = (stamps >= pd.Timestamp(window.start)) & (stamps <= pd.Timestamp(window.end))
    selected = bars.loc[inside]
    if selected.empty:
        raise SearchError(
            f"no bar falls inside the in-sample window {window.start} to {window.end}; "
            f"the frame spans {stamps.min().date()} to {stamps.max().date()}"
            if len(stamps)
            else f"no bar falls inside the in-sample window {window.start} to "
            f"{window.end}; the frame is empty"
        )
    return selected


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
        One mapping per scoreable cell, ordered by descending ``plateau_score``
        and then by ascending parameter values. The tie-break is not cosmetic:
        a grid plateau produces exact ties routinely, and without it the winner
        would depend on row order, which would break the determinism guarantee
        the whole project rests on.
    """
    rows = [
        row
        for row in scored.to_dict(orient="records")
        if not math.isnan(float(row[METRIC_COLUMN])) and not math.isnan(float(row["plateau_score"]))
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
            non-string fingerprint. All of those are producer bugs in this
            module rather than bad user input.
    """
    known = {METRIC_COLUMN, "max_drawdown_pct", "trade_count", FINGERPRINT_COLUMN}
    known |= {"plateau_score", "neighbour_count"}
    parameters = [str(column) for column in scored.columns if column not in known]

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
    data_content_hash: str,
    radius: int = DEFAULT_RADIUS,
    graveyard_path: Path = DEFAULT_GRAVEYARD_PATH,
    starting_equity: float = STARTING_EQUITY,
) -> SearchResult:
    """Backtest every cell of a strategy's grid over the in-sample window and score it.

    The whole loop, in order: cut the bars to
    ``protocol.windows.in_sample``; expand the grid; backtest every cell;
    fingerprint each cell's trades; score the surface by neighbourhood median;
    take the best surviving cell. Every rejection is appended to the graveyard
    as it is made.

    Deterministic. Nothing here consults a clock or a random source, and
    ``tests/test_search_loop.py`` asserts that two runs over the same inputs
    produce an identical results frame, an identical winner and an identical
    rejection list. The artifact's ``generated_at`` is the one thing that moves
    between runs, and it is supplied by the caller rather than read here - see
    :func:`write_search_result`.

    ONE ROW PER CELL, INCLUDING THE CELLS THAT FAILED. A backtest can raise: an
    account driven to zero reaches
    :func:`~strategylab.backtest.metrics.max_drawdown`, which refuses a
    non-positive equity, and a cell whose warm-up is longer than the window has
    no bar to be evaluated on. Such a cell keeps its row with a ``NaN`` metric
    and a fingerprint unique to it, because a dropped row is a hole in the GRID
    and makes cells two steps apart look adjacent. It is also recorded in the
    graveyard under ``backtest_failed``.

    Every cell failing is not an error either - it returns a result with no
    winner and ``failed_cells == grid_size``. That is a finding about the
    window being too short for the ranges declared, and the caller reports it.

    Args:
        definition: The parsed strategy, with its ``$placeholder`` periods
            unresolved. One parsed definition serves every cell;
            :func:`~strategylab.core.strategy.evaluate` resolves the
            placeholders against each cell's ``params`` mapping.
        bars: OHLCV bars indexed by ascending timestamp. May span any range,
            including the held-back window - what is read is decided here, not
            by what is passed.
        protocol: The protocol in force. Read for the in-sample window, the cost
            model, the primary metric, the drawdown ceiling, the research
            instrument and the protocol version.
        config_path: Path the definition was loaded from, recorded in the
            result so a figure can be traced to the file that produced it. Must
            be repository-relative; an absolute path would carry a username into
            a file this project may publish.
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

    Returns:
        The complete result, including the rows of the cells that failed and
        every rejection made.

    Raises:
        SearchError: If ``protocol.validation.primary_metric`` is not the metric
            the scoring reads, if ``bars`` is not indexed by timestamp, or if no
            bar falls inside the in-sample window.
        ValueError: Propagated unchanged from
            :func:`~strategylab.search.grid.expand_grid` for a malformed search
            range, and from the scoring functions for a frame this module built
            wrongly. A ``ValueError`` from
            :func:`~strategylab.backtest.engine.run_backtest` is NOT propagated:
            that is a failed cell, and it is caught and recorded.
        OSError: Propagated unchanged if the graveyard cannot be written. A
            search whose rejections are silently lost is worse than one that
            stops.
    """
    if protocol.validation.primary_metric != METRIC_COLUMN:
        raise SearchError(
            f"the protocol's primary metric is '{protocol.validation.primary_metric}' but "
            f"plateau scoring reads '{METRIC_COLUMN}'; scoring the grid on a metric the "
            "pass decision is not made on would nominate a candidate against one bar and "
            "judge it against another"
        )

    window = _in_sample_window(bars, protocol)
    grid = expand_grid(definition.search)

    rows: list[dict[str, Any]] = []
    rejections: list[Rejection] = []
    failed = 0

    for cell in grid:
        try:
            result = run_backtest(definition, cell, window, protocol.costs, starting_equity)
        except ValueError as exc:
            failed += 1
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
            record_rejection(cell, metrics, "backtest_failed", graveyard_path)
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
        record_rejection(
            rejection.cell,
            {
                METRIC_COLUMN: float(row[METRIC_COLUMN]),
                "max_drawdown_pct": float(row["max_drawdown_pct"]),
            },
            rejection.reason,
            graveyard_path,
        )
    rejections.extend(selection_rejections)

    in_sample = protocol.windows.in_sample
    return SearchResult(
        strategy=definition.name,
        config_path=config_path,
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
        results=scored,
        winner=winner,
        rejections=tuple(rejections),
    )


def _row_mask(scored: pd.DataFrame, cell: dict[str, int]) -> Any:
    """Build a boolean mask selecting the one row matching a cell.

    Args:
        scored: The scored results frame.
        cell: The combination to find, mapping parameter name to value in bars.

    Returns:
        A boolean Series, true on exactly the matching row.
    """
    mask = pd.Series(True, index=scored.index)
    for name, value in cell.items():
        mask &= scored[name] == value
    return mask


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
    It is also what the report component will read, and what makes a later run
    comparable against this one.

    ``generated_at`` is an argument rather than read from the clock here, for
    the same reason ``cache_write`` takes ``fetched_at``: a function that stamps
    itself cannot be tested for determinism, and every other field in this file
    is reproducible from the inputs.

    Args:
        result: The search to record.
        generated_at: When the search ran, timezone-aware and in UTC. Used both
            in the filename and in the payload, so the file's name and its
            contents cannot disagree.
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

    known = {METRIC_COLUMN, "max_drawdown_pct", "trade_count", FINGERPRINT_COLUMN}
    known |= {"plateau_score", "neighbour_count"}
    parameters = [str(column) for column in result.results.columns if column not in known]

    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "protocol_version": result.protocol_version,
        "strategy": result.strategy,
        "config": result.config_path.as_posix(),
        "instrument": result.instrument,
        "data_content_hash": result.data_content_hash,
        "window": {
            "start": result.window_start.isoformat(),
            "end": result.window_end.isoformat(),
            "bars": result.bars_used,
        },
        "primary_metric": METRIC_COLUMN,
        "max_drawdown_ceiling_pct": result.max_drawdown_ceiling_pct,
        "plateau_radius": result.plateau_radius,
        "starting_equity": result.starting_equity,
        "grid_size": result.grid_size,
        "failed_cells": result.failed_cells,
        "noise_ceiling": None,
        "noise_ceiling_note": NOISE_CEILING_NOTE,
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
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path

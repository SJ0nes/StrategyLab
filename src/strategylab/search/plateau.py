"""Plateau scoring: preferring broad regions to isolated peaks.

The best single cell in a grid is usually the luckiest one. A parameter setting
worth trading is surrounded by other settings that also work, because that is
what it means for an edge to be insensitive to a number nobody can know
precisely in advance.

THE SHAPE OF A RESULTS FRAME IS FIXED HERE. It was written before anything
produced one, to define the interface the search loop would have to meet;
:mod:`strategylab.search.loop` now produces exactly this shape and
``strategylab search`` runs it, so what follows describes a frame that exists
rather than one that is intended. A results frame carries:

- one column per searched parameter, named as the config's ``search`` block names
  it, holding that cell's value in bars;
- ``sharpe`` and ``max_drawdown_pct`` and ``trade_count``, as
  :class:`~strategylab.backtest.engine.BacktestResult` reports them;
- ``trade_fingerprint``, a string standing for the cell's trade SET rather than
  its count - see :func:`is_degenerate`. This module fixes only the PROPERTY the
  string must have: two cells share one exactly when they executed the same
  trades. What goes in it is the search loop's choice, and two cases need a
  deliberate answer there rather than a default - a cell that took no trades,
  and a cell whose backtest raised. Giving both the empty string would make a
  region of failures look like a region of agreement.

Every other column name is taken to be a parameter, and NOTHING CHECKS THAT THE
RESULT COLUMNS ARE ALL PRESENT. Only the two columns actually read are required:
``sharpe`` by :func:`plateau_score` and ``trade_fingerprint` by
:func:`is_degenerate`. A frame without ``max_drawdown_pct`` scores fine.

The consequence is worth stating because it is not obvious: a misspelled
``sharp`` is not a missing metric, it is an extra PARAMETER. The frame then
looks like a grid with one more axis than it has, and the error a caller sees is
about the grid being incomplete or carrying duplicate rows - a true statement
about a frame whose real fault is a typo. That is a closed schema without a
closed check, and closing it needs the caller to declare its parameters, which
this signature does not take.

ONE ROW PER CELL, ALWAYS, INCLUDING THE CELLS THAT FAILED. A backtest can raise
- an account driven to zero reaches ``max_drawdown``, which refuses a
non-positive equity - and the search must still emit the row, with ``NaN`` in the
metric, rather than dropping it. A dropped row is not a hole in the median; it is
a hole in the GRID, and it makes cells two steps apart look adjacent.
:func:`plateau_score` refuses a frame that is not a complete grid for exactly
that reason, and a ``NaN`` metric is excluded from a median while being visible
in ``neighbour_count``.

That refusal has a limit worth knowing before relying on it: it catches rows
missing from a grid, not a whole parameter LEVEL missing from one. See
:func:`_validate_grid`.
"""

from __future__ import annotations

import math
from statistics import median

import pandas as pd

from strategylab.search.grid import expand_grid

#: The metric plateau scoring reads. Must match ``protocol.validation
#: .primary_metric``, which is ``sharpe``. The coupling is real and unenforced:
#: this module takes no protocol, so a protocol naming a different primary metric
#: would leave this scoring the wrong column. Stated rather than guarded, because
#: the guard belongs in the search loop that reads both.
METRIC_COLUMN = "sharpe"

#: The column standing for a cell's trade set, read by :func:`is_degenerate`.
FINGERPRINT_COLUMN = "trade_fingerprint"

#: Columns a results frame carries that are NOT searched parameters.
RESULT_COLUMNS = frozenset({METRIC_COLUMN, "max_drawdown_pct", "trade_count", FINGERPRINT_COLUMN})

#: Columns :func:`plateau_score` adds. Reserved so a second pass cannot mistake
#: its own output for a parameter.
ADDED_COLUMNS = frozenset({"plateau_score", "neighbour_count"})

#: The share of a neighbourhood that must carry the SAME trade fingerprint as the
#: cell being judged for that neighbourhood to count as degenerate.
#:
#: Four fifths rather than all of it, because a plateau whose every cell but one
#: trades identically is insensitive in the way that matters, and requiring
#: literal unanimity would let a single differing corner cell clear a region that
#: is doing nothing. It is a REPORTING threshold - degenerate plateaus are
#: flagged, not rejected - which is why it is a constant here rather than a
#: number in ``protocol.yaml``: the protocol is written once and carries what
#: decides a pass, and this decides what a report says.
DEGENERATE_SHARE = 0.8


def _parameter_columns(results: pd.DataFrame) -> list[str]:
    """Return the searched-parameter columns of a results frame, in frame order.

    Args:
        results: The results frame.

    Returns:
        Every column that is not a known result or added column, in the order
        the frame carries them.
    """
    known = RESULT_COLUMNS | ADDED_COLUMNS
    return [str(column) for column in results.columns if column not in known]


def _axes(results: pd.DataFrame, parameters: list[str]) -> dict[str, list[float]]:
    """Return the ascending distinct values present on each parameter axis.

    Args:
        results: The results frame.
        parameters: The parameter column names.

    Returns:
        Each parameter's distinct values, ascending. Position in this list is
        what ``radius`` counts in, which is what makes a radius a number of GRID
        STEPS rather than of parameter units.
    """
    return {name: sorted(set(results[name].tolist())) for name in parameters}


def _validate_grid(results: pd.DataFrame, parameters: list[str]) -> None:
    """Refuse a frame that is not one row per cell of a complete grid.

    A missing row is not a thin neighbourhood, which this module handles and
    reports. It punches a hole the neighbourhood walk cannot see, so the cells
    around it are scored over a region that was never searched.

    WHAT THIS CANNOT CATCH, and the caller must therefore not do: dropping every
    row at one value of a parameter. In the committed 6x5x3 grid, fifteen rows
    carry ``fast=15``; remove them all and what remains is a complete 5x5x3 grid,
    indistinguishable from a search that only ever declared five ``fast``
    values - and the positions of ``10`` and ``20`` become adjacent, so cells
    two grid steps apart are scored as neighbours. Detecting that needs the
    declared ``search_spec``, which this function does not receive. A partial
    row drop is refused; a whole level silently is not.

    Args:
        results: The results frame.
        parameters: The parameter column names.

    Raises:
        ValueError: If two rows share a parameter combination, or if the frame
            is not the full cartesian product of the values it contains.
    """
    combinations = results[parameters].apply(tuple, axis=1)
    if combinations.duplicated().any():
        duplicated = sorted({str(value) for value in combinations[combinations.duplicated()]})
        raise ValueError(
            f"results carries more than one row for {len(duplicated)} parameter "
            f"combination(s), such as {duplicated[0]}; a results frame is one row per cell"
        )

    expected = 1
    for values in _axes(results, parameters).values():
        expected *= len(values)
    if len(results) != expected:
        raise ValueError(
            f"results holds {len(results)} rows but the values it contains span a complete "
            f"grid of {expected}; a cell that failed must still carry a row, with a NaN "
            f"'{METRIC_COLUMN}', because a dropped row shortens an axis and makes cells two "
            "steps apart look adjacent"
        )


def _neighbourhoods(results: pd.DataFrame, parameters: list[str], radius: int) -> list[list[int]]:
    """Return, for each row, the positional indices of its neighbourhood.

    A cell is its own neighbour: the distance from a cell to itself is zero,
    which is within any radius, and a neighbourhood median that excluded the
    cell being scored would answer a different question.

    Args:
        results: The results frame.
        parameters: The parameter column names.
        radius: Neighbourhood half-width in grid steps.

    Returns:
        One list of positional indices per row, in row order.
    """
    axes = _axes(results, parameters)
    positions = [
        [axes[name].index(value) for name, value in zip(parameters, row, strict=True)]
        for row in results[parameters].itertuples(index=False, name=None)
    ]
    return [
        [
            other
            for other, other_position in enumerate(positions)
            if all(abs(a - b) <= radius for a, b in zip(position, other_position, strict=True))
        ]
        for position in positions
    ]


def plateau_score(results: pd.DataFrame, radius: int = 1) -> pd.DataFrame:
    """Score each grid cell by the median of its parameter neighbourhood.

    A cell is scored by the MEDIAN of the cells within ``radius`` steps of it
    along every parameter axis, not by its own result, so isolated spikes lose
    to broad regions. The median rather than the mean, so one adjacent outlier
    cannot carry a neighbourhood.

    The cell counts as its own neighbour, so a radius of ``1`` on a single axis
    scores over three cells and not two. A radius of ``0`` is permitted and
    scores each cell by itself alone, which is the no-plateau baseline a caller
    may want to compare against.

    Args:
        results: One row per grid cell. Carries a column per searched parameter
            holding that cell's value in bars, plus the metric being scored.
            See this module's docstring for the full schema; a column this
            module does not recognise is treated as a parameter.
        radius: Neighbourhood half-width in GRID STEPS, not in parameter units,
            so a radius of ``1`` means the immediately adjacent cells on each
            axis regardless of how coarse the step is. Positions are taken from
            the ascending distinct values PRESENT in ``results``, which is why
            the frame must be a complete grid.

    Returns:
        A copy of ``results`` with a ``plateau_score`` column added, holding the
        neighbourhood median for each cell, and a ``neighbour_count`` column
        holding how many cells actually contributed to it. Cells at the grid
        boundary are scored over the neighbours they actually have, and the
        count makes a thin neighbourhood visible rather than assumed. The input
        frame is not modified.

        A ``NaN`` metric - the cell failed - is excluded from every median it
        would otherwise enter, and is not counted in ``neighbour_count``. A
        neighbourhood with no finite metric at all scores ``NaN`` with a count
        of ``0`` rather than raising, because one unscoreable region is not a
        reason to refuse the whole surface.

    Raises:
        ValueError: If ``results`` is empty, lacks the metric column, carries no
            parameter column at all, holds more than one row for a parameter
            combination, is not the complete grid its own values span, or
            ``radius`` is negative.
    """
    if radius < 0:
        raise ValueError(f"radius is {radius}; a neighbourhood half-width cannot be negative")
    if results.empty:
        raise ValueError("results is empty; there is no grid to score")
    if METRIC_COLUMN not in results.columns:
        raise ValueError(
            f"results lacks the '{METRIC_COLUMN}' column that plateau scoring reads; "
            f"it carries {sorted(str(column) for column in results.columns)}"
        )

    parameters = _parameter_columns(results)
    if not parameters:
        raise ValueError(
            "results carries no parameter column; every column is a known result column, "
            "so there is no axis to take a neighbourhood along"
        )
    _validate_grid(results, parameters)

    metric = [float(value) for value in results[METRIC_COLUMN].tolist()]
    scores: list[float] = []
    counts: list[int] = []
    for neighbourhood in _neighbourhoods(results, parameters, radius):
        finite = [metric[index] for index in neighbourhood if not math.isnan(metric[index])]
        scores.append(median(finite) if finite else math.nan)
        counts.append(len(finite))

    scored = results.copy()
    scored["plateau_score"] = scores
    scored["neighbour_count"] = counts
    return scored


def is_edge_winner(cell: dict[str, int], search_spec: dict[str, dict[str, float]]) -> bool:
    """Report whether a winning cell sits on a boundary of the searched grid.

    A winner on the edge is a wall, not an optimum: the surface was still
    improving when the range ran out, so the true best value lies outside what
    was searched. The caller must reject it and widen the range rather than
    trade it.

    THE BOUNDARY IS THE HIGHEST VALUE THE GRID ACTUALLY REACHES, which is not
    always the declared ``max``. A range is inclusive of ``max`` only when ``max``
    falls on a step boundary, so ``{min: 1, max: 10, step: 4}`` tops out at 9 and
    a cell at 9 IS on the edge. Comparing against the declared ``max`` would
    report that grid as having no upper edge at all, and a wall would be traded
    as an optimum. The axis is therefore taken from
    :func:`~strategylab.search.grid.expand_grid`, so the two can never disagree.

    Args:
        cell: The winning combination, mapping parameter name to value in bars.
        search_spec: The ranges that were searched, in the shape
            :func:`~strategylab.search.grid.expand_grid` accepts.

    Returns:
        ``True`` if any parameter sits at the lowest or highest value its axis
        reaches. A single-cell axis - ``min`` equal to ``max`` - is both at once,
        and reports ``True``: a parameter that was not really searched cannot
        testify that its winner is interior.

    Raises:
        ValueError: If ``cell`` names a parameter absent from ``search_spec``, if
            it omits one ``search_spec`` declares, or if a value is not on the
            axis its parameter actually generated. The last is not in the
            contract this function carried as a stub, and is added because
            "is this value at the edge" has no answer for a value that is not on
            the grid at all - a caller asking it has already lost track of which
            grid produced the cell.

            Every ``ValueError`` :func:`expand_grid` raises propagates unchanged,
            because the axis is built by calling it.
    """
    grid = expand_grid(search_spec)
    axes: dict[str, list[int]] = {name: sorted({row[name] for row in grid}) for name in search_spec}

    unknown = sorted(set(cell) - set(axes))
    if unknown:
        raise ValueError(
            f"cell names {unknown}, which the searched ranges do not declare; "
            f"they declare {sorted(axes)}"
        )
    missing = sorted(set(axes) - set(cell))
    if missing:
        raise ValueError(
            f"cell omits {missing}, which the searched ranges declare; a cell is a value "
            "for every searched parameter"
        )

    # Every value is validated BEFORE any edge is reported. Interleaving the two
    # made the off-axis refusal depend on parameter order: a cell whose first
    # parameter sat on an edge returned True without the rest ever being looked
    # at, so the same malformed cell raised or did not according to which
    # parameter happened to come first.
    for name, values in axes.items():
        if cell[name] not in values:
            raise ValueError(
                f"cell has '{name}' = {cell[name]}, which is not a value that range "
                f"generates; it generates {values[0]} to {values[-1]}"
            )
    return any(cell[name] in (values[0], values[-1]) for name, values in axes.items())


def is_degenerate(results: pd.DataFrame, cell: dict[str, int], radius: int = 1) -> bool:
    """Report whether a cell's neighbourhood is flat through insensitivity.

    Flatness from parameter insensitivity is NOT robustness and must not be
    reported as a plateau. If neighbouring cells produce near-identical trade
    sets, the parameters are not doing anything, and the smooth surface is an
    artefact rather than evidence that the edge is stable.

    THE COMPARISON IS OVER TRADE SETS, NOT TRADE COUNTS. Two cells that each
    took five trades on different days are not the same strategy, and a count
    would call them identical. So a results frame carries a
    ``trade_fingerprint`` per cell: a string standing for the executed trades
    themselves, which the search loop is responsible for building such that two
    cells share a fingerprint exactly when they executed the same trades. A cell
    that never traded has a fingerprint like any other, and a neighbourhood of
    such cells is degenerate - correctly, because parameters that produce no
    trades at all are doing nothing.

    Args:
        results: One row per grid cell, as passed to :func:`plateau_score`, and
            carrying a ``trade_fingerprint`` column.
        cell: The combination whose neighbourhood is being judged. Must name
            every searched parameter, and its values must be present in
            ``results``.
        radius: Neighbourhood half-width in grid steps, counted as in
            :func:`plateau_score`.

    Returns:
        ``True`` if at least :data:`DEGENERATE_SHARE` of the neighbourhood
        carries the same fingerprint as ``cell``, meaning the plateau is
        degenerate. The cell counts within its own neighbourhood, so a
        neighbourhood of one is trivially degenerate - which is honest: a single
        cell offers no evidence that anything varies.

    Raises:
        ValueError: If ``results`` lacks the ``trade_fingerprint`` column or
            holds a non-string in it, if it is empty or not a complete grid, if
            ``cell`` does not name exactly the frame's parameter columns, if
            ``cell`` is not present in ``results``, or if ``radius`` is negative.

            THE NON-STRING CHECK IS AN ADDITION, and it exists because a ``NaN``
            fingerprint silently inverted the promise two paragraphs above.
            ``NaN`` is equal to nothing, itself included, so a cell carrying one
            matched none of its own neighbourhood, and a neighbourhood of one
            reported NOT degenerate - the exact opposite of what this contract
            says a single cell does. A missing fingerprint is a producer bug,
            and it is refused rather than allowed to invert an answer.
    """
    if radius < 0:
        raise ValueError(f"radius is {radius}; a neighbourhood half-width cannot be negative")
    if results.empty:
        raise ValueError("results is empty; there is no neighbourhood to judge")
    if FINGERPRINT_COLUMN not in results.columns:
        raise ValueError(
            f"results lacks the '{FINGERPRINT_COLUMN}' column, so trade SETS cannot be "
            "compared; a trade count would call two different sets of the same size identical"
        )

    fingerprints = results[FINGERPRINT_COLUMN].tolist()
    unstringy = sorted(
        {type(value).__name__ for value in fingerprints if not isinstance(value, str)}
    )
    if unstringy:
        raise ValueError(
            f"'{FINGERPRINT_COLUMN}' holds {unstringy} where every value must be a string; "
            "a NaN there is equal to nothing, itself included, so a cell would match none of "
            "its own neighbourhood and a neighbourhood of one would report NOT degenerate"
        )

    parameters = _parameter_columns(results)
    if not parameters:
        raise ValueError("results carries no parameter column; there is no axis to judge along")
    if set(cell) != set(parameters):
        raise ValueError(
            f"cell names {sorted(cell)} but the results frame's parameters are "
            f"{sorted(parameters)}; a cell is a value for every one of them"
        )
    _validate_grid(results, parameters)

    wanted = tuple(cell[name] for name in parameters)
    rows = [
        index
        for index, row in enumerate(results[parameters].itertuples(index=False, name=None))
        if row == wanted
    ]
    if not rows:
        raise ValueError(f"cell {cell} is not present in results")

    neighbourhood = _neighbourhoods(results, parameters, radius)[rows[0]]
    own = fingerprints[rows[0]]
    matching = sum(1 for index in neighbourhood if fingerprints[index] == own)
    return matching / len(neighbourhood) >= DEGENERATE_SHARE

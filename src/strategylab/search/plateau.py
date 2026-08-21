"""Plateau scoring: preferring broad regions to isolated peaks.

The best single cell in a grid is usually the luckiest one. A parameter setting
worth trading is surrounded by other settings that also work, because that is
what it means for an edge to be insensitive to a number nobody can know
precisely in advance.
"""

from __future__ import annotations

import pandas as pd


def plateau_score(results: pd.DataFrame, radius: int = 1) -> pd.DataFrame:
    """Score each grid cell by the median of its parameter neighbourhood.

    A cell is scored by the MEDIAN of the cells within ``radius`` steps of it
    along every parameter axis, not by its own result, so isolated spikes lose
    to broad regions. The median rather than the mean, so one adjacent outlier
    cannot carry a neighbourhood.

    Args:
        results: One row per grid cell. Carries a column per searched parameter
            holding that cell's value, plus the metric being scored.
        radius: Neighbourhood half-width in GRID STEPS, not in parameter units,
            so a radius of ``1`` means the immediately adjacent cells on each
            axis regardless of how coarse the step is.

    Returns:
        ``results`` with a ``plateau_score`` column added, holding the
        neighbourhood median for each cell. Cells at the grid boundary are
        scored over the neighbours they actually have, and the count is
        reported in a ``neighbour_count`` column so a thin neighbourhood is
        visible rather than assumed.

    Raises:
        ValueError: If ``results`` is empty, lacks the metric column, or
            ``radius`` is negative.
    """
    raise NotImplementedError


def is_edge_winner(cell: dict[str, int], search_spec: dict[str, dict[str, float]]) -> bool:
    """Report whether a winning cell sits on a boundary of the searched grid.

    A winner on the edge is a wall, not an optimum: the surface was still
    improving when the range ran out, so the true best value lies outside what
    was searched. The caller must reject it and widen the range rather than
    trade it.

    Args:
        cell: The winning combination, mapping parameter name to value.
        search_spec: The ranges that were searched, in the shape
            :func:`strategylab.search.grid.expand_grid` accepts.

    Returns:
        ``True`` if any parameter sits at the minimum or maximum of its
        searched range.

    Raises:
        ValueError: If ``cell`` names a parameter absent from ``search_spec``.
    """
    raise NotImplementedError


def is_degenerate(results: pd.DataFrame, cell: dict[str, int], radius: int = 1) -> bool:
    """Report whether a cell's neighbourhood is flat through insensitivity.

    Flatness from parameter insensitivity is NOT robustness and must not be
    reported as a plateau. If neighbouring cells produce near-identical trade
    sets, the parameters are not doing anything, and the smooth surface is an
    artefact rather than evidence that the edge is stable.

    Args:
        results: One row per grid cell, as passed to :func:`plateau_score`, and
            carrying enough per-cell trade information to compare trade sets.
        cell: The combination whose neighbourhood is being judged.
        radius: Neighbourhood half-width in grid steps.

    Returns:
        ``True`` if the neighbourhood's trade sets are near-identical, meaning
        the plateau is degenerate.

    Raises:
        ValueError: If ``results`` lacks the trade information the comparison
            needs, or ``cell`` is not present in ``results``.
    """
    raise NotImplementedError

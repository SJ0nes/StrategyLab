"""The search surface as a heatmap: the report a search actually needs.

WHY A SURFACE AND NOT A WINNER. ``docs/decisions/0001`` chose an exhaustive grid
over an iterative search precisely because "the question this pipeline asks is
not which parameters performed best. It is whether the good region is broad or
is a spike." That question is answered by the SHAPE of the results across the
parameter space, and a table of the top ten cells cannot show a shape. This
module draws all of them.

WHAT THE COLOUR MEANS, AND WHY IT IS NOT THE SCORE. Cells are coloured by their
OWN metric, not by ``plateau_score``. The plateau score is already a
neighbourhood median - a smoothed surface - and colouring by it would make every
region look like a plateau, erasing the spike-versus-plateau distinction the
figure exists to show. The score is in the hover, where it belongs: the eye gets
the honest surface, and the number that decided the winner is one hover away.

THE NOISE CEILING IS DRAWN ON THE SAME AXIS AS THE RESULT. Below the heatmap
is one horizontal Sharpe axis carrying every cell of the grid, every surrogate's
best-in-grid figure, the band between the surrogates' median and 95th
percentile, the candidate, and buy-and-hold. A candidate inside the band is seen
to be inside it before anything is read, which is what the ``report-design``
standard asks for. The verdict printed above it is the search's own
``noise_gate``, in the search's own sentence - this module places marks and
decides nothing.

When a search was run without ``--trials`` the axis is still drawn, with the
cells, the candidate and the benchmark on it and no band, and the page says that
the band is absent because nothing was computed.

A CELL CLICKS THROUGH TO ITS EQUITY CURVE, drawn beside what holding the
instrument did over the same bars. The curves come from the artifact, where they
are stored thinned to a few hundred points FOR DRAWING. Nothing on the page
recomputes a metric from them; every figure shown is the one the search
recorded from the full curve.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from strategylab.report.palette import ACCENT, FAILED_FILL, VIRIDIS, sample
from strategylab.report.render import DEFAULT_REPORT_DIR, render
from strategylab.search.loop import SEARCH_SCHEMA_VERSION

#: Side of one heatmap cell, in SVG user units.
CELL = 46

#: Space between a panel's edge and its first cell, in SVG user units. Leaves
#: room for the axis labels, which sit outside the grid.
PANEL_PAD = 54

#: Most facet panels this module will draw. A three-parameter grid facets into
#: one panel per value of its third parameter, and beyond a couple of dozen the
#: page stops being a figure and starts being a contact sheet nobody reads.
MAX_PANELS = 24

#: Width of the Sharpe axis figure, in SVG user units.
AXIS_WIDTH = 760

#: Space at each end of that axis, so a mark at an extreme is not clipped.
AXIS_PAD = 20

#: The template this module renders. Resolved against the package's templates
#: directory by :func:`~strategylab.report.render.render`.
SURFACE_TEMPLATE = "surface.html.j2"


class ReportError(Exception):
    """A report could not be built from the result it was handed.

    Distinct from a rendering failure. This means the artifact is not something
    a surface can be drawn from - too few parameters, too many facets, a schema
    this module does not know.
    """


def _axis_values(cells: list[dict[str, Any]], name: str) -> list[int]:
    """Return the ascending distinct values a parameter takes across the cells.

    Args:
        cells: The artifact's cell list.
        name: Parameter name.

    Returns:
        Every distinct value, ascending. Position in this list is what places a
        cell on the axis, so a grid missing a value simply has a shorter axis
        rather than a gap - the search itself refuses an incomplete grid, and
        this module trusts that rather than re-checking it.
    """
    return sorted({int(cell["params"][name]) for cell in cells})


def _normalise(value: float, low: float, high: float) -> float:
    """Place a metric on the 0-1 ramp position for its value.

    Args:
        value: The metric, in its own units.
        low: The lowest finite metric on the surface, same units.
        high: The highest finite metric on the surface, same units.

    Returns:
        The ramp position as a FRACTION. A surface where every finite cell holds
        the same value has no range to spread across and maps to the middle of
        the ramp rather than dividing by zero - the flat colour is then honest,
        because the surface really is flat.
    """
    if high <= low:
        return 0.5
    return (value - low) / (high - low)


def build_context(artifact: dict[str, Any], command: str) -> dict[str, Any]:
    """Turn a search artifact into everything the surface template needs.

    THE FIRST TWO PARAMETERS ARE THE HEATMAP AXES and the rest facet it, in the
    order the artifact lists them - which is the order the config's ``search``
    block declares. For the committed strategy that is ``fast`` across,
    ``slow`` down, one panel per ``trend``. The rule is arbitrary but it is
    stated, and it is stable: the same config always produces the same layout,
    so two runs can be compared by eye.

    Args:
        artifact: A parsed search artifact, as
            :func:`~strategylab.search.loop.write_search_result` writes it.
        command: The exact command that produced the artifact, drawn on the
            figure. A figure that cannot say where it came from cannot be used
            to make a decision or put in front of anyone else.

    Returns:
        The template context. Carries the three traceability keys
        :func:`~strategylab.report.render.render` requires, plus the panels,
        the legend and the summary counts.

    Raises:
        ReportError: If the artifact declares a schema version this reader does
            not understand, carries no cells, declares no searched parameters,
            or would facet into more than :data:`MAX_PANELS` panels.
        KeyError: If the artifact is missing a field this module reads. Not
            caught and re-raised as something friendlier, because a
            well-formed artifact always has them and a malformed one is a
            producer bug worth seeing raw.
    """
    version = artifact.get("schema_version")
    if version != SEARCH_SCHEMA_VERSION:
        raise ReportError(
            f"this artifact declares schema version {version!r} and this reader understands "
            f"{SEARCH_SCHEMA_VERSION}. Re-run the search rather than drawing a surface from "
            "a file whose fields may mean something else - an audit pointed out that nothing "
            "read this field at all, which made it decoration rather than a safeguard"
        )

    cells = artifact["cells"]
    if not cells:
        raise ReportError("the artifact carries no cells; there is no surface to draw")

    parameters = list(cells[0]["params"])
    if not parameters:
        raise ReportError("the artifact's cells declare no parameters; there are no axes")

    across = parameters[0]
    down = parameters[1] if len(parameters) > 1 else None
    facets = parameters[2:]

    facet_values = [_axis_values(cells, name) for name in facets]
    panel_count = math.prod(len(values) for values in facet_values) if facet_values else 1
    if panel_count > MAX_PANELS:
        raise ReportError(
            f"this grid facets into {panel_count} panels, above the {MAX_PANELS} this "
            "report draws; a page of that many is a contact sheet rather than a figure"
        )

    metric = artifact["primary_metric"]
    finite = [cell[metric] for cell in cells if cell[metric] is not None]
    low = min(finite) if finite else 0.0
    high = max(finite) if finite else 0.0

    winner_params = artifact["winner"]["params"] if artifact["winner"] else None
    rejected = {
        tuple(sorted(rejection["params"].items())): rejection["reason"]
        for rejection in artifact["rejections"]
    }

    across_values = _axis_values(cells, across)
    down_values = _axis_values(cells, down) if down else [0]

    panels: list[dict[str, Any]] = []
    for facet_key in _facet_combinations(facets, facet_values):
        members = [
            (position, cell)
            for position, cell in enumerate(cells)
            if all(int(cell["params"][name]) == value for name, value in facet_key.items())
        ]
        panels.append(
            _panel(
                members,
                facet_key,
                across,
                down,
                across_values,
                down_values,
                artifact["primary_metric"],
                low,
                high,
                winner_params,
                rejected,
            )
        )

    return {
        "protocol_version": artifact["protocol_version"],
        "fixture_hash": artifact["data_content_hash"],
        "command": command,
        "generated_at": artifact["generated_at"],
        "strategy": artifact["strategy"],
        "config": artifact["config"],
        "instrument": artifact["instrument"],
        "window": artifact["window"],
        "metric": artifact["primary_metric"],
        "across": across,
        "down": down,
        "panels": panels,
        "legend": _legend(low, high),
        "winner": artifact["winner"],
        "outcomes": _outcomes(artifact),
        "grid_size": artifact["grid_size"],
        "failed_cells": artifact["failed_cells"],
        "costs_frac": artifact["costs_frac"],
        "max_drawdown_ceiling_pct": artifact["max_drawdown_ceiling_pct"],
        "plateau_radius": artifact["plateau_radius"],
        "noise_ceiling": artifact["noise_ceiling"],
        "noise_ceiling_note": artifact["noise_ceiling_note"],
        "noise_gate": artifact["noise_gate"],
        "benchmark": artifact["benchmark"],
        "sharpe_axis": _sharpe_axis(artifact),
        "curves": artifact["equity_curves"],
        "accent": ACCENT,
        "failed_fill": FAILED_FILL,
        "panel_width": PANEL_PAD + len(across_values) * CELL + 16,
        "panel_height": PANEL_PAD + len(down_values) * CELL + 16,
        "cell": CELL,
        "pad": PANEL_PAD,
    }


def _facet_combinations(names: list[str], values: list[list[int]]) -> list[dict[str, int]]:
    """Enumerate every combination of the facet parameters, in ascending order.

    Args:
        names: Facet parameter names, in artifact order.
        values: Each facet's ascending distinct values, positionally matched to
            ``names``.

    Returns:
        One mapping per panel. A grid with no facet parameters yields a single
        empty mapping, which is one panel covering the whole surface.
    """
    combinations: list[dict[str, int]] = [{}]
    for name, options in zip(names, values, strict=True):
        combinations = [
            {**existing, name: option} for existing in combinations for option in options
        ]
    return combinations


def _panel(
    members: list[tuple[int, dict[str, Any]]],
    facet: dict[str, int],
    across: str,
    down: str | None,
    across_values: list[int],
    down_values: list[int],
    metric: str,
    low: float,
    high: float,
    winner_params: dict[str, int] | None,
    rejected: dict[tuple[tuple[str, int], ...], str],
) -> dict[str, Any]:
    """Build one heatmap panel.

    Args:
        members: The cells belonging to this facet, each with its position in
            the artifact's own cell list. The position is what finds a cell's
            equity curve, which the artifact stores in that same order.
        facet: The facet values this panel holds fixed, mapping name to value
            in bars.
        across: Parameter on the horizontal axis.
        down: Parameter on the vertical axis, or ``None`` for a one-parameter
            grid, which draws as a single row.
        across_values: Ascending values of ``across``, shared by every panel so
            the panels align.
        down_values: The same for ``down``.
        metric: Name of the metric being coloured.
        low: Lowest finite metric across the WHOLE surface, not this panel.
        high: Highest finite metric across the whole surface. Shared so that
            colour means the same thing in every panel - per-panel scaling
            would make a weak panel look identical to a strong one.
        winner_params: The winning cell, or ``None`` if none survived.
        rejected: Reason by cell, for every rejection the search recorded.

    Returns:
        The panel: its title, its axis ticks, and one drawable cell per member.
    """
    cells: list[dict[str, Any]] = []
    for position, member in members:
        value = member[metric]
        column = across_values.index(int(member["params"][across]))
        row = down_values.index(int(member["params"][down])) if down else 0
        key = tuple(sorted((name, int(v)) for name, v in member["params"].items()))
        cells.append(
            {
                "x": PANEL_PAD + column * CELL,
                "y": PANEL_PAD + row * CELL,
                "fill": FAILED_FILL if value is None else sample(_normalise(value, low, high)),
                "failed": value is None,
                "index": position,
                "value": value,
                "params": member["params"],
                "label": ", ".join(f"{name}={v}" for name, v in member["params"].items()),
                "plateau_score": member["plateau_score"],
                "max_drawdown_pct": member["max_drawdown_pct"],
                "trade_count": member["trade_count"],
                "neighbour_count": member["neighbour_count"],
                "traded": not member["trade_fingerprint"].startswith("no-trades"),
                "rejected": rejected.get(key),
                "winner": winner_params is not None
                and all(int(winner_params[name]) == int(v) for name, v in member["params"].items()),
            }
        )

    title = ", ".join(f"{name} = {value}" for name, value in facet.items()) or "all cells"
    return {
        "title": title,
        "cells": cells,
        "across_ticks": [
            {"x": PANEL_PAD + index * CELL + CELL / 2, "label": str(value)}
            for index, value in enumerate(across_values)
        ],
        "down_ticks": [
            {"y": PANEL_PAD + index * CELL + CELL / 2, "label": str(value)}
            for index, value in enumerate(down_values)
        ]
        if down
        else [],
    }


def _legend(low: float, high: float) -> dict[str, Any]:
    """Build the colour legend: the ramp, and what its ends mean.

    Args:
        low: Lowest finite metric on the surface, in the metric's units.
        high: Highest finite metric, same units.

    Returns:
        The ramp stops as CSS colours with their percentage offsets, plus the
        two labels.
    """
    return {
        "low": low,
        "high": high,
        "stops": [
            {
                "offset": round(index / (len(VIRIDIS) - 1) * 100, 2),
                "colour": sample(index / (len(VIRIDIS) - 1)),
            }
            for index in range(len(VIRIDIS))
        ],
    }


def _axis_x(value: float, low: float, high: float) -> float:
    """Place a Sharpe on the horizontal axis.

    Args:
        value: The Sharpe to place, a dimensionless ratio.
        low: The Sharpe at the axis's left end.
        high: The Sharpe at its right end.

    Returns:
        The horizontal position in SVG user units, rounded to two places. The
        ends sit :data:`AXIS_PAD` in from the figure's edges. An axis with no
        range puts everything at its centre rather than dividing by zero.
    """
    span = AXIS_WIDTH - 2 * AXIS_PAD
    return round(AXIS_PAD + _normalise(value, low, high) * span, 2)


def _sharpe_axis(artifact: dict[str, Any]) -> dict[str, Any]:
    """Lay every Sharpe the artifact holds along one axis.

    ONE SCALE FOR EVERYTHING, which is the point: the cells, the surrogates, the
    candidate and buy-and-hold are only comparable by eye if a unit of distance
    means the same for each. The scale runs from the lowest to the highest value
    present, and always includes zero, because "is this above nothing at all" is
    a question the figure should answer without a label.

    Args:
        artifact: The parsed search artifact.

    Returns:
        ``low`` and ``high``, the Sharpes at the two ends; ``zero``, where a
        Sharpe of nought falls; ``cells`` and ``trials``, the positions of every
        finite cell and every scoreable surrogate; ``band``, the positions of the
        surrogates' median and 95th percentile, or ``None`` with no ceiling;
        ``winner`` and ``benchmark``, each a position and a value, or ``None``.
    """
    metric = artifact["primary_metric"]
    cells = [cell[metric] for cell in artifact["cells"] if cell[metric] is not None]
    ceiling = artifact["noise_ceiling"]
    trials = [v for v in (ceiling["trial_best_sharpe"] if ceiling else []) if v is not None]
    winner = artifact["winner"][metric] if artifact["winner"] else None
    benchmark = artifact["benchmark"][metric] if artifact["benchmark"] else None

    present = [*cells, *trials, 0.0]
    present += [value for value in (winner, benchmark) if value is not None]
    low, high = min(present), max(present)

    band = None
    if ceiling and ceiling["percentiles"]["p50"] is not None:
        band = {
            "p50": _axis_x(ceiling["percentiles"]["p50"], low, high),
            "p95": _axis_x(ceiling["percentiles"]["p95"], low, high),
            "p95_value": ceiling["percentiles"]["p95"],
        }

    def mark(value: float | None) -> dict[str, float] | None:
        return None if value is None else {"x": _axis_x(value, low, high), "value": value}

    return {
        "low": low,
        "high": high,
        "zero": _axis_x(0.0, low, high),
        "cells": [_axis_x(value, low, high) for value in cells],
        "trials": [_axis_x(value, low, high) for value in trials],
        "band": band,
        "winner": mark(winner),
        "benchmark": mark(benchmark),
        "width": AXIS_WIDTH,
        "pad": AXIS_PAD,
    }


def _outcomes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """Partition the grid by what became of each cell.

    A PARTITION, SUMMING TO THE GRID, and that is the whole point. An earlier
    version listed ``failed_cells`` and then every rejection reason beneath it -
    but a failed cell IS a rejection, recorded under ``backtest_failed``, so it
    appeared twice in a table that ends with the grid total and therefore reads
    as a partition. A reader adding the rows got more cells than the search ran.

    "Not examined" is the row that makes it add up, and it is honest rather than
    residual: :func:`~strategylab.search.loop.select_candidate` walks the ranked
    cells and STOPS at the first survivor, so every cell below the winner was
    never looked at. Calling those "kept" would say they passed checks they
    never faced.

    Args:
        artifact: The parsed search artifact.

    Returns:
        One entry per outcome, each with a ``label`` and a ``count``, ordered
        failures first, then rejections by count descending and reason
        ascending, then the candidate, then whatever was never reached. Every
        cell of the grid appears in exactly one row.
    """
    counts: dict[str, int] = {}
    for rejection in artifact["rejections"]:
        counts[rejection["reason"]] = counts.get(rejection["reason"], 0) + 1

    failed = counts.pop("backtest_failed", 0)
    rows = [{"label": "failed to backtest", "count": failed}]
    rows += [
        {"label": f"rejected: {reason}", "count": count}
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    chosen = 1 if artifact["winner"] else 0
    rows.append({"label": "candidate", "count": chosen})
    accounted = failed + sum(counts.values()) + chosen
    rows.append({"label": "not examined", "count": artifact["grid_size"] - accounted})
    return [row for row in rows if row["count"]]


def render_surface(
    artifact_path: Path,
    out_dir: Path = DEFAULT_REPORT_DIR,
    command: str | None = None,
) -> Path:
    """Read one search artifact and draw its surface as a self-contained page.

    Args:
        artifact_path: The ``state/search-<stamp>.json`` to read.
        out_dir: Directory to write into. Created if absent.
        command: The command that produced the artifact, drawn on the figure.
            Defaults to the ``strategylab search`` invocation implied by the
            artifact's own recorded config path, which is what produced it.

    Returns:
        The path written, named after the artifact so a report and its result
        can never be confused for a different pair.

    Raises:
        ReportError: If the artifact cannot be drawn - see
            :func:`build_context`.
        FileNotFoundError: If ``artifact_path`` does not exist.
        json.JSONDecodeError: If it is not JSON.
    """
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    context = build_context(artifact, command or f"uv run strategylab search {artifact['config']}")
    return render(SURFACE_TEMPLATE, context, out_dir / f"{artifact_path.stem}.html")

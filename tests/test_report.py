"""Tests for the HTML reports: the renderer, the colour ramp, and the surface.

EVERY EXPECTED NUMBER HERE IS DERIVED FROM A RULE STATED BESIDE IT. The two
rules that carry almost all of them are written out once, so a reader checking
the arithmetic never has to open `src/`.

**Cell geometry.** A cell is `CELL` units square. The cell at column `c`, row
`r` of a panel has its top-left corner at

    x = PANEL_PAD + c * CELL
    y = PANEL_PAD + r * CELL

with `c` the position of that cell's `across` value in the ascending distinct
values of that axis, and `r` the same for `down`. A panel is therefore

    width  = PANEL_PAD + len(across_values) * CELL + 16
    height = PANEL_PAD + len(down_values)   * CELL + 16

where the trailing 16 is the right and bottom margin. With `CELL = 46` and
`PANEL_PAD = 54`, a 6 x 5 grid is 54 + 276 + 16 = 346 by 54 + 230 + 16 = 300,
and the first row sits at x = 54, 100, 146, 192, 238, 284.

**Ramp position.** A metric `v` is placed on the ramp at

    t = (v - low) / (high - low)

clamped to [0, 1], where `low` and `high` are the lowest and highest FINITE
metrics across the whole surface - not per panel, so a colour means the same
thing in every panel. `t` is then scaled across `len(VIRIDIS) - 1 = 9`
intervals, and the colour is a linear interpolation between the two stops it
falls between. A surface with no range maps everything to `t = 0.5`.

**What no test here claims.** Nothing in this file is evidence about the
strategy. The surface tests build their own artifacts; the one test that reads
a real search artifact asserts shape, not performance.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from strategylab.report.palette import ACCENT, FAILED_FILL, VIRIDIS, sample
from strategylab.report.render import TEMPLATE_DIR, TRACEABILITY_KEYS, render
from strategylab.report.surface import (
    CELL,
    MAX_PANELS,
    PANEL_PAD,
    ReportError,
    build_context,
    render_surface,
)

TRACEABLE: dict[str, Any] = {
    "protocol_version": 1,
    "fixture_hash": "not-a-real-snapshot-hash-000000000000000000000000000000000000",
    "command": "uv run strategylab search configs/small.yaml",
}


# --------------------------------------------------------------------------
# The colour ramp.
# --------------------------------------------------------------------------


def test_the_ramp_ends_are_the_first_and_last_stops() -> None:
    # Position 1.0 must land exactly on the last stop rather than one past it,
    # which is what `t * (len - 1)` buys over `t * len`.
    first = VIRIDIS[0]
    last = VIRIDIS[-1]
    assert sample(0.0) == "#" + "".join(f"{channel:02x}" for channel in first)
    assert sample(1.0) == "#" + "".join(f"{channel:02x}" for channel in last)


def test_a_stop_position_reproduces_that_stop_exactly() -> None:
    # There are 9 intervals between 10 stops, so stop `i` sits at i/9 and the
    # interpolation there has fraction 0 - it must return the stop untouched.
    for index, stop in enumerate(VIRIDIS):
        expected = "#" + "".join(f"{channel:02x}" for channel in stop)
        assert sample(index / (len(VIRIDIS) - 1)) == expected


def test_a_midpoint_between_two_stops_is_their_average() -> None:
    # Halfway between stop 0 (68, 1, 84) and stop 1 (72, 40, 120) is
    # (70.0, 20.5, 102.0), which rounds to (70, 20, 102) under banker's
    # rounding - Python rounds 20.5 to the even 20 - giving #461466.
    halfway = 0.5 / (len(VIRIDIS) - 1)
    assert sample(halfway) == "#461466"


def test_the_ramp_rises_monotonically_in_lightness() -> None:
    # The property that makes it safe for this figure: no manufactured edges.
    # Approximate luminance, ITU-R BT.601 coefficients.
    def luma(colour: str) -> float:
        red, green, blue = (int(colour[index : index + 2], 16) for index in (1, 3, 5))
        return 0.299 * red + 0.587 * green + 0.114 * blue

    steps = [luma(sample(index / 40)) for index in range(41)]
    assert all(later >= earlier - 1e-9 for earlier, later in zip(steps, steps[1:], strict=False))


@pytest.mark.parametrize("position", [-0.5, 1.5, -1e9, 1e9])
def test_a_position_outside_the_range_is_clamped(position: float) -> None:
    # A caller normalising against a min and max can land a hair outside through
    # floating point, and that is not worth an exception.
    assert sample(position) in {sample(0.0), sample(1.0)}


def test_a_nan_position_is_refused_rather_than_coloured() -> None:
    # Colouring it at the bottom of the ramp would paint a cell that never ran
    # as a merely poor one, which is the single most misleading thing this
    # figure could do.
    with pytest.raises(ValueError, match="NaN"):
        sample(math.nan)


def test_a_non_number_position_is_refused() -> None:
    with pytest.raises(ValueError, match="not a number"):
        sample("0.5")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The renderer.
# --------------------------------------------------------------------------


def _template(tmp_path: Path, body: str) -> str:
    """Write a throwaway template into the package's template directory.

    Returns its name. The renderer resolves against the package rather than a
    caller-supplied directory - deliberately, so a report renders identically
    from anywhere - so a test exercising it has to put its template there.
    """
    name = f"_probe_{abs(hash(body)) % 10**8}.html.j2"
    path = TEMPLATE_DIR / name
    path.write_text(body, encoding="utf-8")
    return name


@pytest.fixture(autouse=True)
def _clean_probe_templates() -> Any:
    """Remove any probe template a test wrote, however the test ended."""
    yield
    for path in TEMPLATE_DIR.glob("_probe_*.html.j2"):
        path.unlink()


def test_render_writes_the_page_and_creates_its_directory(tmp_path: Path) -> None:
    name = _template(tmp_path, "<p>{{ command }}</p>")
    out = tmp_path / "nested" / "deeper" / "page.html"
    written = render(name, TRACEABLE, out)

    assert written == out
    assert out.read_text(encoding="utf-8") == "<p>uv run strategylab search configs/small.yaml</p>"


@pytest.mark.parametrize("omitted", TRACEABILITY_KEYS)
def test_a_context_missing_a_traceability_key_is_refused(tmp_path: Path, omitted: str) -> None:
    # A figure that cannot say where it came from cannot be used to make a
    # decision, so this is refused at render time rather than left to a
    # template that would emit an empty span.
    name = _template(tmp_path, "<p>x</p>")
    context = {key: value for key, value in TRACEABLE.items() if key != omitted}
    with pytest.raises(ValueError, match=omitted):
        render(name, context, tmp_path / "page.html")
    assert not (tmp_path / "page.html").exists()


def test_a_missing_template_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no template named"):
        render("does-not-exist.html.j2", TRACEABLE, tmp_path / "page.html")


@pytest.mark.parametrize(
    "body",
    [
        '<link rel="stylesheet" href="https://cdn.example.com/x.css">',
        '<script src="http://cdn.example.com/x.js"></script>',
        '<img src="//cdn.example.com/x.png">',
    ],
)
def test_a_page_reaching_outside_itself_is_refused(tmp_path: Path, body: str) -> None:
    # "Self-contained" is the property the whole format rests on and it decays
    # the first time somebody adds a font link. Checked against the RENDERED
    # output, so a URL smuggled in through the context is caught too.
    name = _template(tmp_path, body)
    with pytest.raises(ValueError, match="outside itself"):
        render(name, TRACEABLE, tmp_path / "page.html")
    assert not (tmp_path / "page.html").exists()


def test_a_relative_reference_is_not_mistaken_for_an_external_one(tmp_path: Path) -> None:
    # An anchor to a fragment is not a network request, and refusing one would
    # be the check overreaching.
    name = _template(tmp_path, '<a href="#surface">jump</a>')
    written = render(name, TRACEABLE, tmp_path / "page.html")
    assert "#surface" in written.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The surface context.
# --------------------------------------------------------------------------


def _artifact(cells: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    """A minimal search artifact carrying the fields the report reads."""
    artifact: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": "2026-08-22T13:05:00+00:00",
        "protocol_version": 1,
        "strategy": "probe",
        "config": "configs/small.yaml",
        "instrument": "SPY",
        "data_content_hash": "not-a-real-snapshot-hash-000000000000000000000000000000000000",
        "window": {"start": "2010-01-01", "end": "2010-06-30", "bars": 124},
        "primary_metric": "sharpe",
        "max_drawdown_ceiling_pct": 25.0,
        "costs_frac": {
            "spread": 0.0003,
            "slippage": 0.0005,
            "commission": 0.0005,
            "fx_conversion": 0.0,
        },
        "commission_min_flat": 0.0,
        "plateau_radius": 1,
        "starting_equity": 10000.0,
        "grid_size": len(cells),
        "failed_cells": sum(1 for cell in cells if cell["sharpe"] is None),
        "noise_ceiling": None,
        "noise_ceiling_note": "Not computed: search/noise.py is a stub.",
        "winner": None,
        "rejections": [],
        "cells": cells,
    }
    artifact.update(overrides)
    return artifact


def _cell(params: dict[str, int], sharpe: float | None, **overrides: Any) -> dict[str, Any]:
    """One artifact cell."""
    cell: dict[str, Any] = {
        "params": params,
        "sharpe": sharpe,
        "max_drawdown_pct": None if sharpe is None else 5.0,
        "trade_count": 0 if sharpe is None else 7,
        "trade_fingerprint": "failed:x" if sharpe is None else "trades:abc",
        "plateau_score": sharpe,
        "neighbour_count": 0 if sharpe is None else 3,
    }
    cell.update(overrides)
    return cell


def _two_axis_cells() -> list[dict[str, Any]]:
    """A 2 x 3 grid: `a` in (1, 2) across, `b` in (10, 20, 30) down."""
    return [
        _cell({"a": a, "b": b}, float(index))
        for index, (a, b) in enumerate([(1, 10), (1, 20), (1, 30), (2, 10), (2, 20), (2, 30)])
    ]


def test_the_first_two_parameters_are_the_axes_and_the_rest_facet() -> None:
    cells = [
        _cell({"a": a, "b": b, "c": c}, 1.0)
        for a in (1, 2)
        for b in (10, 20)
        for c in (100, 200, 300)
    ]
    context = build_context(_artifact(cells), "cmd")

    assert context["across"] == "a"
    assert context["down"] == "b"
    # One panel per value of the third parameter, in ascending order.
    assert [panel["title"] for panel in context["panels"]] == ["c = 100", "c = 200", "c = 300"]
    assert all(len(panel["cells"]) == 4 for panel in context["panels"])


def test_cell_positions_follow_the_stated_geometry() -> None:
    context = build_context(_artifact(_two_axis_cells()), "cmd")
    cells = context["panels"][0]["cells"]

    # `a` across takes (1, 2) -> columns 0, 1. `b` down takes (10, 20, 30) ->
    # rows 0, 1, 2. So a=2, b=30 is column 1, row 2.
    corner = next(c for c in cells if c["params"] == {"a": 2, "b": 30})
    assert corner["x"] == PANEL_PAD + 1 * CELL
    assert corner["y"] == PANEL_PAD + 2 * CELL

    origin = next(c for c in cells if c["params"] == {"a": 1, "b": 10})
    assert (origin["x"], origin["y"]) == (PANEL_PAD, PANEL_PAD)


def test_panel_size_follows_the_stated_geometry() -> None:
    context = build_context(_artifact(_two_axis_cells()), "cmd")
    # 2 across, 3 down.
    assert context["panel_width"] == PANEL_PAD + 2 * CELL + 16
    assert context["panel_height"] == PANEL_PAD + 3 * CELL + 16


def test_colour_spans_the_whole_surface_not_each_panel() -> None:
    # Two panels, one weak and one strong. If colour were scaled per panel the
    # weak panel's best cell would be the ramp's top, and a reader comparing
    # panels would be misled about which region is good.
    cells = [
        _cell({"a": 1, "b": 1, "c": 1}, 0.0),
        _cell({"a": 2, "b": 1, "c": 1}, 1.0),
        _cell({"a": 1, "b": 1, "c": 2}, 9.0),
        _cell({"a": 2, "b": 1, "c": 2}, 10.0),
    ]
    context = build_context(_artifact(cells), "cmd")
    weak_best = context["panels"][0]["cells"][1]["fill"]
    strong_best = context["panels"][1]["cells"][1]["fill"]

    assert context["panels"][0]["cells"][0]["fill"] == sample(0.0)
    assert strong_best == sample(1.0)
    # 1.0 on a 0-10 surface is one tenth of the way up.
    assert weak_best == sample(0.1)
    assert weak_best != strong_best


def test_a_flat_surface_maps_to_the_middle_of_the_ramp() -> None:
    # No range to spread across. The flat colour is honest - the surface really
    # is flat - and it must not divide by zero.
    cells = [_cell({"a": a, "b": 1}, 2.5) for a in (1, 2, 3)]
    context = build_context(_artifact(cells), "cmd")
    assert {c["fill"] for c in context["panels"][0]["cells"]} == {sample(0.5)}


def test_a_failed_cell_is_marked_rather_than_coloured() -> None:
    # It must never read as "nothing here", and it must never be coloured at
    # the bottom of the ramp, which reads as a poor result.
    cells = [_cell({"a": 1, "b": 1}, 1.0), _cell({"a": 2, "b": 1}, None)]
    context = build_context(_artifact(cells), "cmd")
    failed = context["panels"][0]["cells"][1]

    assert failed["failed"] is True
    assert failed["fill"] == FAILED_FILL
    assert failed["fill"] != sample(0.0)
    assert failed["value"] is None


def test_the_winner_and_the_rejections_are_marked() -> None:
    cells = _two_axis_cells()
    artifact = _artifact(
        cells,
        winner={
            "params": {"a": 2, "b": 20},
            "sharpe": 4.0,
            "max_drawdown_pct": 5.0,
            "plateau_score": 4.0,
            "neighbour_count": 3,
            "degenerate": False,
        },
        rejections=[
            {"params": {"a": 1, "b": 10}, "reason": "edge_winner", "detail": "d"},
            {"params": {"a": 2, "b": 30}, "reason": "drawdown_ceiling", "detail": "d"},
        ],
    )
    context = build_context(artifact, "cmd")
    drawn = context["panels"][0]["cells"]

    assert [c["params"] for c in drawn if c["winner"]] == [{"a": 2, "b": 20}]
    assert {c["rejected"] for c in drawn if c["rejected"]} == {
        "edge_winner",
        "drawdown_ceiling",
    }
    assert context["rejection_counts"] == [
        {"reason": "drawdown_ceiling", "count": 1},
        {"reason": "edge_winner", "count": 1},
    ]


def test_a_one_parameter_grid_draws_as_a_single_row() -> None:
    cells = [_cell({"a": a}, float(a)) for a in (1, 2, 3)]
    context = build_context(_artifact(cells), "cmd")

    assert context["down"] is None
    assert context["panels"][0]["down_ticks"] == []
    assert {c["y"] for c in context["panels"][0]["cells"]} == {PANEL_PAD}


def test_an_artifact_with_no_cells_is_refused() -> None:
    with pytest.raises(ReportError, match="no cells"):
        build_context(_artifact([]), "cmd")


def test_a_grid_facetting_into_too_many_panels_is_refused() -> None:
    # A page of that many is a contact sheet rather than a figure.
    panels = MAX_PANELS + 1
    cells = [_cell({"a": 1, "b": 1, "c": c}, 1.0) for c in range(panels)]
    with pytest.raises(ReportError, match=f"{panels} panels"):
        build_context(_artifact(cells), "cmd")


def test_the_context_carries_every_traceability_key() -> None:
    # The renderer refuses a context without them, so the surface builder must
    # supply them or no surface report can ever be produced.
    context = build_context(_artifact(_two_axis_cells()), "the command")
    assert all(key in context for key in TRACEABILITY_KEYS)
    assert (
        context["fixture_hash"] == "not-a-real-snapshot-hash-000000000000000000000000000000000000"
    )
    assert context["command"] == "the command"
    assert context["accent"] == ACCENT


# --------------------------------------------------------------------------
# End to end.
# --------------------------------------------------------------------------


def test_a_surface_renders_to_a_self_contained_page(tmp_path: Path) -> None:
    artifact = _artifact(
        _two_axis_cells(),
        winner={
            "params": {"a": 2, "b": 20},
            "sharpe": 4.0,
            "max_drawdown_pct": 5.0,
            "plateau_score": 4.0,
            "neighbour_count": 3,
            "degenerate": True,
        },
    )
    path = tmp_path / "search-probe.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    out = render_surface(path, tmp_path / "reports")
    html = out.read_text(encoding="utf-8")

    # Named after the artifact, so a report and its result cannot be confused
    # for a different pair.
    assert out.name == "search-probe.html"
    # One drawable rect per cell.
    assert html.count('class="cellrect"') == len(artifact["cells"])
    # The traceability is on the figure, not in a sibling file.
    assert artifact["data_content_hash"] in html
    assert "uv run strategylab search configs/small.yaml" in html
    assert f"v{artifact['protocol_version']}" in html
    # The absent noise ceiling is stated where the band would be.
    assert "No noise ceiling was computed" in html
    # A degenerate winner is flagged rather than presented as clean.
    assert "degenerate plateau" in html


def test_the_rendered_page_makes_no_external_request(tmp_path: Path) -> None:
    # `render` enforces this, but the surface template is the one page this
    # project actually ships, so it is pinned here too.
    path = tmp_path / "search-probe.json"
    path.write_text(json.dumps(_artifact(_two_axis_cells())), encoding="utf-8")
    html = render_surface(path, tmp_path / "reports").read_text(encoding="utf-8")

    for marker in ("http://", "https://", "//cdn", "<script src", "<link "):
        assert marker not in html, f"the page references {marker!r}"

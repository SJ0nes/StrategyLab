"""A validation verdict as a page: the folds side by side, and the bar they were held to.

WHY SIDE BY SIDE. A walk-forward exists to show whether a result holds up year
after year or came from one of them. Six numbers in a table have to be read and
compared; six bars on one scale are a shape, and one tall fold among five flat
ones is seen at once. The ``report-design`` standard asks for exactly that.

EVERY OUTCOME CARRIES THE SAME WEIGHT. ``insufficient_data`` is not a soft
failure and is never drawn as an empty space: a fold that traded too little to
judge gets its own panel, its own label and its own hatching, as large as a
pass. A fold whose Sharpe could not be measured at all is drawn the same way and
says so, rather than as a bar of height zero - which would read as a measured
nothing.

WHAT THE POOLED VERDICT IS READ AGAINST. The page puts three things beside it
that the verdict itself does not weigh: the candidate's in-sample Sharpe, which
the held-back figure is expected to fall short of; what holding the instrument
did over the same bars; and the search's noise-gate counts. None of them changes
the outcome. They are there because a ``pass`` means "cleared a pre-registered
bar", and a reader deciding what that is worth needs the other three.

This module places marks and decides nothing. Every figure is the validation's
own, read from its artifact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from strategylab.report.palette import ACCENT
from strategylab.report.render import DEFAULT_REPORT_DIR, render
from strategylab.validate.walkforward import VALIDATION_SCHEMA_VERSION

#: Width of one fold's panel, in SVG user units.
FOLD_WIDTH = 132

#: Height of the area a fold's bar is drawn in, in SVG user units.
CHART_HEIGHT = 180

#: Space above and below that area, for the fold's label and its figures.
CHART_TOP = 28

#: Width of a fold's bar, centred in its panel.
BAR_WIDTH = 56

#: The template this module renders.
VERDICT_TEMPLATE = "verdict.html.j2"

#: The outcomes a fold or a validation can carry, in the artifact's spelling.
OUTCOMES = frozenset({"pass", "fail", "insufficient_data"})


class VerdictReportError(Exception):
    """A verdict page could not be built from the artifact it was handed."""


def _y(value: float, low: float, high: float) -> float:
    """Place a Sharpe on the vertical axis.

    Args:
        value: The Sharpe to place, a dimensionless ratio.
        low: The Sharpe at the bottom of the chart area.
        high: The Sharpe at its top.

    Returns:
        The vertical position in SVG user units, rounded to two places, measured
        down from the top of the figure. A higher Sharpe is a smaller number. A
        scale with no range puts everything at the middle.
    """
    share = 0.5 if high <= low else (value - low) / (high - low)
    return round(CHART_TOP + (1.0 - share) * CHART_HEIGHT, 2)


def build_verdict_context(artifact: dict[str, Any], command: str) -> dict[str, Any]:
    """Turn a validation artifact into everything the verdict template needs.

    ONE VERTICAL SCALE FOR EVERY FOLD, running from the lowest to the highest
    Sharpe on the page and always including both zero and the bar the candidate
    had to clear. Per-fold scaling would make a weak fold look like a strong
    one, and a scale that left out the bar would hide how far short a fold fell.

    Args:
        artifact: A parsed validation artifact, as
            :func:`~strategylab.validate.walkforward.write_validation_result`
            writes it.
        command: The exact command that produced the artifact, drawn on the
            page.

    Returns:
        The template context, carrying the three traceability keys
        :func:`~strategylab.report.render.render` requires.

    Raises:
        VerdictReportError: If the artifact declares a schema version this
            reader does not understand, or an outcome that is not one of
            :data:`OUTCOMES`.
        KeyError: If the artifact is missing a field this module reads. A
            well-formed artifact always has them.
    """
    version = artifact.get("schema_version")
    if version != VALIDATION_SCHEMA_VERSION:
        raise VerdictReportError(
            f"this artifact declares schema version {version!r} and this reader understands "
            f"{VALIDATION_SCHEMA_VERSION}. A verdict cannot be re-derived without spending "
            "another read of the held-back window, so it is not redrawn from a shape this "
            "code does not know"
        )
    outcomes = [artifact["outcome"], *(fold["outcome"] for fold in artifact["folds"])]
    unknown = sorted({outcome for outcome in outcomes if outcome not in OUTCOMES})
    if unknown:
        raise VerdictReportError(
            f"this artifact carries the outcome(s) {unknown}, which are not among "
            f"{sorted(OUTCOMES)}; drawing one would mean guessing which weight to give it"
        )

    bar = artifact["criteria"]["effective_sharpe_bar"]
    pooled = artifact["pooled"]["sharpe"]
    held = artifact["benchmark"]["sharpe"] if artifact["benchmark"] else None
    measured = [fold["sharpe"] for fold in artifact["folds"] if fold["sharpe"] is not None]
    present = [0.0, bar, *measured]
    present += [value for value in (pooled, held) if value is not None]
    low, high = min(present), max(present)

    folds: list[dict[str, Any]] = []
    for position, fold in enumerate(artifact["folds"]):
        sharpe = fold["sharpe"]
        left = position * FOLD_WIDTH
        top = None if sharpe is None else min(_y(sharpe, low, high), _y(0.0, low, high))
        folds.append(
            {
                **fold,
                "left": left,
                "bar_x": left + (FOLD_WIDTH - BAR_WIDTH) / 2,
                "bar_y": top,
                "bar_height": None
                if sharpe is None
                else round(abs(_y(sharpe, low, high) - _y(0.0, low, high)), 2),
                "measured": sharpe is not None,
            }
        )

    return {
        "protocol_version": artifact["protocol_version"],
        "fixture_hash": artifact["data_content_hash"],
        "command": command,
        "generated_at": artifact["generated_at"],
        "strategy": artifact["strategy"],
        "config": artifact["config"],
        "config_content_hash": artifact["config_content_hash"],
        "source_search": artifact["source_search"],
        "instrument": artifact["instrument"],
        "params": artifact["params"],
        "window": artifact["window"],
        "warmup_bars": artifact["warmup_bars"],
        "criteria": artifact["criteria"],
        "costs_frac": artifact["costs_frac"],
        "outcome": artifact["outcome"],
        "reason": artifact["reason"],
        "pooled": artifact["pooled"],
        "in_sample": artifact["in_sample"],
        "benchmark": artifact["benchmark"],
        "noise_gate": artifact["noise_gate"],
        "noise_ceiling_note": artifact["noise_ceiling_note"],
        "walk_forward_touches": artifact["walk_forward_touches"],
        "folds": folds,
        "scale": {
            "low": low,
            "high": high,
            "zero_y": _y(0.0, low, high),
            "bar_y": _y(bar, low, high),
            "pooled_y": None if pooled is None else _y(pooled, low, high),
            "held_y": None if held is None else _y(held, low, high),
        },
        "accent": ACCENT,
        "fold_width": FOLD_WIDTH,
        "bar_width": BAR_WIDTH,
        "chart_top": CHART_TOP,
        "chart_height": CHART_HEIGHT,
        "figure_width": max(len(folds), 1) * FOLD_WIDTH,
        "figure_height": CHART_TOP + CHART_HEIGHT + 84,
    }


def render_verdict(
    artifact_path: Path,
    out_dir: Path = DEFAULT_REPORT_DIR,
    command: str | None = None,
) -> Path:
    """Read one validation artifact and draw its verdict as a self-contained page.

    Args:
        artifact_path: The ``state/validation-<stamp>.json`` to read.
        out_dir: Directory to write into. Created if absent.
        command: The command that produced the artifact, drawn on the page.
            Defaults to the ``strategylab validate`` invocation implied by the
            search artifact the verdict names as its source.

    Returns:
        The path written, named after the artifact.

    Raises:
        VerdictReportError: If the artifact cannot be drawn - see
            :func:`build_verdict_context`.
        FileNotFoundError: If ``artifact_path`` does not exist.
        json.JSONDecodeError: If it is not JSON.
    """
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    context = build_verdict_context(
        artifact, command or f"uv run strategylab validate {artifact['source_search']}"
    )
    return render(VERDICT_TEMPLATE, context, out_dir / f"{artifact_path.stem}.html")

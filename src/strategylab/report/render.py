"""HTML reporting.

These are tools the operator returns to, not documents produced once, so this
module is mostly about interaction and only briefly about styling. See the
``report-design`` skill for the full standard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_REPORT_DIR = Path("reports")


def render(template: str, context: dict[str, Any], out: Path) -> Path:
    """Render one self-contained HTML report.

    No external assets - no CDN, no bundler, no build step. Charts are
    hand-built inline SVG, which keeps the file small and opens it instantly,
    and speed is most of what makes a tool feel good to use.

    The rendered page must support:

    - hovering a grid cell to see its metrics without a page load;
    - clicking a cell to show that cell's equity curve;
    - the noise ceiling drawn on the SAME axis as the result rather than in a
      table below it, so a winner sitting inside the noise distribution is
      visible rather than computed;
    - fold results laid out side by side, so inconsistency reads as a shape;
    - loading two result sets to compare runs.

    Every number shown is traceable: the protocol version, the fixture hash,
    and the command that produced it appear on the figure itself.
    ``insufficient_data`` is displayed as prominently as a pass or a fail, never
    buried - burying an honest non-result is the one thing that makes a research
    tool untrustworthy.

    Args:
        template: Name of the Jinja2 template to render, resolved against the
            package's bundled templates.
        context: Values the template needs. Must carry ``protocol_version``,
            ``fixture_hash``, and ``command``, since a figure without them is
            not traceable and this function will not emit one.
        out: Path to write the rendered HTML to, relative to the repository
            root. Created along with its parent directory if absent.

    Returns:
        The path actually written.

    Raises:
        FileNotFoundError: If ``template`` does not exist.
        ValueError: If ``context`` omits any of the traceability keys.
    """
    raise NotImplementedError

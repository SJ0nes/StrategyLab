"""HTML reporting.

These are tools the operator returns to, not documents produced once, so this
module is mostly about interaction and only briefly about styling. See the
``report-design`` skill for the full standard.

SELF-CONTAINED IS A HARD REQUIREMENT, NOT A PREFERENCE. No CDN, no bundler, no
build step, no external request of any kind. One file that opens instantly from
disk, works offline, works in five years and can be emailed. Charts are inline
SVG written into the document and the data is a JSON literal in a ``<script>``
tag, so the whole thing is one file with no companions.
:func:`render` refuses a page that reaches outside itself, because "no external
assets" is the kind of rule that decays silently the first time somebody adds a
font link.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

DEFAULT_REPORT_DIR = Path("reports")

#: Where the bundled templates live, resolved against this package rather than
#: the working directory so a report renders identically from anywhere.
TEMPLATE_DIR = Path(__file__).parent / "templates"

#: Keys a context must carry before anything is written.
#:
#: A figure that cannot say where it came from cannot be used to make a
#: decision, and cannot be put in front of anyone else. These are refused at
#: render time rather than left to a template that would quietly emit an empty
#: span.
TRACEABILITY_KEYS: tuple[str, ...] = ("protocol_version", "fixture_hash", "command")

#: Patterns that would make a page reach outside itself. Checked against the
#: rendered output, not the template, so an external reference smuggled in
#: through the context is caught too.
#:
#: EVERY FETCH VECTOR, NOT JUST ``src`` AND ``href``. An earlier version matched
#: only those two attributes, which an audit showed leaves the three ways a
#: stylesheet or font realistically arrives - a CSS ``url()``, an ``@import``,
#: and a meta refresh - completely unguarded. A check that catches the obvious
#: case and misses the likely ones is worse than none, because it is believed.
#:
#: What is deliberately NOT matched: a ``data:`` URI, which is self-contained by
#: definition; a fragment link; and a bare URL in prose, which is text rather
#: than a request. The pattern requires a scheme in a position that actually
#: fetches.
_EXTERNAL = re.compile(
    r"""(?ix)
      (?: src | href | xlink:href ) \s* = \s* ["']? \s* (?: https? : | // )
    | url \s* \( \s* ["']? \s* (?: https? : | // )
    | @import \s+ ["']? \s* (?: https? : | // )
    | http-equiv \s* = \s* ["']? \s* refresh
    """
)


def render(template: str, context: dict[str, Any], out: Path) -> Path:
    """Render one self-contained HTML report.

    No external assets - no CDN, no bundler, no build step. Charts are
    hand-built inline SVG, which keeps the file small and opens it instantly,
    and speed is most of what makes a tool feel good to use.

    WHAT THE RENDERED PAGES SUPPORT TODAY, against what the standard asks for.
    The full list is five interactions; two of them are met and three cannot be:

    - hovering a grid cell to see its metrics without a page load - MET;
    - clicking a cell to show that cell's equity curve - NOT MET. The search
      artifact carries per-cell metrics and no curves; ninety curves over three
      and a half thousand bars is tens of megabytes against a thirty-kilobyte
      file, so what to store is a decision nobody has taken yet;
    - the noise ceiling on the SAME axis as the result - NOT MET, though no
      longer for want of a ceiling. ``search --trials`` computes one and the
      page reports where the candidate falls in it, but as a sentence and a
      percentile rather than a band drawn on the surface's own axis. A page
      whose search had no ceiling says that instead;
    - fold results side by side - NOT MET, because ``validate/`` is a stub and
      no fold exists;
    - loading two result sets to compare runs - NOT MET.

    Three of those wait on components that do not exist. They are listed here
    rather than quietly dropped, because a contract that describes five
    interactions and a module that offers two should say which two.

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
        ValueError: If ``context`` omits any of the traceability keys, or if
            the rendered page references an external URL. The second is not in
            the contract this function carried as a stub and is added because
            "self-contained" is the property the whole format rests on: it
            decays the first time somebody adds a font link, and nothing else
            in the project would notice.
    """
    missing = [key for key in TRACEABILITY_KEYS if key not in context]
    if missing:
        raise ValueError(
            f"context omits the traceability key(s) {missing}; a figure that cannot say "
            "which protocol, which data snapshot and which command produced it cannot be "
            "used to make a decision"
        )

    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default_for_string=True, default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    try:
        loaded = environment.get_template(template)
    except TemplateNotFound as exc:
        raise FileNotFoundError(
            f"no template named '{template}' in {TEMPLATE_DIR.name}/; "
            f"available: {sorted(path.name for path in TEMPLATE_DIR.glob('*.j2'))}"
        ) from exc

    html = loaded.render(**context)
    external = _EXTERNAL.search(html)
    if external:
        raise ValueError(
            f"the rendered page references something outside itself, at "
            f"{html[max(0, external.start() - 40) : external.end() + 20]!r}; these reports "
            "must open from disk with no network and no companion files"
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out

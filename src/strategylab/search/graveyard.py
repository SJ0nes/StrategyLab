"""The graveyard: an append-only record of every rejected candidate.

Nothing is ever deleted. The rejection count is a finding, not waste - it is
the denominator that makes a survivor mean something, and a project that keeps
only its winners has quietly discarded the evidence needed to judge them.

THE FORMAT IS JSON LINES, one object per line, appended and never rewritten. One
line per rejection means a truncated write costs the last record rather than the
file, and a reader can stream a graveyard larger than memory. The record carries:

- ``schema_version``, so a later reader can tell what it is looking at;
- ``recorded_at``, an ISO-8601 UTC instant;
- ``candidate``, the parameter combination;
- ``metrics``, what it achieved;
- ``reason``, from the closed vocabulary in :data:`REJECTION_REASONS`.

This file lives under ``state/``, which is gitignored, because a graveyard from
a real search is a record of real trading research and not a fixture.

TWO THINGS THIS MODULE IS NOT. It is not deterministic - ``recorded_at`` reads a
clock, so the same rejection recorded twice produces two different lines. The
project's determinism rule is about the ENGINE, whose output must not depend on
when it ran; a log of what happened when is the one place a clock belongs, and
``DecisionRecord`` and ``CacheEntry`` both carry the same field for the same
reason. And it is not safe against concurrent writers: one line is written per
call in a single append, which is atomic enough for the searches this project
runs, and nothing coordinates two processes writing the same file.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_GRAVEYARD_PATH = Path("state/graveyard.jsonl")

#: Schema version of a graveyard record. Bumped when the fields change, so a
#: reader meeting an unfamiliar version can say so rather than guess.
GRAVEYARD_SCHEMA_VERSION = 1

#: Every reason a candidate may be rejected for. CLOSED, in the same sense and
#: for the same reason as the strategy config's condition vocabulary: this set is
#: the denominator's index, and a typo'd ``edge_winnner`` would silently become a
#: fifth category that no report counts and no reader notices. Adding a reason is
#: a code change with a test, never a string a caller invents.
#:
#: ``backtest_failed`` is not in the four the stub contract named, and is added
#: because the case exists and had nowhere to go: a cell whose backtest raised -
#: an account driven to zero reaches ``max_drawdown``, which refuses a
#: non-positive equity - is rejected for a reason none of the other four
#: describes. ``search/plateau.py`` requires such a cell to keep its row with a
#: ``NaN`` metric, and this is what the search writes beside it.
REJECTION_REASONS = frozenset(
    {
        "edge_winner",
        "degenerate_plateau",
        "below_noise_ceiling",
        "drawdown_ceiling",
        "backtest_failed",
    }
)


def _jsonable_metric(value: float, name: str) -> float | None:
    """Render one metric for JSON, turning a non-finite value into ``None``.

    JSON has no ``NaN`` and no ``Infinity``. Python's :mod:`json` will happily
    write the bare tokens ``NaN`` and ``Infinity``, which are not JSON and which
    a strict parser refuses - so a graveyard containing one failed cell would be
    unreadable by anything but Python. The value becomes ``null`` instead, and
    nothing is lost that matters: ``reason`` already says the cell failed, which
    is the fact the record exists to preserve.

    Args:
        value: The metric value, in whatever unit that metric carries.
        name: Metric name, used in the message.

    Returns:
        The value as a float, or ``None`` when it is not finite.

    Raises:
        ValueError: If ``value`` is not a real number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"metric '{name}' is not a number: {type(value).__name__}")
    return float(value) if math.isfinite(value) else None


def record_rejection(
    candidate: dict[str, int],
    metrics: dict[str, float],
    reason: str,
    path: Path = DEFAULT_GRAVEYARD_PATH,
) -> None:
    """Append one rejected candidate to the graveyard.

    Append-only. Existing records are never rewritten or removed, so the file
    is a complete history of what was tried and why it did not survive.

    The line is always terminated by a bare line feed, on every platform. A JSON
    Lines file is data, not source, and a reader splitting on newlines should
    not have to strip a carriage return the writing platform happened to add.

    Args:
        candidate: The rejected parameter combination, mapping name to value in
            bars.
        metrics: The metrics it achieved, keyed by metric name, each in that
            metric's own unit - ``sharpe`` is a ratio, ``max_drawdown_pct`` a
            PERCENTAGE. Recorded even though it was rejected, because "rejected
            with Sharpe 1.9 on an edge cell" and "rejected with Sharpe 0.1" are
            different findings. A non-finite value is recorded as JSON ``null``;
            see :func:`_jsonable_metric`.
        reason: Why it was rejected. Must be one of :data:`REJECTION_REASONS`.
        path: Graveyard file, relative to the repository root. Created along
            with its parent directory if absent.

    Returns:
        None.

    Raises:
        ValueError: If ``reason`` is empty, or is not in
            :data:`REJECTION_REASONS`, or if a metric value is not a real
            number.

            THE VOCABULARY CHECK IS NOT IN THE CONTRACT THIS FUNCTION CARRIED AS
            A STUB, which named only the empty-string case while describing the
            reason as coming "from the engine's fixed vocabulary". A vocabulary
            that is fixed but unchecked is a convention, and the one thing this
            file exists to support - counting how many candidates died and of
            what - is exactly what a silent misspelling breaks. The cost is
            real and worth stating: a search that reaches this with a bad reason
            raises mid-run, so the caller must use a constant rather than a
            literal it typed twice.

            A candidate whose parameter names are not strings is refused too:
            ``json`` coerces an ``int`` key to a string without complaint, so
            ``{5: 10}`` would be recorded as ``{"5": 10}`` and a later reader
            could not tell which it had been.

            NO REFUSAL LEAVES A FILE BEHIND. The record is serialised before the
            path is touched, so a failure to serialise cannot create the empty
            file that an earlier version left whenever ``json.dumps`` raised.
            Whatever ``json.dumps`` refuses - a value of a type it cannot
            represent - is re-raised as a ``ValueError`` naming it, rather than
            escaping as the ``TypeError`` this contract does not admit.

            OSError propagates unchanged if the file or its directory cannot be
            written. That is not caught, because a graveyard that silently fails
            to record is worse than a search that stops.
    """
    if not reason:
        raise ValueError("reason is empty; a rejection must say what it was rejected for")
    if reason not in REJECTION_REASONS:
        raise ValueError(
            f"reason '{reason}' is not one of {sorted(REJECTION_REASONS)}; the vocabulary is "
            "closed so that rejections can be counted by category"
        )

    unnamed = sorted(repr(key) for key in candidate if not isinstance(key, str))
    if unnamed:
        raise ValueError(
            f"candidate has non-string parameter names {unnamed}; JSON object keys are "
            "strings, and json would silently coerce an int key to one"
        )

    record: dict[str, Any] = {
        "schema_version": GRAVEYARD_SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "candidate": dict(candidate),
        "metrics": {name: _jsonable_metric(value, name) for name, value in metrics.items()},
        "reason": reason,
    }

    # SERIALISED BEFORE THE FILE IS TOUCHED, so that no refusal leaves a file
    # behind. Serialising inside the `with` block created an empty file and left
    # it there whenever json.dumps raised, which broke the one property this
    # function promises about a call it rejects.
    #
    # allow_nan=False is a second line of defence, unreachable as long as
    # _jsonable_metric runs first, which is why no test exercises it. It is kept
    # because the invariant it protects - this file stays valid JSON - is the
    # one a silent regression would break most quietly.
    try:
        line = json.dumps(record, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"rejection is not serialisable as JSON: {exc}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")

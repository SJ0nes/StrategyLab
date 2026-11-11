"""What the window remembers between launches: today, only whether the tour has been seen.

The page cannot keep this itself. Its storage belongs to its origin, and the
origin carries the server's port, which is chosen afresh at every launch, so
anything the page stored would be gone the next time the window opened. The
server keeps it instead, in ``state/window.json``, which is not committed.

The file holds nothing that matters if it is lost or damaged: the tour is
offered again. It is read as a file this server did not write, through
:mod:`strategylab.api.reading`.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from strategylab.api.reading import Unreadable, read_json_object

#: Where the window's memory is kept, relative to the project root.
WINDOW_FILE = Path("state") / "window.json"

#: Schema version of the file.
WINDOW_SCHEMA_VERSION = 1

#: Largest file read, in bytes.
MAX_WINDOW_BYTES = 16 * 1024


def tour_seen(root: Path) -> bool:
    """Say whether the tour has been seen or skipped.

    Args:
        root: The project root.

    Returns:
        True only when the file is there, readable, of this schema version,
        and records a tour as seen. Anything else is False, and the tour is
        offered.
    """
    try:
        payload = read_json_object(root / WINDOW_FILE, MAX_WINDOW_BYTES)
    except Unreadable:
        return False
    if payload.get("schema_version") != WINDOW_SCHEMA_VERSION:
        return False
    return isinstance(payload.get("tour_seen_at"), str)


def mark_tour_seen(root: Path, now: datetime) -> bool:
    """Record that the tour has been seen or skipped.

    Written through a temporary file renamed over the old one.

    Args:
        root: The project root.
        now: The present moment, timezone-aware.

    Returns:
        True, as :func:`tour_seen` now reports.

    Raises:
        OSError: If the file cannot be written.
    """
    path = root / WINDOW_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix="window-", suffix=".partial")
    partial = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "schema_version": WINDOW_SCHEMA_VERSION,
                    "tour_seen_at": now.astimezone(UTC).isoformat(),
                },
                output,
            )
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)
    return tour_seen(root)

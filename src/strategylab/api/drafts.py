"""What the app knows about a draft beyond its config: where it came from, who checked it.

Two files, both optional.

``configs/<id>.source.json`` SAYS WHERE A STRATEGY CAME FROM. It is written by
the ``import-strategy`` skill when Claude Code reads a strategy out of a video,
and committed beside the config like the config itself. It holds the video's
link and title, each rule with the time in the video it was stated, and every
thing the video said that the config could not express. A strategy written by
hand has none, and that is not an error. The registry lists ``*.yaml`` only,
so this file is never taken for a strategy.

``state/confirmed.json`` SAYS THE OPERATOR READ THE RULES. A strategy is
confirmed for one content hash of its config: editing the file in any way that
changes what the loader reads undoes the confirmation, and the rules have to be
read again. No job can start for a strategy that is not confirmed for the file
as it is now (``docs/decisions/0018``). The file is under ``state/``, so it is
not committed.

Both are read as files this server did not write - or, for the second, may not
have written - through :mod:`strategylab.api.reading`, and every field is let
through only as the kind of thing it should be.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from strategylab.api.reading import Unreadable, missing, read_json_object, text_or_none
from strategylab.api.registry import CONFIGS_DIR, StrategyFile

#: Where confirmations are kept, relative to the project root.
CONFIRMED_FILE = Path("state") / "confirmed.json"

#: Schema version of a source file and of the confirmations file.
SOURCE_SCHEMA_VERSION = 1
CONFIRMED_SCHEMA_VERSION = 1

#: Largest source or confirmations file read, in bytes.
MAX_DRAFT_BYTES = 256 * 1024

#: Most items read from one list in a source file, and longest text kept.
MAX_ITEMS = 50
MAX_TEXT = 500

#: The only kind of link passed on. The window shows it as text and never
#: opens it.
LINK_PREFIX = "https://"

#: How many times a confirmation is retried when Windows refuses to replace the
#: file because something else has it open, and the pause between, in seconds.
REPLACE_ATTEMPTS = 20
REPLACE_PAUSE = 0.05

#: Held while the confirmations file is read, changed and written back, so two
#: requests to one server cannot each lose the other's confirmation.
_WRITING = threading.Lock()


class StaleConfirmation(Exception):
    """The rules on screen are not the rules in the file now."""


def source_file(strategy: StrategyFile) -> Path:
    """Name a strategy's source file.

    Args:
        strategy: The strategy, as the registry found it.

    Returns:
        ``configs/<id>.source.json``, relative to the project root.
    """
    return CONFIGS_DIR / f"{strategy.id}.source.json"


def _text(value: Any) -> str | None:
    """Let a field through only if it is text, cut to :data:`MAX_TEXT` characters.

    Args:
        value: A field read from a file.

    Returns:
        The text with control and formatting characters removed and
        surrounding space trimmed, or ``None`` when it is not text or nothing
        is left.
    """
    text = text_or_none(value)
    if text is None:
        return None
    # Control and formatting characters - a NUL, a right-to-left override -
    # render as nothing or turn the text around, so a title or a link could
    # read as something it is not. They are dropped.
    text = "".join(char for char in text if unicodedata.category(char) not in ("Cc", "Cf"))
    if not text.strip():
        return None
    return text.strip()[:MAX_TEXT]


def _items(value: Any, fields: tuple[str, ...]) -> list[dict[str, str | None]]:
    """Read a list of small records, keeping only the named text fields.

    Args:
        value: A field read from a file, expected to be a list of objects.
        fields: The text fields to keep from each; the first is required.

    Returns:
        Up to :data:`MAX_ITEMS` records. An entry that is not an object, or
        whose first field is not text, is skipped.
    """
    if not isinstance(value, list):
        return []
    kept: list[dict[str, str | None]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        record = {name: _text(entry.get(name)) for name in fields}
        if record[fields[0]] is None:
            continue
        kept.append(record)
        if len(kept) == MAX_ITEMS:
            break
    return kept


def read_source(root: Path, strategy: StrategyFile) -> dict[str, Any] | None:
    """Report where a strategy came from, as its source file records it.

    Args:
        root: The project root.
        strategy: The strategy.

    Returns:
        ``None`` when the strategy has no source file. The missing shape when
        it has one that cannot be read, or that declares another schema
        version. Otherwise ``available`` true; ``url``, the link if it starts
        with :data:`LINK_PREFIX` and ``None`` otherwise; ``title``, ``channel``
        and ``watched_at`` as recorded, each text or ``None``; ``rules``, each
        ``{"rule", "at"}`` - a rule as the video stated it and the time in the
        video; and ``left_out``, each ``{"what", "why", "at"}`` - something the
        video said that the config does not express, and why.
    """
    path = root / source_file(strategy)
    if not path.exists():
        return None
    try:
        payload = read_json_object(path, MAX_DRAFT_BYTES)
    except Unreadable as exc:
        return missing(f"The record of where this strategy came from could not be read: {exc}.")
    if payload.get("schema_version") != SOURCE_SCHEMA_VERSION:
        return missing(
            "The record of where this strategy came from is in a format this app does not read."
        )
    raw = payload.get("source")
    source: dict[str, Any] = raw if isinstance(raw, dict) else {}
    url = _text(source.get("url"))
    return {
        "available": True,
        "url": url if url is not None and url.startswith(LINK_PREFIX) else None,
        "title": _text(source.get("title")),
        "channel": _text(source.get("channel")),
        "watched_at": _text(source.get("watched_at")),
        "rules": _items(payload.get("rules"), ("rule", "at")),
        "left_out": _items(payload.get("left_out"), ("what", "why", "at")),
    }


def source_hash(root: Path, strategy: StrategyFile) -> str | None:
    """Fingerprint a strategy's source file, so a confirmation covers it too.

    Args:
        root: The project root.
        strategy: The strategy.

    Returns:
        The SHA-256 of the file's bytes, or ``None`` when there is no file or
        it cannot be read. A confirmation records this; a source file that
        changes afterwards - its list of what was left out, say - undoes it.
    """
    try:
        return hashlib.sha256((root / source_file(strategy)).read_bytes()).hexdigest()
    except OSError:
        return None


def _confirmations(root: Path) -> dict[str, Any]:
    """Read the confirmations on record.

    Args:
        root: The project root.

    Returns:
        Each strategy id mapped to what was recorded for it. Empty when there
        is no file, it cannot be read, or it declares another schema version:
        an unreadable record of a confirmation is no confirmation.
    """
    try:
        payload = read_json_object(root / CONFIRMED_FILE, MAX_DRAFT_BYTES)
    except Unreadable:
        return {}
    if payload.get("schema_version") != CONFIRMED_SCHEMA_VERSION:
        return {}
    confirmed = payload.get("confirmed")
    return confirmed if isinstance(confirmed, dict) else {}


def confirmation(root: Path, strategy: StrategyFile) -> dict[str, Any]:
    """Say whether the operator has confirmed a strategy's rules for its file as it is now.

    Args:
        root: The project root.
        strategy: The strategy.

    Returns:
        ``confirmed``, true only when a confirmation is on record for this
        strategy's id, its config's content hash as it is now, AND its source
        file's bytes as they are now (or no source file then and now); and
        ``confirmed_at``, when that confirmation was recorded, or ``None``.
        ``stale`` is true when a confirmation is on record for an earlier
        version of the file.
    """
    entry = _confirmations(root).get(strategy.id)
    if not isinstance(entry, dict):
        return {"confirmed": False, "confirmed_at": None, "stale": False}
    matches = entry.get("config_hash") == strategy.config_hash and entry.get(
        "source_hash"
    ) == source_hash(root, strategy)
    return {
        "confirmed": matches,
        "confirmed_at": text_or_none(entry.get("confirmed_at")) if matches else None,
        "stale": not matches,
    }


def confirm(root: Path, strategy: StrategyFile, shown: str, now: datetime) -> dict[str, Any]:
    """Record that the operator has read a strategy's rules and found them right.

    The operator confirms what was on screen, and the page sends the config
    hash it was shown with the request. If the file has changed since, the
    confirmation is refused: pressing the button must not approve rules
    nobody saw. The record names the config's content hash and the source
    file's fingerprint, so it lapses when either changes.

    The file is rewritten whole, under a lock, through a temporary file of its
    own renamed over it, so a reader never sees half of one and two requests
    cannot lose each other's work. Windows refuses the rename while anything
    has the file open; it is retried briefly.

    Args:
        root: The project root.
        strategy: The strategy, as the registry found it now.
        shown: The config hash the page showed the rules for.
        now: The present moment, timezone-aware.

    Returns:
        The strategy's confirmation, as :func:`confirmation` reports it.

    Raises:
        StaleConfirmation: If ``shown`` is not the config's hash now.
        OSError: If the file cannot be written.
    """
    if shown != strategy.config_hash:
        raise StaleConfirmation(
            "the strategy file has changed since its rules were shown; read them again"
        )
    path = root / CONFIRMED_FILE
    with _WRITING:
        confirmed = dict(_confirmations(root))
        confirmed[strategy.id] = {
            "config_hash": strategy.config_hash,
            "source_hash": source_hash(root, strategy),
            "config": strategy.config,
            "confirmed_at": now.astimezone(UTC).isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=path.parent, prefix="confirmed-", suffix=".partial")
        partial = Path(name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as output:
                json.dump(
                    {"schema_version": CONFIRMED_SCHEMA_VERSION, "confirmed": confirmed},
                    output,
                    indent=2,
                    sort_keys=True,
                )
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    os.replace(partial, path)
                    break
                except PermissionError:
                    if attempt == REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(REPLACE_PAUSE)
        finally:
            partial.unlink(missing_ok=True)
    return confirmation(root, strategy)

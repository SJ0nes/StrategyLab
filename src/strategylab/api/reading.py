"""How the app's server reads a file it did not write, and words what went wrong.

Shared by every reader under :mod:`strategylab.api`. Three rules live here so
that no reader has to remember them:

A FILE IN ``configs/`` OR ``state/`` IS NOT TRUSTED TO BE WELL-FORMED. One too
large to be what it claims is not opened. One holding ``NaN`` or ``Infinity`` -
which Python's JSON reader accepts and JSON does not - is refused, because a
response containing either cannot be sent.

NO REASON NAMES A DIRECTORY ABOVE THE PROJECT. The loaders put the path they
were given into their error messages, and that path starts with wherever the
project happens to be kept - on most machines, a directory named after the
person. Every reason passed on to the app goes through :func:`plain_reason`,
which writes the project root as ``.``.

WHAT IS READ CAN ALWAYS BE SENT. :func:`parse_json` refuses anything the
response could not carry or the window could not draw: a number that is not
finite however it was spelled, an integer too large to be a count or a price,
text that is not valid Unicode, and nesting deeper than any file this project
writes. The four typed readers - :func:`text_or_none` and its neighbours - then
let a field through only as the kind of thing it is supposed to be, so a file
holding a list where a date belongs yields a missing date and not a crash.

WHAT IS MISSING IS RETURNED AS MISSING. :func:`missing` is the one shape that
stands for a figure which could not be read: never a zero, never a default.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

#: Deepest nesting read. A search artifact, the deepest file this project
#: writes, nests four levels.
MAX_DEPTH = 16

#: Largest whole number read, in absolute value. Far above any count, bar or
#: price here, and small enough to convert to a float without overflowing.
MAX_INTEGER = 2**63


class Unreadable(Exception):
    """A file could not be read as what it claims to be.

    The message is a reason in plain words, and names no directory: it is
    written here, not taken from the operating system.
    """


def plain_reason(root: Path, error: Exception) -> str:
    """Render an error for the app without the directory the project is kept in.

    Args:
        root: The project root.
        error: The error a loader raised.

    Returns:
        The error's message with every spelling of ``root`` - as given, resolved,
        and with either kind of slash - replaced by ``.``.
    """
    message = str(error)
    spellings = {str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}
    # An OSError prints its file name as Python would write it in source, with
    # each backslash doubled, so that spelling has to be looked for as well.
    spellings |= {spelling.replace("\\", "\\\\") for spelling in spellings}
    for spelling in sorted(spellings, key=len, reverse=True):
        # Windows paths compare without regard to case, and a drive letter is
        # reported in either.
        message = re.sub(re.escape(spelling), ".", message, flags=re.IGNORECASE)
    return message


def without_root(text: str, root: Path) -> str:
    """Take the project root out of a message, in either slash style.

    An error raised while reading a file names its full path, which on this
    machine includes the user's name. The window, and so anyone shown it,
    needs only the path inside the project.

    Args:
        text: The message.
        root: The project root.

    Returns:
        The message with ``root`` and the separator after it removed, whether
        written with forward slashes or the platform's own.
    """
    for form in {root.as_posix(), str(root)}:
        for separator in ("/", "\\"):
            text = text.replace(form + separator, "")
    return text


def missing(reason: str) -> dict[str, Any]:
    """Return the shape that stands for a figure which could not be read.

    Args:
        reason: Why, in words the person using the app can act on.

    Returns:
        ``{"available": False, "reason": reason}``.
    """
    return {"available": False, "reason": reason}


def parse_json(text: str) -> Any:
    """Parse JSON text, refusing what a response could not carry.

    Args:
        text: The text to parse.

    Returns:
        The parsed value. Every number in it is finite, every integer is within
        :data:`MAX_INTEGER` of zero, every string and key is valid Unicode, and
        nothing is nested deeper than :data:`MAX_DEPTH`.

    Raises:
        ValueError: If the text is not JSON; holds ``NaN``, ``Infinity`` or
            ``-Infinity``, or a number such as ``1e999`` that reads as one;
            holds an integer beyond :data:`MAX_INTEGER`; holds text that is not
            valid Unicode; or is nested deeper than :data:`MAX_DEPTH`.
    """
    try:
        value = json.loads(text)
    except RecursionError as exc:
        raise ValueError("nested too deeply to read") from exc
    _check_sendable(value, 0)
    return value


def _check_sendable(value: Any, depth: int) -> None:
    """Refuse a parsed JSON value that a response could not carry.

    Args:
        value: The value, as :func:`json.loads` returned it.
        depth: How many containers enclose it.

    Raises:
        ValueError: See :func:`parse_json`.
    """
    if depth > MAX_DEPTH:
        raise ValueError(f"nested more than {MAX_DEPTH} deep")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("holds text that is not valid Unicode") from exc
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        if abs(value) > MAX_INTEGER:
            raise ValueError("holds a whole number too large to be a figure")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("holds a number that is not finite")
    elif isinstance(value, list):
        for item in value:
            _check_sendable(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_sendable(key, depth + 1)
            _check_sendable(item, depth + 1)


def text_or_none(value: Any) -> str | None:
    """Let a field through only if it is text.

    Args:
        value: A field read from a file.

    Returns:
        ``value`` when it is a string, otherwise ``None``.
    """
    return value if isinstance(value, str) else None


def figure_or_none(value: Any) -> float | int | None:
    """Let a field through only if it is a number.

    Args:
        value: A field read from a file.

    Returns:
        ``value`` when it is an integer or a float - and not a boolean, which
        Python counts as an integer - otherwise ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def count_or_none(value: Any) -> int | None:
    """Let a field through only if it is a whole number.

    Args:
        value: A field read from a file.

    Returns:
        ``value`` when it is an integer and not a boolean, otherwise ``None``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def params_or_none(value: Any) -> dict[str, int] | None:
    """Let a field through only if it is a parameter combination.

    Args:
        value: A field read from a file.

    Returns:
        ``value`` when it is a mapping whose every value is a whole number (in
        BARS, for every parameter this project searches), otherwise ``None``.
    """
    if not isinstance(value, dict):
        return None
    if any(count_or_none(item) is None for item in value.values()):
        return None
    return {str(name): int(item) for name, item in value.items()}


def read_json_object(path: Path, max_bytes: int) -> dict[str, Any]:
    """Read a file as one JSON object.

    Args:
        path: The file.
        max_bytes: The largest size, in bytes, that is opened at all.

    Returns:
        The object.

    Raises:
        Unreadable: If the file cannot be opened, is larger than ``max_bytes``,
            is not JSON, holds a number JSON does not have, or is not an
            object. The message says which.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise Unreadable("the file could not be opened") from exc
    if size > max_bytes:
        raise Unreadable(f"the file is {size} bytes, more than the {max_bytes} read")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Unreadable("the file could not be opened") from exc
    except ValueError as exc:
        # UnicodeDecodeError is a ValueError: bytes that are not UTF-8.
        raise Unreadable("the file is not valid JSON") from exc
    try:
        payload = parse_json(text)
    except ValueError as exc:
        raise Unreadable("the file is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise Unreadable("the file does not hold a JSON object")
    return payload

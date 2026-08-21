"""The research protocol: the single reader and writer of ``protocol.yaml``.

The protocol is written once, before any search has run. Editing it after results
exist invalidates every comparison made before the edit, which is why this module
exposes exactly one mutation: the walk-forward access counter.

Everything downstream reaches the protocol through attribute access on
:class:`Protocol` - ``p.windows.in_sample.start``, never dict indexing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from strategylab.backtest.costs import CostModel

DEFAULT_PROTOCOL_PATH = Path("protocol.yaml")

# Divisor taking a percentage to a fraction. Named rather than inlined so the
# one conversion in the system is greppable.
PERCENT_PER_UNIT = 100.0

# The trailing ``\r?`` is load-bearing. Under re.MULTILINE, ``$`` matches only
# at end-of-string or immediately before ``\n`` - so on a CRLF file the ``\r``
# sits between the digits and the anchor and neither pattern matches at all.
# This module exists to preserve a file's line endings byte for byte, and
# without this it would refuse the CRLF file it promises to handle.
# Two things these patterns must tolerate, both learned the hard way.
#
# The ``\r?`` is load-bearing: under re.MULTILINE ``$`` matches only before a
# ``\n``, so on a CRLF file the ``\r`` would defeat the anchor entirely and the
# counter would be reported missing from a file that plainly contains it.
#
# The optional trailing comment matters just as much. This module exists BECAUSE
# the comments in protocol.yaml are the rulebook, and the file itself tells the
# operator to reset this counter by hand - which is exactly the edit that
# attracts a note. A pattern rejecting
# ``walk_forward_touches: 0  # reset 2026-08-21`` would be stricter than the
# loader that accepts it.
#
# The comment alternation requires WHITESPACE before the ``#``, because that is
# what YAML requires. Without it, ``0#note`` is a plain scalar that the loader
# rejects as a non-integer - and a pattern that matched it anyway would let the
# incrementer happily rewrite a line the loader refuses to read.
#
# _TOUCH_LINE's trailing context is a LOOKAHEAD rather than a match, so the span
# covers the digits alone and splicing cannot disturb the comment, the trailing
# whitespace, or the line ending.
_LINE_TAIL = r"(?:[ \t]+#[^\n\r]*)?[ \t]*\r?$"
_TOUCH_LINE = re.compile(
    r"^(?P<indent>[ \t]+)walk_forward_touches:[ \t]*(?P<count>\d+)(?=" + _LINE_TAIL + ")",
    re.MULTILINE,
)
_ACCESS_LOG_HEADER = re.compile(r"^access_log:" + _LINE_TAIL, re.MULTILINE)
# A line beginning in column zero with something other than a comment ends the
# preceding block. Used to bound the search for the counter to its own mapping.
_NEXT_TOP_LEVEL = re.compile(r"^[^\s#]", re.MULTILINE)


class ProtocolError(Exception):
    """Raised when ``protocol.yaml`` is missing, malformed, or self-inconsistent.

    The message always names the offending field, so the operator can go straight
    to the line rather than re-reading the whole file.
    """


@dataclass(frozen=True)
class Window:
    """A date range, inclusive of both endpoints.

    Attributes:
        start: First date the window covers.
        end: Last date the window covers.
    """

    start: date
    end: date


@dataclass(frozen=True)
class Windows:
    """The in-sample and held-back windows, and how the latter is split.

    Attributes:
        in_sample: The only dates a parameter search may look at.
        walk_forward: Dates held back from the search, used only to test a
            candidate the search has already chosen.
        fold_months: Length in whole months of each rolling walk-forward fold.
    """

    in_sample: Window
    walk_forward: Window
    fold_months: int


@dataclass(frozen=True)
class Instruments:
    """The ticker searched on and the ticker actually traded.

    Attributes:
        research: Ticker the search runs against, chosen for depth of history.
        execution: Ticker that would actually be bought, chosen for being
            purchasable from the operator's account. May differ from
            ``research``; when it does, that substitution is a stated limitation
            recorded in ``LIMITATIONS.md``, not a footnote.
    """

    research: str
    execution: str


@dataclass(frozen=True)
class Validation:
    """The pass criteria, fixed before any result exists.

    Attributes:
        primary_metric: Name of the metric the pass decision is made on.
        min_sharpe: Annualised Sharpe floor, before ``pass_margin`` is added.
            Pre-registered here rather than passed in at run time, because a
            threshold chosen after seeing the result is not a threshold.
        min_trades_per_fold: Below this many trades, that FOLD reports
            ``insufficient_data``. Applies to a single fold only.
        min_trades_pooled: Below this many trades summed across all folds, the
            whole VALIDATION reports ``insufficient_data``. The pooled figure
            carries the pass decision, so it needs its own floor; a per-fold
            threshold alone would contradict a pooled decision.
        max_drawdown_ceiling_pct: Peak-to-trough drawdown expressed in percent,
            above which a candidate is rejected regardless of its return;
            ``25.0`` means twenty-five percent.
        pass_margin: ADDITIVE margin on ``min_sharpe``, in the metric's own
            units. The effective bar is ``min_sharpe + pass_margin`` — with the
            defaults, 1.0 + 0.20 = 1.20. A marginal pass is a fail.
    """

    primary_metric: str
    min_sharpe: float
    min_trades_per_fold: int
    min_trades_pooled: int
    max_drawdown_ceiling_pct: float
    pass_margin: float

    @property
    def effective_sharpe_bar(self) -> float:
        """The Sharpe a candidate must actually clear to pass.

        Spelled out as a single value so no caller has to remember whether the
        margin is additive or multiplicative. It is additive.

        Returns:
            ``min_sharpe + pass_margin``, in annualised Sharpe units.
        """
        return self.min_sharpe + self.pass_margin


@dataclass(frozen=True)
class Protocol:
    """The parsed protocol.

    Attribute access throughout - ``p.windows.in_sample.start``, never dict
    indexing. Everything downstream depends on this shape.

    Attributes:
        protocol_version: Schema version of ``protocol.yaml``.
        created: Date the protocol was pre-registered.
        instruments: Research and execution tickers.
        windows: In-sample and held-back date ranges.
        costs: Broker cost parameters, fixed before any search runs.
        validation: The pass criteria.
        walk_forward_touches: How many times the held-back window has been read.
            Every read erodes its independence, so the count is reported rather
            than hidden.
    """

    protocol_version: int
    created: date
    instruments: Instruments
    windows: Windows
    costs: CostModel
    validation: Validation
    walk_forward_touches: int


def _field(where: str, key: str) -> str:
    """Join a parent path and a key into a dotted field name for messages.

    Args:
        where: Dotted path of the parent mapping, or ``""`` for the file root.
        key: Key within that mapping.

    Returns:
        The dotted field name, without a leading dot at the root.
    """
    return f"{where}.{key}" if where else key


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    """Fetch a required key, raising a :class:`ProtocolError` that names it.

    Args:
        mapping: The mapping to read from.
        key: The key that must be present.
        where: Dotted path of ``mapping`` within the file, ``""`` at the root.

    Returns:
        The value stored at ``key``.

    Raises:
        ProtocolError: If ``key`` is absent.
    """
    if key not in mapping:
        raise ProtocolError(f"protocol.yaml: missing required field '{_field(where, key)}'")
    return mapping[key]


def _read_preserving_newlines(path: Path) -> str:
    r"""Read a text file without translating its line endings.

    ``Path.read_text`` opens with ``newline=None``, which rewrites every
    ``\r\n`` and ``\r`` to ``\n`` on the way in. Paired with a default write
    that expands ``\n`` back to the platform separator, that silently converts
    the whole file's line endings. Opening with ``newline=""`` disables both
    halves of the translation, which is what lets the counter be edited without
    touching the rest of the file.

    Args:
        path: File to read.

    Returns:
        The file's exact text, line endings intact.

    Raises:
        ProtocolError: If the file is missing, is not a readable file, or is
            not valid UTF-8.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError as exc:
        raise ProtocolError(f"protocol.yaml: file not found at '{path}'") from exc
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"protocol.yaml: file at '{path}' is not valid UTF-8") from exc
    except OSError as exc:
        raise ProtocolError(f"protocol.yaml: cannot read '{path}': {exc}") from exc


def _as_str(value: Any, where: str) -> str:
    """Coerce a parsed YAML value to a string, or raise naming the field.

    Deliberately does NOT accept a list, mapping, or ``None`` by stringifying
    it. A ticker written as ``[SPY]`` would otherwise load as the literal string
    ``"['SPY']"`` and fail much later, at the data vendor, with an error that
    names neither the field nor the file.

    Args:
        value: The parsed value.
        where: Dotted path of the field, used in the message.

    Returns:
        The value as a ``str``.

    Raises:
        ProtocolError: If ``value`` is not a string.
    """
    if not isinstance(value, str):
        raise ProtocolError(
            f"protocol.yaml: field '{where}' must be a string, got {type(value).__name__}"
        )
    return value


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    """Coerce a parsed YAML value to a mapping, or raise naming the field.

    Args:
        value: The parsed value.
        where: Dotted path of the field, used in the message.

    Returns:
        The value as a ``dict``.

    Raises:
        ProtocolError: If ``value`` is not a mapping.
    """
    if not isinstance(value, dict):
        raise ProtocolError(
            f"protocol.yaml: field '{where}' must be a mapping, got {type(value).__name__}"
        )
    return value


def _as_date(value: Any, where: str) -> date:
    """Coerce a parsed YAML value to a date, or raise naming the field.

    Accepts an ISO-8601 ``YYYY-MM-DD`` string or a value PyYAML has already
    parsed into a date, so quoting in the file is not load-bearing.

    Args:
        value: The parsed value.
        where: Dotted path of the field, used in the message.

    Returns:
        The value as a ``date``.

    Raises:
        ProtocolError: If ``value`` is neither a date nor an ISO-8601 string.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ProtocolError(
                f"protocol.yaml: field '{where}' is not an ISO-8601 date: {value!r}"
            ) from exc
    raise ProtocolError(
        f"protocol.yaml: field '{where}' must be a date, got {type(value).__name__}"
    )


def _as_float(value: Any, where: str) -> float:
    """Coerce a parsed YAML value to a float, or raise naming the field.

    Args:
        value: The parsed value.
        where: Dotted path of the field, used in the message.

    Returns:
        The value as a ``float``.

    Raises:
        ProtocolError: If ``value`` is not a real number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ProtocolError(
            f"protocol.yaml: field '{where}' must be a number, got {type(value).__name__}"
        )
    return float(value)


def _as_int(value: Any, where: str) -> int:
    """Coerce a parsed YAML value to an int, or raise naming the field.

    Args:
        value: The parsed value.
        where: Dotted path of the field, used in the message.

    Returns:
        The value as an ``int``.

    Raises:
        ProtocolError: If ``value`` is not an integer.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(
            f"protocol.yaml: field '{where}' must be an integer, got {type(value).__name__}"
        )
    return value


def _non_negative(value: float, where: str) -> float:
    """Return ``value`` if it is zero or positive, else raise naming the field.

    Args:
        value: The value to check.
        where: Dotted path of the field, used in the message.

    Returns:
        ``value`` unchanged.

    Raises:
        ProtocolError: If ``value`` is negative.
    """
    if value < 0:
        raise ProtocolError(f"protocol.yaml: field '{where}' must be >= 0, got {value}")
    return value


def _window(raw: Any, where: str) -> Window:
    """Build a :class:`Window` from a parsed mapping, validating its ordering.

    Args:
        raw: The parsed mapping, expected to carry ``start`` and ``end``.
        where: Dotted path of the field, used in messages.

    Returns:
        The parsed window.

    Raises:
        ProtocolError: If a key is missing, a date is unparseable, or ``end``
            precedes ``start``.
    """
    mapping = _as_mapping(raw, where)
    start = _as_date(_require(mapping, "start", where), f"{where}.start")
    end = _as_date(_require(mapping, "end", where), f"{where}.end")
    if end < start:
        raise ProtocolError(
            f"protocol.yaml: field '{where}' ends ({end}) before it starts ({start})"
        )
    return Window(start=start, end=end)


def _rate(mapping: dict[str, Any], key: str, *, required: bool) -> float:
    """Read one percent-valued rate and return it as a fraction.

    This is the ONE place in the system where a percentage becomes a fraction.
    ``protocol.yaml`` stores rates under ``_pct`` keys because that is what the
    numbers in it are; everything downstream takes fractions. Converting in
    exactly one place is what makes the hundred-fold error testable rather than
    merely warned about.

    Args:
        mapping: The parsed ``costs`` mapping.
        key: The ``_pct`` key to read.
        required: Whether absence is an error. Optional rates default to zero,
            so a broker without them needs no entry rather than an explicit
            zero.

    Returns:
        The rate as a fraction, being the stored percentage divided by 100.

    Raises:
        ProtocolError: If the rate is missing while required, non-numeric, or
            negative.
    """
    where = f"costs.{key}"
    raw = _require(mapping, key, "costs") if required else mapping.get(key, 0.0)
    return _non_negative(_as_float(raw, where), where) / PERCENT_PER_UNIT


def _costs(raw: Any) -> CostModel:
    """Build the :class:`CostModel` from the ``costs`` block.

    Reads percentages and returns fractions. See :func:`_rate`.

    Args:
        raw: The parsed ``costs`` mapping.

    Returns:
        The broker cost parameters, every rate a fraction.

    Raises:
        ProtocolError: If ``costs`` is not a mapping, or a required rate is
            missing, non-numeric, or negative.
    """
    mapping = _as_mapping(raw, "costs")
    flat = _as_float(mapping.get("commission_min_flat", 0.0), "costs.commission_min_flat")
    return CostModel(
        spread_frac=_rate(mapping, "spread_pct", required=True),
        slippage_frac=_rate(mapping, "slippage_pct", required=True),
        commission_frac=_rate(mapping, "commission_pct", required=True),
        # A cash amount, not a rate: no conversion, and no division by 100.
        commission_min_flat=_non_negative(flat, "costs.commission_min_flat"),
        fx_conversion_frac=_rate(mapping, "fx_conversion_pct", required=False),
    )


def _validation(raw: Any) -> Validation:
    """Build the :class:`Validation` block and check its bounds.

    Args:
        raw: The parsed ``validation`` mapping.

    Returns:
        The pass criteria.

    Raises:
        ProtocolError: If a field is missing, mistyped, or out of range.
    """
    mapping = _as_mapping(raw, "validation")
    block = Validation(
        primary_metric=_as_str(
            _require(mapping, "primary_metric", "validation"), "validation.primary_metric"
        ),
        min_sharpe=_as_float(
            _require(mapping, "min_sharpe", "validation"), "validation.min_sharpe"
        ),
        min_trades_per_fold=_as_int(
            _require(mapping, "min_trades_per_fold", "validation"),
            "validation.min_trades_per_fold",
        ),
        min_trades_pooled=_as_int(
            _require(mapping, "min_trades_pooled", "validation"),
            "validation.min_trades_pooled",
        ),
        max_drawdown_ceiling_pct=_as_float(
            _require(mapping, "max_drawdown_ceiling_pct", "validation"),
            "validation.max_drawdown_ceiling_pct",
        ),
        pass_margin=_as_float(
            _require(mapping, "pass_margin", "validation"), "validation.pass_margin"
        ),
    )
    for name, count in (
        ("validation.min_trades_per_fold", block.min_trades_per_fold),
        ("validation.min_trades_pooled", block.min_trades_pooled),
    ):
        if count < 1:
            raise ProtocolError(f"protocol.yaml: field '{name}' must be >= 1, got {count}")
    if block.min_trades_pooled < block.min_trades_per_fold:
        raise ProtocolError(
            "protocol.yaml: 'validation.min_trades_pooled' "
            f"({block.min_trades_pooled}) is below "
            f"'validation.min_trades_per_fold' ({block.min_trades_per_fold}); the pooled "
            "floor covers every fold together and cannot be looser than the floor for one"
        )
    if block.max_drawdown_ceiling_pct <= 0:
        raise ProtocolError(
            "protocol.yaml: field 'validation.max_drawdown_ceiling_pct' must be > 0, "
            f"got {block.max_drawdown_ceiling_pct}"
        )
    _non_negative(block.pass_margin, "validation.pass_margin")
    return block


def _find_touch_line(text: str) -> re.Match[str]:
    """Locate the counter line inside the ``access_log`` mapping specifically.

    Searching the whole file for ``walk_forward_touches:`` would match a line of
    that name under any other key, and would silently increment it while
    reporting it as ``access_log.walk_forward_touches``. The search is therefore
    bounded to the region between the ``access_log:`` header and the next
    top-level key, and the line must be indented, so a top-level key of the same
    name cannot match.

    Args:
        text: Full text of the protocol file.

    Returns:
        The match for the counter line, carrying ``indent`` and ``count``.

    Raises:
        ProtocolError: If there is no ``access_log:`` block, or no indented
            counter line within it.
    """
    header = _ACCESS_LOG_HEADER.search(text)
    if header is None:
        raise ProtocolError("protocol.yaml: missing required field 'access_log'")
    following = _NEXT_TOP_LEVEL.search(text, header.end() + 1)
    region_end = following.start() if following is not None else len(text)
    match = _TOUCH_LINE.search(text, header.end(), region_end)
    if match is None:
        raise ProtocolError(
            "protocol.yaml: missing required field 'access_log.walk_forward_touches'"
        )
    return match


def load_protocol(path: Path = DEFAULT_PROTOCOL_PATH) -> Protocol:
    """Load and validate ``protocol.yaml``.

    This is the ONLY reader of that file; everything else in the system takes a
    :class:`Protocol` instance.

    Validation covers presence and type of every field; ordering within each
    window; non-overlap between the in-sample and walk-forward windows;
    non-negativity of every rate and of the access counter; ``fold_months`` at
    least one; both trade floors at least one and the pooled floor not below
    the per-fold one; and ``max_drawdown_ceiling_pct`` strictly positive.

    Cost rates are stored in ``protocol.yaml`` as percentages and returned as
    FRACTIONS. This function is the only place that conversion happens.

    Args:
        path: Path to the protocol file, relative to the working directory.

    Returns:
        The parsed and validated protocol.

    Raises:
        ProtocolError: On any failure, naming the offending field.
    """
    try:
        parsed = yaml.safe_load(_read_preserving_newlines(path))
    except yaml.YAMLError as exc:
        raise ProtocolError(f"protocol.yaml: not valid YAML: {exc}") from exc

    if parsed is None:
        raise ProtocolError(f"protocol.yaml: file at '{path}' is empty")
    root = _as_mapping(parsed, "<root>")

    instruments_raw = _as_mapping(_require(root, "instruments", ""), "instruments")
    instruments = Instruments(
        research=_as_str(
            _require(instruments_raw, "research", "instruments"), "instruments.research"
        ),
        execution=_as_str(
            _require(instruments_raw, "execution", "instruments"), "instruments.execution"
        ),
    )

    windows_raw = _as_mapping(_require(root, "windows", ""), "windows")
    in_sample = _window(_require(windows_raw, "in_sample", "windows"), "windows.in_sample")
    walk_forward_raw = _as_mapping(
        _require(windows_raw, "walk_forward", "windows"), "windows.walk_forward"
    )
    walk_forward = _window(walk_forward_raw, "windows.walk_forward")
    if in_sample.start <= walk_forward.end and walk_forward.start <= in_sample.end:
        raise ProtocolError(
            "protocol.yaml: 'windows.in_sample' and 'windows.walk_forward' overlap "
            f"({in_sample.start}..{in_sample.end} vs {walk_forward.start}..{walk_forward.end}); "
            "the search must never see the held-back window"
        )
    fold_months = _as_int(
        _require(walk_forward_raw, "fold_months", "windows.walk_forward"),
        "windows.walk_forward.fold_months",
    )
    if fold_months < 1:
        raise ProtocolError(
            "protocol.yaml: field 'windows.walk_forward.fold_months' must be >= 1, "
            f"got {fold_months}"
        )

    access_raw = _as_mapping(_require(root, "access_log", ""), "access_log")
    touches = _as_int(
        _require(access_raw, "walk_forward_touches", "access_log"),
        "access_log.walk_forward_touches",
    )
    if touches < 0:
        raise ProtocolError(
            f"protocol.yaml: field 'access_log.walk_forward_touches' must be >= 0, got {touches}"
        )

    return Protocol(
        protocol_version=_as_int(_require(root, "protocol_version", ""), "protocol_version"),
        created=_as_date(_require(root, "created", ""), "created"),
        instruments=instruments,
        windows=Windows(in_sample=in_sample, walk_forward=walk_forward, fold_months=fold_months),
        costs=_costs(_require(root, "costs", "")),
        validation=_validation(_require(root, "validation", "")),
        walk_forward_touches=touches,
    )


def increment_walk_forward_touches(path: Path = DEFAULT_PROTOCOL_PATH) -> int:
    """Increment and persist the held-back-window access counter.

    Rewrites only the ``walk_forward_touches`` line, leaving every other byte of
    the file — including its line endings — exactly as found. The comments in
    ``protocol.yaml`` are the rulebook, and a round trip through a YAML dumper
    would delete them.

    The counter is located inside the ``access_log`` mapping specifically, not
    by the first line of that name anywhere in the file.

    The rewrite is NOT atomic: the file is truncated and rewritten in place, so
    a failure mid-write can leave the pre-registration damaged. Keep it in
    version control, which is where its authoritative copy lives anyway.

    Args:
        path: Path to the protocol file, relative to the working directory.

    Returns:
        The new count, after incrementing.

    Raises:
        ProtocolError: If the file is missing or carries no
            ``access_log.walk_forward_touches`` line.
    """
    text = _read_preserving_newlines(path)
    match = _find_touch_line(text)
    new_count = int(match.group("count")) + 1
    # Splice over the DIGITS ONLY. Rewriting the whole line would normalise its
    # indentation and any trailing whitespace, which is a byte change to a line
    # this function is supposed to leave alone apart from the number.
    start, end = match.span("count")
    updated = text[:start] + str(new_count) + text[end:]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(updated)
    return new_count

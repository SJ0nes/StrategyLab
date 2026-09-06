"""The decision log: one structured record per decision, written at decision time.

Backtest and live emit the IDENTICAL schema. That is the whole point - it is
what makes ``strategylab diff`` possible, and without it "the live worker
behaved differently" is an anecdote rather than a diff.

Records are never reconstructed after the fact. A record rebuilt from prices
after the event records what the strategy WOULD have seen, not what it did see,
and the gap between those two is precisely the thing being measured.

WHAT THIS MODULE GUARANTEES, and it is a narrow list: that a record written here
comes back from :func:`read_decisions` EQUAL to the one written, and that a
malformed line is refused loudly rather than silently skipped. Round-tripping is
the property ``diff`` rests on. If reading loses a field, or widens a float, or
turns a tuple into a list, then two identical runs produce two logs that differ
and the difference is this module's fault rather than the strategy's.

THE THREE WAYS THAT GUARANTEE COULD HAVE BEEN FALSE ARE CLOSED BY REFUSING THE
INPUT, not by hoping. A non-string mapping key, which JSON coerces to a string
silently; a timestamp carrying precision finer than a microsecond, which
``datetime`` cannot hold and which a ``pandas.Timestamp`` from a bar index
carries by construction; and a ``recorded_at`` at some offset other than UTC,
which would come back as a different rendering of the same instant. Each is
rejected on write, so nothing reaches a log that cannot come back from it. The
one thing that IS reduced is a named timezone: ``ZoneInfo("UTC")`` comes back as
a fixed ``+00:00``. Records still compare equal, because datetimes compare by
instant, but the ``tzinfo`` object is not the one that went in.

THE READ PATH IS AS STRICT AS THE WRITE PATH, and an earlier version of this
module was not - it coerced seven fields with ``str()``, ``float()`` and
``int()``, so a ``ticker`` of ``null`` read back as the string ``"None"`` and a
``protocol_version`` of ``1.9`` was truncated to ``1``. Both directions now
refuse what the other would not have produced. A reader looser than its writer
is a reader that accepts logs this project cannot have written, and then ``diff``
compares one of them against a real one.

TWO DELIBERATE DIVERGENCES FROM :mod:`strategylab.search.graveyard`, the other
append-only JSON Lines writer here, stated because a reader of both will
otherwise assume they agree. A non-finite metric there becomes JSON ``null`` and
is argued for; a non-finite float HERE is refused, because a metric that could
not be computed is a fact worth recording while an indicator value that is not a
number means the evaluation read garbage and no decision should have followed.
Key sorting is the same in both, for the same reason: the bytes of a line are a
function of the record alone.

WHAT IT DOES NOT GUARANTEE. It does not decide how ``indicator_values`` and
``thresholds`` are KEYED - it serialises whatever mapping it is handed. The
contract says "keyed by indicator name", which is unambiguous only while no
strategy names the same indicator twice; ``close above sma(150)`` and
``close above sma(50)`` in one condition list would collide under any naming by
indicator alone. Settling that is the emitter's job and it is not settled here,
because the emitter does not exist yet and a convention invented in advance is a
convention the emitter will contradict. ``LIMITATIONS.md`` records the gap.

TWO TIMESTAMPS IN DIFFERENT FRAMES, deliberately, and a reader has to know which
is which. ``bar_timestamp`` is timezone-NAIVE, because that is what the bar
index is throughout this project - ``windows.py`` refuses a timezone-aware frame
outright. ``recorded_at`` is timezone-AWARE and in UTC, because it is wall-clock
time on a machine and an unqualified one cannot be compared against another
machine's. The asymmetry is not an oversight: in a backtest the two are hours or
years apart and the gap means nothing, while live the gap IS a measurement - how
long the worker took to decide after the bar closed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from strategylab.core.types import Action

DEFAULT_DECISION_LOG_PATH = Path("state/decisions.jsonl")

SCHEMA_VERSION: int = 1

#: The two values ``DecisionRecord.source`` may take. A closed set, checked on
#: write: ``diff`` pairs a backtest log against a live one, and a third value
#: appearing would mean one side of that pair was something nobody designed for.
SOURCES: frozenset[str] = frozenset({"backtest", "live"})


@dataclass(frozen=True)
class DecisionRecord:
    """Everything known at the moment one decision was made.

    Attributes:
        schema_version: Version of this record shape. A HARD GATE, not a
            compatibility hint: :func:`read_decisions` refuses any value but
            :data:`SCHEMA_VERSION`, and :func:`write_decision` refuses to write
            one. So a log written months ago can be read for exactly as long as
            nobody bumps that constant, and the session that bumps it either
            writes a reader for the old shape or accepts that existing logs
            become unreadable. Refusing beats the alternative, which is reading
            an old shape as though it were the new one.
        bar_timestamp: Timestamp of the bar being evaluated. Timezone-NAIVE,
            matching the bar index everywhere else in this project.
            MICROSECOND RESOLUTION: a ``pandas.Timestamp`` carrying non-zero
            nanoseconds is refused rather than silently truncated, because
            ``datetime`` cannot hold it and the record would not round-trip.
        recorded_at: Wall-clock time the record was written, timezone-AWARE and
            at UTC - an offset other than zero is refused, so the field means
            what its name says. Equal to the bar timestamp in a backtest and
            later than it when live, and the difference between the two is
            itself a measurement. See the module docstring for why the two
            timestamps are in different frames.
        source: Either ``backtest`` or ``live``. The only field whose value is
            expected to differ between the two logs being diffed.
        ticker: Symbol evaluated.
        action: What the strategy decided.
        indicator_values: Every indicator value the evaluation read, keyed by
            indicator name, in price units. Keys must be strings: JSON object
            keys are strings and an integer one would be coerced silently, so
            the record would not come back equal to itself.
        thresholds: Every threshold compared against, keyed by the same names,
            in PRICE UNITS as well - with one caveat the emitter inherits. A
            condition's right-hand side may be a bare literal rather than an
            indicator (``Condition.right`` is ``IndicatorRef | float``), and a
            literal's unit is whatever the config author meant by it. Nothing
            here can check that.
        conditions_met: Rendering of each condition that passed, in config
            order.
        conditions_failed: The same for each condition that did not pass. A
            hold is only explicable through this field, which is why rejections
            are logged as fully as entries.
        requested_size_pct: Size the strategy asked for, as a percentage of
            equity, so ``95.0`` means ninety-five percent.
        approved_size_pct: Size the risk layer permitted, as a percentage of
            equity. Equal to ``requested_size_pct`` when nothing bound, lower
            when a limit reduced it, and ``0.0`` on a rejection - which is the
            same value a ``HOLD`` or an ``EXIT_LONG`` carries in
            ``requested_size_pct``, so ``0.0`` alone does not distinguish
            "refused" from "nothing was asked for". Read it beside
            ``risk_verdict``, which does.
        risk_verdict: The risk layer's answer, as its string value - the closed
            vocabulary of :class:`~strategylab.risk.limits.Verdict`. NOT
            validated against it here, on write or on read, because that layer
            is a stub and nothing can produce one yet; when it exists, this is
            the field that should gain the check ``source`` already has.
        risk_reason: Why the risk layer answered as it did.
        params: The resolved parameters in force, so a record is readable
            without its config. Values are in BARS.
        protocol_version: Version of the protocol in force, so a record is
            traceable to the rules that produced it.
    """

    schema_version: int
    bar_timestamp: datetime
    recorded_at: datetime
    source: str
    ticker: str
    action: Action
    indicator_values: dict[str, float]
    thresholds: dict[str, float]
    conditions_met: tuple[str, ...]
    conditions_failed: tuple[str, ...]
    requested_size_pct: float
    approved_size_pct: float
    risk_verdict: str
    risk_reason: str
    params: dict[str, int]
    protocol_version: int


def _payload(record: DecisionRecord) -> dict[str, Any]:
    """Render one record as the JSON object written to a line.

    Every key here is read back by :func:`_record`, and the two must stay in
    step: a field added to one and not the other is a field ``diff`` silently
    stops comparing.

    Args:
        record: The record to render.

    Returns:
        A JSON-serialisable object, with the enum reduced to its value and both
        timestamps to ISO 8601.
    """
    return {
        "schema_version": record.schema_version,
        "bar_timestamp": record.bar_timestamp.isoformat(),
        "recorded_at": record.recorded_at.isoformat(),
        "source": record.source,
        "ticker": record.ticker,
        "action": record.action.value,
        "indicator_values": dict(record.indicator_values),
        "thresholds": dict(record.thresholds),
        "conditions_met": list(record.conditions_met),
        "conditions_failed": list(record.conditions_failed),
        "requested_size_pct": record.requested_size_pct,
        "approved_size_pct": record.approved_size_pct,
        "risk_verdict": record.risk_verdict,
        "risk_reason": record.risk_reason,
        "params": dict(record.params),
        "protocol_version": record.protocol_version,
    }


def _string_keys(mapping: dict[str, Any], field: str) -> None:
    """Refuse a mapping whose keys are not all strings.

    JSON object keys are strings and :func:`json.dumps` coerces an integer one
    without complaint, so ``{5: 10}`` is written as ``{"5": 10}`` and comes back
    unequal to what went in. That breaks the round trip this module's docstring
    promises, silently, for a record whose annotation said ``dict[str, ...]``
    and whose dataclass did nothing to enforce it.

    :func:`strategylab.search.graveyard.record_rejection` refuses exactly this,
    for exactly this reason. The two writers agree.

    Args:
        mapping: The mapping to check.
        field: Field name, for the message.

    Raises:
        ValueError: If any key is not a string.
    """
    unnamed = sorted(str(key) for key in mapping if not isinstance(key, str))
    if unnamed:
        raise ValueError(
            f"'{field}' has non-string key(s) {unnamed}; JSON object keys are strings, and "
            "json would coerce these silently - the record would not come back equal to "
            "the one written"
        )


def _round_trippable(stamp: datetime, field: str) -> None:
    """Refuse a timestamp that cannot survive ISO 8601 and come back equal.

    THE CASE THIS EXISTS FOR IS A ``pandas.Timestamp``, which the emitter will
    take straight from a bar index. It subclasses :class:`datetime.datetime` and
    carries NANOSECONDS; ``isoformat`` renders them and
    :meth:`datetime.datetime.fromisoformat` returns a plain ``datetime``, whose
    resolution stops at microseconds. The value would be silently truncated and
    the record would not equal itself across a write and a read.

    Checked by round-tripping the rendering rather than by reaching for a pandas
    attribute, so this module needs no opinion about what type it was handed.

    Args:
        stamp: The timestamp to check.
        field: Field name, for the message.

    Raises:
        ValueError: If rendering and reparsing does not reproduce the instant.
    """
    if datetime.fromisoformat(stamp.isoformat()) != stamp:
        raise ValueError(
            f"'{field}' is {stamp!r}, which does not survive ISO 8601 unchanged - a "
            "timestamp carrying precision finer than a microsecond, such as a pandas "
            "Timestamp with nanoseconds, would be truncated and the record would not read "
            "back equal to itself"
        )


def _line(record: DecisionRecord) -> str:
    """Validate one record and render it as a single JSON line.

    Args:
        record: The record to render.

    Returns:
        The JSON object followed by a newline, keys sorted. SORTED for the
        reason :mod:`strategylab.search.graveyard` gives: it makes the bytes of
        a line a function of the record alone, rather than of the order an
        emitter happened to build its mappings in.

    Raises:
        ValueError: If ``source`` is outside :data:`SOURCES`; if
            ``schema_version`` is not :data:`SCHEMA_VERSION`; if ``action`` is
            not an :class:`~strategylab.core.types.Action`; if ``recorded_at``
            is timezone-naive or is not at UTC, or ``bar_timestamp`` is
            timezone-aware; if either timestamp carries precision finer than a
            microsecond; if a mapping has a non-string key; if any float is not
            finite; or if any value is not JSON-serialisable, which arrives as
            a ``TypeError`` and is re-raised as a ``ValueError`` rather than
            escaping a clause that does not admit it.
    """
    if record.source not in SOURCES:
        raise ValueError(
            f"source is {record.source!r}, which is not one of {sorted(SOURCES)}; `diff` "
            "pairs a backtest log against a live one and has no third case"
        )
    if record.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version is {record.schema_version}, and this build writes version "
            f"{SCHEMA_VERSION}. Writing a version this code does not produce would put two "
            "shapes in one append-only file"
        )
    # Checked rather than trusted: the dataclass is not runtime-typed, so
    # `action="hold"` is constructible and would reach `.value` as an
    # AttributeError - a type this function's contract does not admit.
    if not isinstance(record.action, Action):
        raise ValueError(
            f"action is {record.action!r}, which is not an Action; the record would fail "
            "on the way to JSON with an error naming neither the field nor the log"
        )

    # The two frames, checked rather than assumed. A naive `recorded_at` cannot
    # be compared against another machine's, and an aware `bar_timestamp` would
    # not equal the bar index it came from - which is how a diff would come to
    # report every bar as differing.
    if record.recorded_at.tzinfo is None:
        raise ValueError(
            "recorded_at is timezone-naive; it is wall-clock time on a machine and an "
            "unqualified one cannot be compared against another machine's"
        )
    if record.recorded_at.utcoffset() != timedelta(0):
        raise ValueError(
            f"recorded_at is at offset {record.recorded_at.utcoffset()} and this field is "
            "documented as UTC. Accepting any offset would make two logs from two machines "
            "compare by a rendering rather than by an instant"
        )
    if record.bar_timestamp.tzinfo is not None:
        raise ValueError(
            f"bar_timestamp carries a timezone ({record.bar_timestamp.tzinfo}) and the bar "
            "index it comes from is naive throughout this project; a diff against a naive "
            "log would report every bar as different"
        )
    _round_trippable(record.bar_timestamp, "bar_timestamp")
    _round_trippable(record.recorded_at, "recorded_at")

    for field, mapping in (
        ("indicator_values", record.indicator_values),
        ("thresholds", record.thresholds),
        ("params", record.params),
    ):
        _string_keys(dict(mapping), field)

    # allow_nan=False, so a non-finite value raises here rather than writing a
    # bare `NaN` token that a strict parser refuses. It should be unreachable:
    # `core.strategy` already refuses to decide on a non-finite indicator. That
    # is the argument for checking rather than for trusting.
    #
    # TypeError is translated rather than propagated, as graveyard.py does: a
    # value json cannot represent - a Decimal, an enum, a set - would otherwise
    # escape as a type this contract does not admit.
    try:
        return json.dumps(_payload(record), allow_nan=False, sort_keys=True) + "\n"
    except TypeError as exc:
        raise ValueError(f"record is not serialisable as JSON: {exc}") from exc


def write_decision(record: DecisionRecord, path: Path = DEFAULT_DECISION_LOG_PATH) -> None:
    """Append one decision record to the log as a single JSON line.

    Append-only, one JSON object per line, so a crash mid-write costs at most
    the final record rather than the file.

    THAT BOUND IS ON THIS FUNCTION AND NOT ON :func:`write_decisions`, which
    hands N lines to one buffered write: a crash inside it can truncate the
    last line of the batch, and :func:`read_decisions` then refuses the whole
    log rather than the damaged record. The two promises - crash costs one
    record, malformed line refuses everything - pull against each other, and
    which one you get depends on which writer produced the log.

    THE RECORD IS RENDERED BEFORE THE FILE IS OPENED, so a record this module
    refuses cannot leave a partial line behind. Validation failures are the
    common case here - a wrong ``source``, a naive timestamp - and none of them
    should cost the log its parseability.

    Use :func:`write_decisions` for many records at once. This function opens
    and closes the file per call, which is right for a live worker deciding
    once a bar and wrong for a backtest emitting thousands.

    Args:
        record: The record to write.
        path: Log file. A relative path - and the default is one - resolves
            against the WORKING DIRECTORY, not the repository root; the two
            coincide only because every command in this project is documented
            as run from the root. Created along with its parent directory if
            absent.

    Returns:
        None.

    Raises:
        ValueError: If ``record.source`` is neither ``backtest`` nor ``live``,
            if ``record.schema_version`` does not match :data:`SCHEMA_VERSION`,
            if ``recorded_at`` is timezone-naive or ``bar_timestamp`` is
            timezone-aware, or if the record holds a non-finite float. Nothing
            is written in any of these cases.
        OSError: Propagated unchanged if the file cannot be written.
    """
    write_decisions((record,), path)


def write_decisions(
    records: Sequence[DecisionRecord] | Iterable[DecisionRecord],
    path: Path = DEFAULT_DECISION_LOG_PATH,
) -> int:
    """Append many decision records in one open, in the order given.

    NOT IN THE ORIGINAL CONTRACT, and added because the backtester needs it: a
    run over the in-sample window emits several thousand records, and opening
    the file once per bar turns a decision log into the slowest part of a
    backtest. The format is identical - this is the same lines through the same
    renderer, and :func:`read_decisions` cannot tell which function wrote them.

    EVERY RECORD IS RENDERED BEFORE ANY IS WRITTEN. A batch containing one bad
    record writes nothing at all, rather than appending the good ones up to the
    failure and leaving the caller to work out how far it got.

    Args:
        records: The records to write, in order.
        path: Log file, resolved as :func:`write_decision` describes. Created
            along with its parent directory if absent - EXCEPT for an empty
            batch, which creates nothing at all and returns zero.

    Returns:
        How many records were appended.

    Raises:
        ValueError: On the first record that fails validation - see
            :func:`write_decision`. Nothing is written.
        OSError: Propagated unchanged if the file cannot be written.
    """
    lines = [_line(record) for record in records]
    if not lines:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.writelines(lines)
    return len(lines)


def _text(payload: dict[str, Any], key: str, line_number: int) -> str:
    """Read a string field, refusing anything that is not already a string.

    NOT ``str(...)``, which is what this did first and which coerced silently:
    a ``ticker`` of ``null`` became the string ``"None"`` and a ``source`` of
    ``5`` became ``"5"``. Both would then be diffed against a real log.

    Args:
        payload: The parsed line.
        key: Field to read.
        line_number: One-based line, for the message.

    Returns:
        The string.

    Raises:
        ValueError: If the value is not a string.
    """
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"line {line_number}: '{key}' is {value!r}, which is not a string")
    return value


def _number(payload: dict[str, Any], key: str, line_number: int) -> float:
    """Read a finite numeric field, refusing a bool, a string or a non-finite value.

    ``float("95.0")`` and ``float(True)`` both succeed, which is why this does
    not call ``float`` on whatever it is given. The finiteness check is the read
    side of the rule :func:`_line` enforces on write: ``json.loads`` accepts the
    bare tokens ``NaN`` and ``Infinity``, so without it this reader would hand
    back records its own writer refuses to produce.

    Args:
        payload: The parsed line.
        key: Field to read.
        line_number: One-based line, for the message.

    Returns:
        The value as a float.

    Raises:
        ValueError: If it is not a real number, or is not finite.
    """
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"line {line_number}: '{key}' is {value!r}, which is not a number")
    if not math.isfinite(value):
        raise ValueError(
            f"line {line_number}: '{key}' is {value}, which is not finite; the writer "
            "refuses to produce one, so a log carrying it was not written by this project"
        )
    return float(value)


def _whole(payload: dict[str, Any], key: str, line_number: int) -> int:
    """Read a field that must be a whole number, refusing a truncation.

    ``int(1.9)`` is ``1``. A ``protocol_version`` of ``1.9`` silently becoming
    version 1 would make a record claim traceability to rules it was not
    produced under.

    Args:
        payload: The parsed line.
        key: Field to read.
        line_number: One-based line, for the message.

    Returns:
        The value as an int.

    Raises:
        ValueError: If it is not a whole number.
    """
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise ValueError(f"line {line_number}: '{key}' is {value!r}, which is not a whole number")
    return int(value)


def _stamp(payload: dict[str, Any], key: str, line_number: int, *, aware: bool) -> datetime:
    """Read a timestamp and check it is in the frame this field is documented in.

    THE FRAMES ARE CHECKED ON THE WAY IN AS WELL AS ON THE WAY OUT. The module
    docstring's longest paragraph is about the asymmetry between the two
    timestamps, and an earlier version of this reader enforced none of it: a
    line carrying an aware ``bar_timestamp`` read straight back, which is the
    exact state the writer calls out as "how a diff would come to report every
    bar as differing".

    Args:
        payload: The parsed line.
        key: Field to read.
        line_number: One-based line, for the message.
        aware: Whether this field must carry a timezone at UTC. ``False``
            means it must carry none at all.

    Returns:
        The parsed timestamp.

    Raises:
        ValueError: If the value is not an ISO 8601 STRING - an integer is
            refused rather than being read as a basic-format date - or is in
            the wrong frame.
    """
    raw = _text(payload, key, line_number)
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"line {line_number}: '{key}' is not ISO 8601: {exc}") from exc

    if aware:
        if stamp.tzinfo is None:
            raise ValueError(f"line {line_number}: '{key}' carries no timezone and must")
        if stamp.utcoffset() != timedelta(0):
            raise ValueError(
                f"line {line_number}: '{key}' is at offset {stamp.utcoffset()} and this "
                "field is documented as UTC"
            )
    elif stamp.tzinfo is not None:
        raise ValueError(
            f"line {line_number}: '{key}' carries a timezone and the bar index it comes "
            "from is naive throughout this project"
        )
    return stamp


def _floats(raw: Any, field: str, line_number: int) -> dict[str, float]:
    """Read one of the two float mappings back, checking its shape.

    Args:
        raw: The parsed value.
        field: Field name, for the message.
        line_number: One-based line the record came from.

    Returns:
        The mapping, every value a ``float``.

    Raises:
        ValueError: If it is not an object of numbers.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"line {line_number}: '{field}' is not an object")
    values: dict[str, float] = {}
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(
                f"line {line_number}: '{field}.{name}' is {value!r}, which is not a number"
            )
        if not math.isfinite(value):
            raise ValueError(
                f"line {line_number}: '{field}.{name}' is {value}, which is not finite. "
                "The writer refuses to produce one - an indicator value that is not a "
                "number means the evaluation read garbage and no decision should have "
                "followed it"
            )
        values[str(name)] = float(value)
    return values


def _strings(raw: Any, field: str, line_number: int) -> tuple[str, ...]:
    """Read one of the rendered-condition lists back as a tuple.

    A TUPLE, not the list JSON gives back. ``DecisionRecord`` declares tuples,
    and a record read as lists would compare unequal to the identical record
    before it was written - which would make every ``diff`` report differences
    that are this module's doing.

    Args:
        raw: The parsed value.
        field: Field name, for the message.
        line_number: One-based line the record came from.

    Returns:
        The strings, in the order stored.

    Raises:
        ValueError: If it is not an array of strings.
    """
    if not isinstance(raw, list):
        raise ValueError(f"line {line_number}: '{field}' is not an array")
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"line {line_number}: '{field}' holds {item!r}, which is not a string")
    return tuple(raw)


def _record(payload: Any, line_number: int) -> DecisionRecord:
    """Rebuild one record from a parsed line.

    Args:
        payload: The parsed JSON object.
        line_number: One-based line it came from, named in every message so a
            bad record in a long log can be found.

    Returns:
        The record.

    Raises:
        ValueError: If the object is malformed, carries an unknown schema
            version, or names an action outside the vocabulary.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"line {line_number}: not a JSON object; it is a {type(payload).__name__}")

    # `is not` rather than `!=`, and an explicit int check, because `True == 1`
    # and `1.0 == 1` in Python: a line carrying `"schema_version": true` would
    # otherwise pass this gate and be laundered into the int 1 below.
    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise ValueError(
            f"line {line_number}: schema_version is {version!r}, and this build reads "
            f"version {SCHEMA_VERSION}"
        )

    missing = [
        key
        for key in (
            "bar_timestamp",
            "recorded_at",
            "source",
            "ticker",
            "action",
            "indicator_values",
            "thresholds",
            "conditions_met",
            "conditions_failed",
            "requested_size_pct",
            "approved_size_pct",
            "risk_verdict",
            "risk_reason",
            "params",
            "protocol_version",
        )
        if key not in payload
    ]
    if missing:
        raise ValueError(f"line {line_number}: missing field(s) {missing}")

    try:
        action = Action(payload["action"])
    except ValueError as exc:
        raise ValueError(
            f"line {line_number}: action is {payload['action']!r}, which is not one of "
            f"{sorted(member.value for member in Action)}"
        ) from exc

    bar_timestamp = _stamp(payload, "bar_timestamp", line_number, aware=False)
    recorded_at = _stamp(payload, "recorded_at", line_number, aware=True)

    params_raw = payload["params"]
    if not isinstance(params_raw, dict):
        raise ValueError(f"line {line_number}: 'params' is not an object")
    params: dict[str, int] = {}
    for name, value in params_raw.items():
        if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
            raise ValueError(
                f"line {line_number}: 'params.{name}' is {value!r}, which is not a whole "
                "number of bars"
            )
        params[str(name)] = int(value)

    source = _text(payload, "source", line_number)
    if source not in SOURCES:
        raise ValueError(
            f"line {line_number}: source is {source!r}, which is not one of "
            f"{sorted(SOURCES)}. The writer refuses to produce one, so a log carrying it "
            "was not written by this project"
        )

    return DecisionRecord(
        # Validated above and then regenerated from the constant rather than
        # copied: the gate has already refused anything else, so the two are the
        # same value, and taking it from the constant means the returned record
        # cannot carry a version this build does not implement.
        schema_version=SCHEMA_VERSION,
        bar_timestamp=bar_timestamp,
        recorded_at=recorded_at,
        source=source,
        ticker=_text(payload, "ticker", line_number),
        action=action,
        indicator_values=_floats(payload["indicator_values"], "indicator_values", line_number),
        thresholds=_floats(payload["thresholds"], "thresholds", line_number),
        conditions_met=_strings(payload["conditions_met"], "conditions_met", line_number),
        conditions_failed=_strings(payload["conditions_failed"], "conditions_failed", line_number),
        requested_size_pct=_number(payload, "requested_size_pct", line_number),
        approved_size_pct=_number(payload, "approved_size_pct", line_number),
        risk_verdict=_text(payload, "risk_verdict", line_number),
        risk_reason=_text(payload, "risk_reason", line_number),
        params=params,
        protocol_version=_whole(payload, "protocol_version", line_number),
    )


def read_decisions(path: Path = DEFAULT_DECISION_LOG_PATH) -> list[DecisionRecord]:
    """Read a decision log back into records.

    A MALFORMED LINE RAISES RATHER THAN BEING SKIPPED. A log with a hole in it
    that nobody is told about is worse than one that will not open: ``diff``
    would report the missing bars as a divergence between backtest and live,
    which is a finding about the simulator that never happened.

    Blank lines are ignored, because a trailing newline is not a missing
    record.

    Args:
        path: Log file, resolved as :func:`write_decision` describes.

    Returns:
        Every record in the file, in the order written. THE WHOLE FILE IS READ
        INTO MEMORY. The format permits incremental reading and this function
        does not offer it; a log large enough for that to matter is a log this
        function is the wrong tool for.

    Raises:
        FileNotFoundError: If the log does not exist. Propagated unchanged
            rather than turned into an empty list: "no log" and "a log with no
            records in it" are different answers, and only one of them means
            the worker ran.
        ValueError: If a line is not JSON, is not an object, carries an unknown
            schema version, omits a field, or holds a value of the wrong shape,
            the wrong type, a non-finite number, a timestamp in the wrong frame,
            or a ``source`` outside :data:`SOURCES`. The message names the
            ONE-BASED line number, because a log is thousands of lines long and
            "malformed record" without a position is not actionable.

            WITH ONE EXCEPTION THAT CANNOT NAME A LINE. A file that is not valid
            UTF-8 fails while being decoded, before there are any lines to
            number, and ``UnicodeDecodeError`` is a ``ValueError`` subclass - so
            it satisfies this clause and not the sentence above it.
        OSError: Propagated unchanged if the file cannot be read for any other
            reason.
    """
    text = path.read_text(encoding="utf-8")

    records: list[DecisionRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"line {line_number}: not valid JSON: {exc}") from exc
        records.append(_record(payload, line_number))
    return records

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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
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
        schema_version: Version of this record shape, so a log written months
            ago can still be read.
        bar_timestamp: Timestamp of the bar being evaluated. Timezone-NAIVE,
            matching the bar index everywhere else in this project.
        recorded_at: UTC wall-clock time the record was written. Equal to the
            bar timestamp in a backtest and later than it when live, and the
            difference between the two is itself a measurement. Timezone-AWARE;
            see the module docstring for why the two differ.
        source: Either ``backtest`` or ``live``. The only field whose value is
            expected to differ between the two logs being diffed.
        ticker: Symbol evaluated.
        action: What the strategy decided.
        indicator_values: Every indicator value the evaluation read, keyed by
            indicator name, in price units.
        thresholds: Every threshold compared against, keyed by the same names.
        conditions_met: Rendering of each condition that passed, in config
            order.
        conditions_failed: The same for each condition that did not pass. A
            hold is only explicable through this field, which is why rejections
            are logged as fully as entries.
        requested_size_pct: Size the strategy asked for, as a percentage of
            equity, so ``95.0`` means ninety-five percent.
        approved_size_pct: Size the risk layer permitted, as a percentage of
            equity. Equal to ``requested_size_pct`` when nothing trimmed it.
        risk_verdict: The risk layer's answer, as its string value.
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


def _line(record: DecisionRecord) -> str:
    """Validate one record and render it as a single JSON line.

    Args:
        record: The record to render.

    Returns:
        The JSON object followed by a newline.

    Raises:
        ValueError: If ``source`` is outside :data:`SOURCES`, if
            ``schema_version`` is not :data:`SCHEMA_VERSION`, if
            ``recorded_at`` is timezone-naive or ``bar_timestamp`` is
            timezone-aware, or if any float in the record is not finite.
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
    # The two frames, checked rather than assumed. A naive `recorded_at` cannot
    # be compared against another machine's, and an aware `bar_timestamp` would
    # not equal the bar index it came from - which is how a diff would come to
    # report every bar as differing.
    if record.recorded_at.tzinfo is None:
        raise ValueError(
            "recorded_at is timezone-naive; it is wall-clock time on a machine and an "
            "unqualified one cannot be compared against another machine's"
        )
    if record.bar_timestamp.tzinfo is not None:
        raise ValueError(
            f"bar_timestamp carries a timezone ({record.bar_timestamp.tzinfo}) and the bar "
            "index it comes from is naive throughout this project; a diff against a naive "
            "log would report every bar as different"
        )

    # allow_nan=False, so a non-finite value raises here rather than writing a
    # bare `NaN` token that a strict parser refuses. It should be unreachable:
    # `core.strategy` already refuses to decide on a non-finite indicator. That
    # is the argument for checking rather than for trusting.
    return json.dumps(_payload(record), allow_nan=False) + "\n"


def write_decision(record: DecisionRecord, path: Path = DEFAULT_DECISION_LOG_PATH) -> None:
    """Append one decision record to the log as a single JSON line.

    Append-only, one JSON object per line, so a log can be read incrementally
    and a crash mid-write costs at most the final record rather than the file.

    THE RECORD IS RENDERED BEFORE THE FILE IS OPENED, so a record this module
    refuses cannot leave a partial line behind. Validation failures are the
    common case here - a wrong ``source``, a naive timestamp - and none of them
    should cost the log its parseability.

    Use :func:`write_decisions` for many records at once. This function opens
    and closes the file per call, which is right for a live worker deciding
    once a bar and wrong for a backtest emitting thousands.

    Args:
        record: The record to write.
        path: Log file, relative to the repository root. Created along with its
            parent directory if absent.

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
        path: Log file, relative to the repository root. Created along with its
            parent directory if absent.

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

    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
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

    try:
        bar_timestamp = datetime.fromisoformat(str(payload["bar_timestamp"]))
        recorded_at = datetime.fromisoformat(str(payload["recorded_at"]))
    except ValueError as exc:
        raise ValueError(f"line {line_number}: a timestamp is not ISO 8601: {exc}") from exc

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

    return DecisionRecord(
        schema_version=SCHEMA_VERSION,
        bar_timestamp=bar_timestamp,
        recorded_at=recorded_at,
        source=str(payload["source"]),
        ticker=str(payload["ticker"]),
        action=action,
        indicator_values=_floats(payload["indicator_values"], "indicator_values", line_number),
        thresholds=_floats(payload["thresholds"], "thresholds", line_number),
        conditions_met=_strings(payload["conditions_met"], "conditions_met", line_number),
        conditions_failed=_strings(payload["conditions_failed"], "conditions_failed", line_number),
        requested_size_pct=float(payload["requested_size_pct"]),
        approved_size_pct=float(payload["approved_size_pct"]),
        risk_verdict=str(payload["risk_verdict"]),
        risk_reason=str(payload["risk_reason"]),
        params=params,
        protocol_version=int(payload["protocol_version"]),
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
        path: Log file, relative to the repository root.

    Returns:
        Every record in the file, in the order written.

    Raises:
        FileNotFoundError: If the log does not exist. Propagated unchanged
            rather than turned into an empty list: "no log" and "a log with no
            records in it" are different answers, and only one of them means
            the worker ran.
        ValueError: If a line is not JSON, is not an object, carries an unknown
            schema version, omits a field, or holds a value of the wrong shape.
            The message names the ONE-BASED line number, because a log is
            thousands of lines long and "malformed record" without a position
            is not actionable.
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

"""Tests for the decision-log schema shared by the backtester and the live worker.

**Round-tripping is the property under test**, because it is the one `diff`
rests on. If reading loses a field, widens a float, or hands back a list where a
tuple was written, then two identical runs produce two logs that differ and the
difference is this module's doing rather than the strategy's. Several tests here
assert equality of the whole record rather than field by field, so a field added
to the writer and not the reader fails rather than being silently dropped.

**Nothing here writes to `state/`.** Every test writes into `tmp_path`. The
default log path is a real location this project uses, and a test appending to
an append-only file in it would leave records behind that no run produced.

**What these tests cannot see.** Whether the emitters agree. Nothing emits a
`DecisionRecord` yet - the backtester still writes its own seven-column frame -
so "backtest and live emit the identical schema" is a property of a design and
not yet of any code. `LIMITATIONS.md` says so.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from strategylab.core.types import Action
from strategylab.logging.decisions import (
    SCHEMA_VERSION,
    SOURCES,
    DecisionRecord,
    read_decisions,
    write_decision,
    write_decisions,
)

BAR = datetime(2024, 3, 15, 0, 0, 0)
RECORDED = datetime(2024, 3, 15, 21, 5, 30, 123456, tzinfo=UTC)


def _record(**overrides: Any) -> DecisionRecord:
    """A complete record, with any field overridable one at a time."""
    fields: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "bar_timestamp": BAR,
        "recorded_at": RECORDED,
        "source": "backtest",
        "ticker": "SPY",
        "action": Action.ENTER_LONG,
        "indicator_values": {"sma_fast": 412.3456, "sma_slow": 405.125, "close": 414.0},
        "thresholds": {"sma_fast": 405.125, "close": 398.75},
        "conditions_met": ("sma(10)=412.346 crosses_above sma(80)=405.125",),
        "conditions_failed": ("close=414 above sma(150)=418.5",),
        "requested_size_pct": 95.0,
        "approved_size_pct": 95.0,
        "risk_verdict": "allow",
        "risk_reason": "no limit bound",
        "params": {"fast": 10, "slow": 80, "trend": 150},
        "protocol_version": 1,
    }
    fields.update(overrides)
    return DecisionRecord(**fields)


# --------------------------------------------------------------------------
# The round trip. Everything `diff` depends on is in here.
# --------------------------------------------------------------------------


def test_a_record_survives_the_round_trip_intact(tmp_path: Path) -> None:
    # Whole-record equality, not field by field: a field the writer emits and
    # the reader drops fails here, and would otherwise show up as a phantom
    # divergence between a backtest log and a live one.
    original = _record()
    path = tmp_path / "decisions.jsonl"

    write_decision(original, path)

    assert read_decisions(path) == [original]


def test_the_condition_lists_come_back_as_tuples_not_lists(tmp_path: Path) -> None:
    # JSON has one array type. A record read back as lists would compare
    # unequal to the identical record before it was written.
    path = tmp_path / "decisions.jsonl"
    write_decision(_record(), path)

    [restored] = read_decisions(path)

    assert isinstance(restored.conditions_met, tuple)
    assert isinstance(restored.conditions_failed, tuple)


def test_both_timestamps_keep_their_frames_and_their_microseconds(tmp_path: Path) -> None:
    # The asymmetry is deliberate: a naive bar timestamp matching the bar index,
    # an aware recorded_at because it is wall-clock time on a machine.
    path = tmp_path / "decisions.jsonl"
    write_decision(_record(), path)

    [restored] = read_decisions(path)

    assert restored.bar_timestamp == BAR
    assert restored.bar_timestamp.tzinfo is None
    assert restored.recorded_at == RECORDED
    assert restored.recorded_at.tzinfo is not None
    assert restored.recorded_at.microsecond == 123456, "sub-second precision is a measurement"


def test_a_float_is_not_widened_or_rounded_by_the_round_trip(tmp_path: Path) -> None:
    # An indicator value is a price. A log that rounds it records a decision
    # made on numbers the strategy never saw.
    awkward = 412.34567890123456
    path = tmp_path / "decisions.jsonl"

    write_decision(_record(indicator_values={"sma_fast": awkward}), path)
    [restored] = read_decisions(path)

    assert restored.indicator_values["sma_fast"] == awkward


def test_every_action_in_the_vocabulary_round_trips(tmp_path: Path) -> None:
    # HOLD especially: it is a real answer rather than the absence of one, and
    # a log that could not represent it would drop most of its rows.
    path = tmp_path / "decisions.jsonl"
    records = [_record(action=action) for action in Action]

    write_decisions(records, path)

    assert [restored.action for restored in read_decisions(path)] == list(Action)


def test_records_come_back_in_the_order_they_were_written(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    days = [_record(bar_timestamp=BAR + timedelta(days=offset)) for offset in range(5)]

    write_decisions(days, path)

    assert [restored.bar_timestamp for restored in read_decisions(path)] == [
        record.bar_timestamp for record in days
    ]


def test_appending_adds_to_a_log_rather_than_replacing_it(tmp_path: Path) -> None:
    # Append-only is the whole storage model: a live worker writes one record a
    # bar over months, and a writer that truncated would lose the run so far.
    path = tmp_path / "decisions.jsonl"

    write_decision(_record(ticker="SPY"), path)
    write_decision(_record(ticker="QQQ"), path)
    write_decisions([_record(ticker="IWM")], path)

    assert [restored.ticker for restored in read_decisions(path)] == ["SPY", "QQQ", "IWM"]


def test_the_log_is_one_json_object_per_line(tmp_path: Path) -> None:
    # The format is a promise to anything that reads it incrementally, and to
    # anything that is not Python.
    path = tmp_path / "decisions.jsonl"
    write_decisions([_record(), _record(action=Action.HOLD)], path)

    lines = path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    for line in lines:
        assert isinstance(json.loads(line), dict)


def test_the_parent_directory_is_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "decisions.jsonl"

    write_decision(_record(), path)

    assert path.is_file()


# --------------------------------------------------------------------------
# What the writer refuses, and the fact that it writes nothing when it does.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["", "paper", "BACKTEST", "sim"])
def test_a_source_outside_the_pair_is_refused(tmp_path: Path, source: str) -> None:
    # `diff` pairs a backtest log against a live one. A third value would mean
    # one side of that pair was something nobody designed for - and "BACKTEST"
    # is in the list because a case-insensitive match would let it through.
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError, match="not one of"):
        write_decision(_record(source=source), path)

    assert not path.exists(), "a refused record must not create the log"


def test_only_backtest_and_live_are_accepted() -> None:
    assert sorted(SOURCES) == ["backtest", "live"]


def test_a_foreign_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError, match="schema_version"):
        write_decision(_record(schema_version=SCHEMA_VERSION + 1), path)

    assert not path.exists()


def test_a_naive_recorded_at_is_refused(tmp_path: Path) -> None:
    # Wall-clock time on a machine. Unqualified, it cannot be compared against
    # another machine's, which is the comparison `diff` exists to make.
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError, match="recorded_at is timezone-naive"):
        write_decision(_record(recorded_at=datetime(2024, 3, 15, 21, 5)), path)


def test_an_aware_bar_timestamp_is_refused(tmp_path: Path) -> None:
    # The bar index is naive throughout this project. An aware one here would
    # not equal the bar it came from, and a diff would report every bar as
    # different for a reason that has nothing to do with the strategy.
    path = tmp_path / "decisions.jsonl"
    aware = datetime(2024, 3, 15, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(ValueError, match="bar_timestamp carries a timezone"):
        write_decision(_record(bar_timestamp=aware), path)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_indicator_value_is_refused(tmp_path: Path, bad: float) -> None:
    # Should be unreachable - `core.strategy` refuses to decide on a non-finite
    # indicator - which is the argument for checking rather than for trusting.
    # A bare NaN token would also make the line unreadable to a strict parser.
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError):
        write_decision(_record(indicator_values={"sma_fast": bad}), path)

    assert not path.exists()


def test_one_bad_record_in_a_batch_writes_none_of_them(tmp_path: Path) -> None:
    # Otherwise the caller is left to work out how far the batch got, against
    # an append-only file they cannot rewind.
    path = tmp_path / "decisions.jsonl"
    batch = [_record(), _record(), _record(source="nonsense"), _record()]

    with pytest.raises(ValueError, match="not one of"):
        write_decisions(batch, path)

    assert not path.exists()


def test_a_bad_record_does_not_damage_an_existing_log(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_decision(_record(ticker="SPY"), path)

    with pytest.raises(ValueError):
        write_decision(_record(source="nonsense"), path)

    assert [restored.ticker for restored in read_decisions(path)] == ["SPY"]


def test_writing_nothing_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"

    assert write_decisions([], path) == 0
    assert not path.exists()


def test_the_batch_writer_reports_how_many_it_wrote(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"

    assert write_decisions([_record(), _record(), _record()], path) == 3


# --------------------------------------------------------------------------
# What the reader refuses. A hole in a log reads as a divergence.
# --------------------------------------------------------------------------


def test_a_missing_log_raises_rather_than_returning_nothing(tmp_path: Path) -> None:
    # "No log" and "a log with no records in it" are different answers, and
    # only one of them means the worker ran.
    with pytest.raises(FileNotFoundError):
        read_decisions(tmp_path / "absent.jsonl")


def test_an_empty_log_reads_as_no_records(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    path.write_text("", encoding="utf-8")

    assert read_decisions(path) == []


def test_blank_lines_are_not_missing_records(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    write_decisions([_record(), _record(action=Action.HOLD)], path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n   \n")

    assert len(read_decisions(path)) == 2


def test_a_malformed_line_raises_and_names_its_line_number(tmp_path: Path) -> None:
    # A skipped line would be a hole `diff` reports as a divergence between
    # backtest and live - a finding about the simulator that never happened.
    # The number matters because a log is thousands of lines long.
    path = tmp_path / "decisions.jsonl"
    write_decisions([_record(), _record(), _record()], path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with pytest.raises(ValueError, match="line 4: not valid JSON"):
        read_decisions(path)


def test_a_line_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    path.write_text("[1, 2, 3]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1: not a JSON object"):
        read_decisions(path)


def test_a_log_from_a_future_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    payload = json.loads(_line_of(_record()))
    payload["schema_version"] = SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1: schema_version"):
        read_decisions(path)


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_every_field_is_required_when_reading(tmp_path: Path, field: str) -> None:
    # Fifteen fields, each removed in turn. A reader that defaulted a missing
    # one would invent a decision nobody made - and `risk_verdict` defaulting
    # to "allow" would record a gate that never ran as having passed.
    path = tmp_path / "decisions.jsonl"
    payload = json.loads(_line_of(_record()))
    del payload[field]
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=f"missing field.*{field}"):
        read_decisions(path)


def test_an_unknown_action_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    payload = json.loads(_line_of(_record()))
    payload["action"] = "go_short"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not one of"):
        read_decisions(path)


def test_a_non_numeric_indicator_value_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    payload = json.loads(_line_of(_record()))
    payload["indicator_values"]["sma_fast"] = "412.3"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a number"):
        read_decisions(path)


def test_a_fractional_parameter_is_refused(tmp_path: Path) -> None:
    # A lookback of 10.5 bars is not a lookback, and a record carrying one
    # cannot describe a run anything performed.
    path = tmp_path / "decisions.jsonl"
    payload = json.loads(_line_of(_record()))
    payload["params"]["fast"] = 10.5
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a whole number of bars"):
        read_decisions(path)


def test_a_condition_list_holding_a_non_string_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    payload = json.loads(_line_of(_record()))
    payload["conditions_met"] = ["fine", 7]
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a string"):
        read_decisions(path)


def _line_of(record: DecisionRecord) -> str:
    """The exact line the writer would produce, for tests that then corrupt it.

    Goes through the writer rather than hand-building a payload, so a test
    corrupting one field is corrupting the real shape and not a guess at it.
    """
    from strategylab.logging.decisions import _line

    return _line(record)

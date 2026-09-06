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
from zoneinfo import ZoneInfo

import pandas as pd
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

# The two entry conditions of the committed config, at fast=10, slow=80,
# trend=150, rendered the way `core.strategy._render` renders them: name, the
# value at six significant figures, the operator, the same for the right side.
# 412.3456 at `:.6g` is 412.346, which is why the string and the mapping below
# differ in their last digit.
CROSSOVER = "sma(10)=412.346 crosses_above sma(80)=405.125"
TREND_FILTER = "close=414 above sma(150)=418.5"
EXIT_CROSSOVER = "sma(10)=401.5 crosses_below sma(80)=405.125"


def _record(**overrides: Any) -> DecisionRecord:
    """A coherent HOLD, with any field overridable one at a time.

    A HOLD BY DEFAULT, and it matters that the default is coherent: this is the
    repository's only worked example of a decision record, and a reader learns
    the schema from it. `core.strategy.evaluate` returns ENTER_LONG only when
    NO entry condition failed - the list is AND-combined - so an ENTER_LONG
    beside a failed condition, which is what this fixture used to be, is a
    record no run can produce. A HOLD is the one action that carries both
    condition lists non-empty, and it is also the commonest row in a real log.

    The `thresholds` keying here - the left indicator's name, holding the value
    of the right-hand side it was compared against - is ILLUSTRATIVE. The
    schema deliberately does not settle it; see the module docstring of
    `logging/decisions.py`.
    """
    fields: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "bar_timestamp": BAR,
        "recorded_at": RECORDED,
        "source": "backtest",
        "ticker": "SPY",
        "action": Action.HOLD,
        "indicator_values": {
            "sma_fast": 412.3456,
            "sma_slow": 405.125,
            "close": 414.0,
            "sma_trend": 418.5,
        },
        "thresholds": {"sma_fast": 405.125, "close": 418.5},
        "conditions_met": (CROSSOVER,),
        "conditions_failed": (TREND_FILTER,),
        # Zero on a HOLD, per `Intent.size_pct`: "Zero when action is HOLD or
        # EXIT_LONG".
        "requested_size_pct": 0.0,
        "approved_size_pct": 0.0,
        "risk_verdict": "allow",
        "risk_reason": "no limit bound",
        "params": {"fast": 10, "slow": 80, "trend": 150},
        "protocol_version": 1,
    }
    fields.update(overrides)
    return DecisionRecord(**fields)


def _for_action(action: Action) -> DecisionRecord:
    """A record for each action, shaped the way `evaluate` shapes that action.

    Mirrors `core.strategy.evaluate` exactly: EXIT_LONG and ENTER_LONG each
    return with `conditions_failed` empty, because both are reached only when
    nothing in their list failed; HOLD is the branch that carries failures.
    """
    if action is Action.ENTER_LONG:
        return _record(
            action=action,
            conditions_met=(CROSSOVER, TREND_FILTER),
            conditions_failed=(),
            requested_size_pct=95.0,
            approved_size_pct=95.0,
        )
    if action is Action.EXIT_LONG:
        return _record(
            action=action,
            conditions_met=(EXIT_CROSSOVER,),
            conditions_failed=(),
        )
    return _record()


def test_the_fixtures_are_records_evaluate_could_actually_return() -> None:
    # Guards the worked example against drifting back into an impossible shape.
    # An ENTER_LONG beside a failed AND-ed entry condition is the case this
    # caught, and it is the schema's only illustration in the repository.
    for action in Action:
        record = _for_action(action)
        assert record.action is action
        if action is Action.HOLD:
            assert record.conditions_failed, "a HOLD is explained by its failures"
            assert record.requested_size_pct == 0.0
        else:
            assert not record.conditions_failed, (
                "evaluate reaches this branch only when nothing in the list failed"
            )
        if action is Action.EXIT_LONG:
            assert record.requested_size_pct == 0.0


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


def test_the_line_on_disk_is_exactly_this(tmp_path: Path) -> None:
    # A GOLDEN LINE, because every other test here compares the writer against
    # the reader and would pass unchanged if BOTH sides adopted a different
    # format. This is the only test that pins what actually lands on disk, and
    # therefore the only one that fails if the on-wire shape changes - which is
    # what a future `diff`, and anything not written in Python, will read.
    #
    # Written out rather than generated: a expected value produced by calling
    # the code under test pins nothing.
    path = tmp_path / "decisions.jsonl"
    write_decision(_record(), path)

    assert path.read_text(encoding="utf-8") == (
        '{"action": "hold", '
        '"approved_size_pct": 0.0, '
        '"bar_timestamp": "2024-03-15T00:00:00", '
        '"conditions_failed": ["close=414 above sma(150)=418.5"], '
        '"conditions_met": ["sma(10)=412.346 crosses_above sma(80)=405.125"], '
        '"indicator_values": {"close": 414.0, "sma_fast": 412.3456, '
        '"sma_slow": 405.125, "sma_trend": 418.5}, '
        '"params": {"fast": 10, "slow": 80, "trend": 150}, '
        '"protocol_version": 1, '
        '"recorded_at": "2024-03-15T21:05:30.123456+00:00", '
        '"requested_size_pct": 0.0, '
        '"risk_reason": "no limit bound", '
        '"risk_verdict": "allow", '
        '"schema_version": 1, '
        '"source": "backtest", '
        '"thresholds": {"close": 418.5, "sma_fast": 405.125}, '
        '"ticker": "SPY"}\n'
    )


def test_a_bar_timestamp_keeps_its_time_of_day(tmp_path: Path) -> None:
    # Every other fixture here is at midnight, so a writer that dropped the
    # time component entirely would pass them all. Daily bars are at midnight;
    # intraday ones would not be, and the schema does not restrict them.
    path = tmp_path / "decisions.jsonl"
    intraday = datetime(2024, 3, 15, 14, 37, 42)

    write_decision(_record(bar_timestamp=intraday), path)

    assert read_decisions(path)[0].bar_timestamp == intraday


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
    #
    # The literal is not itself exactly representable - Python stores the
    # nearest double, whose repr is 412.3456789012346. That is the point: the
    # test compares the round trip against THAT double, so it pins JSON's float
    # handling rather than the decimal digits typed here.
    awkward = 412.34567890123456
    path = tmp_path / "decisions.jsonl"

    write_decision(_record(indicator_values={"sma_fast": awkward}), path)
    [restored] = read_decisions(path)

    assert restored.indicator_values["sma_fast"] == awkward


def test_a_named_utc_zone_survives_as_an_equal_instant(tmp_path: Path) -> None:
    # ZoneInfo("UTC") comes back as datetime.timezone.utc. The tzinfo OBJECT is
    # not preserved and the module docstring says so; what matters is that
    # records still compare equal, because datetimes compare by instant.
    path = tmp_path / "decisions.jsonl"
    zoned = _record(recorded_at=datetime(2024, 3, 15, 21, 5, 30, tzinfo=ZoneInfo("UTC")))

    write_decision(zoned, path)
    [restored] = read_decisions(path)

    assert restored == zoned
    assert restored.recorded_at.tzinfo is not ZoneInfo("UTC")


def test_the_keys_of_a_line_are_sorted(tmp_path: Path) -> None:
    # Sorted for the reason graveyard.py gives: it makes the bytes of a line a
    # function of the record alone, rather than of the order an emitter happened
    # to build its mappings in. Two runs recording the same decision then differ
    # only where they genuinely differ.
    path = tmp_path / "decisions.jsonl"
    write_decision(_record(), path)

    keys = list(json.loads(path.read_text(encoding="utf-8")).keys())

    assert keys == sorted(keys)


def test_two_records_built_in_different_orders_produce_identical_bytes(
    tmp_path: Path,
) -> None:
    # The property sorting buys, stated as a test rather than as a comment.
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"

    write_decision(_record(indicator_values={"a": 1.0, "b": 2.0}), first)
    write_decision(_record(indicator_values={"b": 2.0, "a": 1.0}), second)

    assert first.read_bytes() == second.read_bytes()


def test_every_action_in_the_vocabulary_round_trips(tmp_path: Path) -> None:
    # HOLD especially: it is a real answer rather than the absence of one, and
    # a log that could not represent it would drop most of its rows.
    path = tmp_path / "decisions.jsonl"
    records = [_for_action(action) for action in Action]

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


def test_a_pandas_timestamp_with_nanoseconds_is_refused(tmp_path: Path) -> None:
    # THE CASE THE GUARD EXISTS FOR. A bar index hands the emitter a
    # pandas.Timestamp, which subclasses datetime and carries nanoseconds.
    # `isoformat` renders them; `fromisoformat` returns a plain datetime and
    # stops at microseconds. Measured: the value comes back truncated and
    # compares unequal, so the record would not round-trip. Refused loudly
    # instead of truncated silently.
    path = tmp_path / "decisions.jsonl"
    nanos = pd.Timestamp("2024-03-15 00:00:00.000000001")
    assert nanos.nanosecond == 1, "the fixture must carry sub-microsecond precision"

    with pytest.raises(ValueError, match="does not survive ISO 8601"):
        write_decision(_record(bar_timestamp=nanos), path)

    assert not path.exists()


def test_a_whole_pandas_timestamp_is_accepted(tmp_path: Path) -> None:
    # The guard must not refuse what a daily bar index actually produces, which
    # is a Timestamp at midnight with no sub-microsecond part. A check that
    # rejected every pandas Timestamp would block the emitter entirely.
    path = tmp_path / "decisions.jsonl"
    whole = pd.Timestamp("2024-03-15")

    write_decision(_record(bar_timestamp=whole), path)

    assert read_decisions(path)[0].bar_timestamp == whole


@pytest.mark.parametrize("offset", [5, -5, 1])
def test_a_recorded_at_away_from_utc_is_refused(tmp_path: Path, offset: int) -> None:
    # The field is documented as UTC in two places. Checking only "not naive"
    # would accept +05:30 and leave two logs comparing by a rendering rather
    # than by an instant.
    path = tmp_path / "decisions.jsonl"
    elsewhere = datetime(2024, 3, 15, 21, 5, tzinfo=timezone(timedelta(hours=offset)))

    with pytest.raises(ValueError, match="documented as UTC"):
        write_decision(_record(recorded_at=elsewhere), path)


@pytest.mark.parametrize("field", ["indicator_values", "thresholds", "params"])
def test_a_non_string_mapping_key_is_refused(tmp_path: Path, field: str) -> None:
    # JSON object keys are strings and json coerces an integer one silently, so
    # {5: 10} would be written as {"5": 10} and come back unequal. The frozen
    # dataclass's dict[str, ...] annotation stops nothing at runtime.
    # graveyard.py refuses exactly this, for exactly this reason.
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError, match="non-string key"):
        write_decision(_record(**{field: {5: 10}}), path)

    assert not path.exists()


def test_an_action_that_is_not_an_action_is_refused(tmp_path: Path) -> None:
    # The dataclass is not runtime-typed, so action="hold" is constructible and
    # would reach `.value` as an AttributeError - a type the contract does not
    # admit and a message naming neither the field nor the log.
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError, match="not an Action"):
        write_decision(_record(action="hold"), path)


def test_a_value_json_cannot_represent_is_refused_as_a_value_error(
    tmp_path: Path,
) -> None:
    # json.dumps raises TypeError for a set, a Decimal, an enum. Translated
    # rather than propagated, as graveyard.py does, so it does not escape a
    # Raises clause that admits only ValueError and OSError.
    path = tmp_path / "decisions.jsonl"

    with pytest.raises(ValueError, match="not serialisable as JSON"):
        write_decision(_record(risk_reason={"unserialisable"}), path)


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


# --------------------------------------------------------------------------
# The read path is as strict as the write path. It was not, and a reader looser
# than its writer accepts logs this project cannot have written - which `diff`
# then compares against a real one.
# --------------------------------------------------------------------------


def _corrupt(tmp_path: Path, **changes: Any) -> Path:
    """A log of one record with fields replaced, for the reader's refusals."""
    payload = json.loads(_line_of(_record()))
    payload.update(changes)
    path = tmp_path / "decisions.jsonl"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("field", ["source", "ticker", "risk_verdict", "risk_reason"])
@pytest.mark.parametrize("value", [None, 5, ["a"], {"a": 1}])
def test_a_text_field_that_is_not_a_string_is_refused(
    tmp_path: Path, field: str, value: Any
) -> None:
    # `str(None)` is "None" and `str(5)` is "5". An earlier reader coerced all
    # four of these silently, so a ticker of null became the string "None" and
    # was then diffed against a real log.
    with pytest.raises(ValueError, match="is not a string"):
        read_decisions(_corrupt(tmp_path, **{field: value}))


@pytest.mark.parametrize("field", ["requested_size_pct", "approved_size_pct"])
@pytest.mark.parametrize("value", [True, "95.0", None, []])
def test_a_size_that_is_not_a_number_is_refused(tmp_path: Path, field: str, value: Any) -> None:
    # `float(True)` is 1.0 and `float("95.0")` is 95.0. A list reached `float`
    # as a TypeError, which the Raises clause did not admit and which carried
    # no line number.
    with pytest.raises(ValueError, match="is not a number"):
        read_decisions(_corrupt(tmp_path, **{field: value}))


@pytest.mark.parametrize("value", [1.9, True, "1", None])
def test_a_protocol_version_that_is_not_whole_is_refused(tmp_path: Path, value: Any) -> None:
    # `int(1.9)` is 1. A record claiming traceability to protocol version 1
    # when the log said 1.9 is a record traceable to rules it was not produced
    # under.
    with pytest.raises(ValueError, match="not a whole number"):
        read_decisions(_corrupt(tmp_path, protocol_version=value))


def test_a_bare_nan_token_in_a_log_is_refused(tmp_path: Path) -> None:
    # MEASURED: `json.loads('{"x": NaN}')` succeeds - the bare token is
    # accepted by default. The writer refuses to produce one, so a reader that
    # accepted it would hand back records this project cannot have written.
    sentinel = "-987.654"
    payload = json.loads(_line_of(_record(indicator_values={"sma_fast": float(sentinel)})))
    text = json.dumps(payload)
    assert sentinel in text, "the sentinel must reach the line for this to test anything"

    path = tmp_path / "decisions.jsonl"
    path.write_text(text.replace(sentinel, "NaN") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not finite"):
        read_decisions(path)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_size_in_a_log_is_refused(tmp_path: Path, token: str) -> None:
    # A SENTINEL rather than the field's real value, so the substitution cannot
    # silently miss. An earlier version of this test replaced the literal 95.0
    # and stopped matching the moment the fixture's size changed - it passed
    # while corrupting nothing, which is a test asserting that valid input is
    # accepted under a name promising the opposite.
    sentinel = "-987.654"
    payload = json.loads(_line_of(_record(requested_size_pct=float(sentinel))))
    text = json.dumps(payload)
    assert sentinel in text, "the sentinel must reach the line for this to test anything"

    path = tmp_path / "decisions.jsonl"
    path.write_text(text.replace(sentinel, token) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not finite"):
        read_decisions(path)


def test_an_aware_bar_timestamp_in_a_log_is_refused(tmp_path: Path) -> None:
    # The frames are checked on the way in as well as on the way out. This is
    # the exact state the writer calls out as "how a diff would come to report
    # every bar as differing", and an earlier reader accepted it.
    with pytest.raises(ValueError, match="carries a timezone"):
        read_decisions(_corrupt(tmp_path, bar_timestamp="2024-03-15T00:00:00+02:00"))


def test_a_naive_recorded_at_in_a_log_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="carries no timezone"):
        read_decisions(_corrupt(tmp_path, recorded_at="2024-03-15T21:05:30"))


def test_a_recorded_at_away_from_utc_in_a_log_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="documented as UTC"):
        read_decisions(_corrupt(tmp_path, recorded_at="2024-03-15T21:05:30+05:00"))


def test_an_integer_timestamp_is_not_read_as_a_date(tmp_path: Path) -> None:
    # `str(20240315)` is "20240315", which fromisoformat accepts as a
    # basic-format date - so an integer would have been silently read as
    # midnight on that day.
    with pytest.raises(ValueError, match="is not a string"):
        read_decisions(_corrupt(tmp_path, bar_timestamp=20240315))


def test_a_source_outside_the_pair_in_a_log_is_refused(tmp_path: Path) -> None:
    # Checked on write from the start and not on read, so a hand-edited log
    # could carry a third value into a diff.
    with pytest.raises(ValueError, match="not one of"):
        read_decisions(_corrupt(tmp_path, source="paper"))


@pytest.mark.parametrize("version", [True, 1.0])
def test_a_schema_version_equal_to_one_by_coercion_is_refused(tmp_path: Path, version: Any) -> None:
    # `True == 1` and `1.0 == 1` in Python, so a bare equality gate passes both
    # and the reader would launder them into the int 1.
    with pytest.raises(ValueError, match="schema_version"):
        read_decisions(_corrupt(tmp_path, schema_version=version))


def _line_of(record: DecisionRecord) -> str:
    """The exact line the writer would produce, for tests that then corrupt it.

    Goes through the writer rather than hand-building a payload, so a test
    corrupting one field is corrupting the real shape and not a guess at it.
    """
    from strategylab.logging.decisions import _line

    return _line(record)

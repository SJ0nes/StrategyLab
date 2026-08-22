"""Tests for the graveyard: the append-only record of rejected candidates.

There is almost no arithmetic here. What this file asserts is FILE BEHAVIOUR,
and the two claims worth stating up front are the ones a careless implementation
gets wrong:

**Append-only.** Writing a second record must leave the first byte-identical. A
test that only checked the second record was present would pass against an
implementation that truncated the file every time, so the assertions here compare
the first line before and after the second write.

**The file must be readable by something other than Python.** JSON has no `NaN`
and no `Infinity`. Python's `json` module writes those as bare tokens by default,
producing a file that `json.loads` accepts and a strict parser rejects — so a
graveyard holding one failed cell would silently stop being JSON. Since
`search/plateau.py` requires a failed cell to carry a `NaN` metric, that case is
not hypothetical, and it is asserted directly by parsing with `parse_constant`
set to refuse.

No test writes to `state/`. Every one uses `tmp_path`, because the real graveyard
is a record of real research and a test that appended to it would corrupt the
denominator the file exists to provide.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from strategylab.search.graveyard import (
    GRAVEYARD_SCHEMA_VERSION,
    REJECTION_REASONS,
    record_rejection,
)

CANDIDATE = {"fast": 10, "slow": 60, "trend": 150}
METRICS = {"sharpe": 1.9, "max_drawdown_pct": 12.5}


def refuse_nan(token: str) -> Any:
    """Raise on the non-JSON constants Python's json module will emit."""
    raise AssertionError(f"file contains the non-JSON token {token!r}")


def lines_of(path: Path) -> list[str]:
    """Return the file's lines, with terminators stripped."""
    return path.read_text(encoding="utf-8").splitlines()


def records_in(path: Path) -> list[dict[str, Any]]:
    """Parse every line, refusing NaN and Infinity as a strict reader would."""
    return [json.loads(line, parse_constant=refuse_nan) for line in lines_of(path)]


# --------------------------------------------------------------------------
# What one record contains
# --------------------------------------------------------------------------


def test_a_rejection_is_written_as_one_json_object_on_one_line(tmp_path: Path) -> None:
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, "edge_winner", path)
    assert len(lines_of(path)) == 1
    assert records_in(path)[0]["reason"] == "edge_winner"


def test_the_record_carries_the_candidate_and_metrics_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, "degenerate_plateau", path)
    record = records_in(path)[0]
    assert record["candidate"] == CANDIDATE
    assert record["metrics"] == METRICS


def test_the_record_carries_a_schema_version(tmp_path: Path) -> None:
    # So a later reader can say what it is looking at rather than guess.
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, "edge_winner", path)
    assert records_in(path)[0]["schema_version"] == GRAVEYARD_SCHEMA_VERSION


def test_the_record_carries_a_timezone_aware_utc_instant(tmp_path: Path) -> None:
    # The instant itself is not asserted - it comes from a clock. What is
    # asserted is that it parses and carries an offset, because a naive
    # timestamp in a file that may be read on another machine is ambiguous.
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, "edge_winner", path)
    recorded = datetime.fromisoformat(records_in(path)[0]["recorded_at"])
    assert recorded.tzinfo is not None
    assert recorded.utcoffset() is not None
    assert recorded.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# Append-only
# --------------------------------------------------------------------------


def test_a_second_rejection_appends_and_leaves_the_first_untouched(tmp_path: Path) -> None:
    # The claim the module rests on. Asserting only that the second record is
    # present would pass against an implementation that truncates on every open.
    path = tmp_path / "graveyard.jsonl"
    record_rejection({"fast": 5}, {"sharpe": 0.1}, "edge_winner", path)
    first_line = lines_of(path)[0]

    record_rejection({"fast": 10}, {"sharpe": 0.2}, "degenerate_plateau", path)
    after = lines_of(path)

    assert len(after) == 2
    assert after[0] == first_line
    assert records_in(path)[1]["candidate"] == {"fast": 10}


def test_many_rejections_accumulate_in_the_order_they_were_recorded(tmp_path: Path) -> None:
    # The denominator this file exists to provide: five rejections, five lines.
    path = tmp_path / "graveyard.jsonl"
    for index in range(5):
        record_rejection({"fast": index}, {"sharpe": float(index)}, "edge_winner", path)
    assert [record["candidate"]["fast"] for record in records_in(path)] == [0, 1, 2, 3, 4]


def test_the_parent_directory_is_created_if_it_is_absent(tmp_path: Path) -> None:
    path = tmp_path / "deeper" / "still" / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, "edge_winner", path)
    assert path.exists()


def test_every_line_ends_with_a_bare_line_feed(tmp_path: Path) -> None:
    # A JSON Lines file is data. A reader splitting on "\n" should not have to
    # strip a carriage return that the writing platform added — which is what
    # would happen here without an explicit newline, since these tests run on
    # Windows.
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, "edge_winner", path)
    record_rejection(CANDIDATE, METRICS, "edge_winner", path)
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 2


# --------------------------------------------------------------------------
# The file stays JSON
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_metric_is_recorded_as_null_rather_than_a_non_json_token(
    tmp_path: Path, value: float
) -> None:
    # `json.dumps` writes NaN and Infinity as bare tokens by default. Those are
    # not JSON, and `records_in` refuses them, so this test fails outright
    # against an implementation that passes them through.
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, {"sharpe": value}, "backtest_failed", path)
    assert records_in(path)[0]["metrics"]["sharpe"] is None


def test_a_failed_cell_records_its_reason_even_though_its_metric_is_gone(
    tmp_path: Path,
) -> None:
    # The case `search/plateau.py` creates: a cell whose backtest raised keeps a
    # NaN metric. The metric becomes null, and `reason` is what survives to say
    # why — which is the fact the record exists to preserve.
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, {"sharpe": math.nan}, "backtest_failed", path)
    record = records_in(path)[0]
    assert record["metrics"]["sharpe"] is None
    assert record["reason"] == "backtest_failed"


def test_a_finite_metric_is_not_disturbed(tmp_path: Path) -> None:
    # The null conversion must apply only to the non-finite case.
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, {"sharpe": 0.0, "max_drawdown_pct": -1.5}, "edge_winner", path)
    assert records_in(path)[0]["metrics"] == {"sharpe": 0.0, "max_drawdown_pct": -1.5}


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_an_empty_reason_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reason is empty"):
        record_rejection(CANDIDATE, METRICS, "", tmp_path / "graveyard.jsonl")


@pytest.mark.parametrize("reason", ["edge_winnner", "EDGE_WINNER", "rejected", "edge winner"])
def test_a_reason_outside_the_closed_vocabulary_is_refused(tmp_path: Path, reason: str) -> None:
    # A misspelling would otherwise become a fifth category that no report
    # counts, which breaks the one thing this file supports: counting how many
    # candidates died and of what.
    with pytest.raises(ValueError, match="vocabulary is closed"):
        record_rejection(CANDIDATE, METRICS, reason, tmp_path / "graveyard.jsonl")


@pytest.mark.parametrize("reason", sorted(REJECTION_REASONS))
def test_every_reason_in_the_vocabulary_is_accepted(tmp_path: Path, reason: str) -> None:
    path = tmp_path / "graveyard.jsonl"
    record_rejection(CANDIDATE, METRICS, reason, path)
    assert records_in(path)[0]["reason"] == reason


def test_the_vocabulary_holds_the_five_reasons_the_search_can_produce() -> None:
    # Four from the stub's contract, plus `backtest_failed` for the cell whose
    # backtest raised — a case none of the other four describes and which
    # `search/plateau.py` requires the search to handle.
    assert sorted(REJECTION_REASONS) == [
        "backtest_failed",
        "below_noise_ceiling",
        "degenerate_plateau",
        "drawdown_ceiling",
        "edge_winner",
    ]


@pytest.mark.parametrize("value", ["1.9", None, [1.9], True])
def test_a_metric_that_is_not_a_real_number_is_refused(tmp_path: Path, value: object) -> None:
    # `True` is an `int` in Python and would otherwise be recorded as a Sharpe
    # of 1.0.
    metrics: dict[str, Any] = {"sharpe": value}
    with pytest.raises(ValueError, match="is not a number"):
        record_rejection(CANDIDATE, metrics, "edge_winner", tmp_path / "graveyard.jsonl")


@pytest.mark.parametrize("key", [5, (1, 2), None])
def test_a_candidate_with_a_non_string_parameter_name_is_refused(
    tmp_path: Path, key: object
) -> None:
    # `json` coerces an int key to a string without complaint, so `{5: 10}`
    # would be recorded as `{"5": 10}` and a reader could not tell which it had
    # been. A tuple key is worse: `json.dumps` raises TypeError, which this
    # contract does not admit, and it used to do so with the file already open.
    candidate: dict[Any, Any] = {key: 10}
    with pytest.raises(ValueError, match="non-string parameter names"):
        record_rejection(candidate, METRICS, "edge_winner", tmp_path / "graveyard.jsonl")


def test_nothing_is_written_when_the_record_cannot_be_serialised(tmp_path: Path) -> None:
    # The record is serialised BEFORE the path is touched. Serialising inside
    # the open block created the file, then raised, leaving an empty file that
    # a reader would count as a graveyard with no rejections in it.
    #
    # An unserialisable VALUE, not key: a bad key is refused by the check above
    # before serialisation is ever reached, so a key-based case cannot exercise
    # this path at all. This candidate's parameter name is a string and passes
    # every earlier check, so `json.dumps` is the first thing to object.
    path = tmp_path / "graveyard.jsonl"
    candidate: dict[str, Any] = {"fast": object()}
    with pytest.raises(ValueError, match="not serialisable as JSON"):
        record_rejection(candidate, METRICS, "edge_winner", path)
    assert not path.exists()


def test_a_refused_rejection_writes_nothing(tmp_path: Path) -> None:
    # The reason check runs before the file is touched, so a bad call does not
    # leave a partial file or an empty one behind.
    path = tmp_path / "graveyard.jsonl"
    with pytest.raises(ValueError):
        record_rejection(CANDIDATE, METRICS, "not_a_reason", path)
    assert not path.exists()

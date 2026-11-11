"""Tests for what the app is told has been run: searches, rejections, held-back reads.

Every test builds a small project in a temporary directory and writes the
result files by hand, so each value asserted is one the test chose. Nothing
here runs a search or a validation, and nothing reads the real ``state/``.

The search artifact written here is a two-by-two grid. Its curves, closes and
figures are round numbers picked so that the one derived figure - a cell's
total return - can be checked by eye: 12,500 on 10,000 is +25%.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.history import (
    CANDIDATE,
    list_exams,
    list_searches,
    read_graveyard,
    search_detail,
)
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.core.config import config_content_hash
from strategylab.search.graveyard import GRAVEYARD_SCHEMA_VERSION, record_rejection
from strategylab.search.loop import SEARCH_SCHEMA_VERSION
from strategylab.validate.walkforward import VALIDATION_SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]

TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: The instrument's closes as the artifact records them. No test wants these
#: back, so finding one in a result is unambiguous.
CLOSES = [4321.5, 4322.5, 4323.5]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding the committed protocol, strategy config and risk limits."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    return tmp_path


@pytest.fixture
def strategy(project: Path) -> StrategyFile:
    """The committed strategy, as the registry finds it in `project`."""
    found = find_strategy(project, "ma_crossover")
    assert found is not None
    return found


def _cell(fast: int, slow: int, sharpe: float | None, drawdown: float | None) -> dict[str, Any]:
    return {
        "params": {"fast": fast, "slow": slow},
        "sharpe": sharpe,
        "max_drawdown_pct": drawdown,
        "trade_count": fast + slow,
        "trade_fingerprint": "trades:abc",
        "plateau_score": 0.25,
        "neighbour_count": 3,
    }


def _search(root: Path, stamp: str, **changes: Any) -> Path:
    """Write a complete two-by-two search artifact, then apply `changes` to it."""
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": f"2026-01-{stamp}T00:00:00+00:00",
        "strategy": "ma_crossover",
        "config_content_hash": config_content_hash(root / "configs" / "ma_crossover.yaml"),
        "instrument": "SPY",
        "window": {"start": "2005-01-01", "end": "2018-12-31", "bars": 3523},
        "max_drawdown_ceiling_pct": 25.0,
        "starting_equity": 10000.0,
        "grid_size": 4,
        "noise_ceiling": {
            "trials": 100,
            "scoreable_trials": 100,
            "block_bars": 200,
            "seed": 0,
            "percentiles": {"p50": 0.7, "p90": 1.0, "p95": 1.1},
            "max": 1.2,
            "trial_best_sharpe": [0.5] * 100,
        },
        "noise_gate": {
            "status": "failed",
            "beaten": 25,
            "required": 95,
            "scoreable_trials": 100,
            "summary": "written by a test",
        },
        "benchmark": {"kind": "buy_and_hold", "sharpe": 0.4832, "max_drawdown_pct": 55.19},
        "winner": {"params": {"fast": 10, "slow": 40}, "sharpe": 0.5, "max_drawdown_pct": 12.5},
        "rejections": [
            {"params": {"fast": 5, "slow": 20}, "reason": "edge_winner", "detail": "on the wall"}
        ],
        "equity_curves": {
            "max_points": 400,
            "dates": ["2005-01-03", "2010-01-04", "2018-12-31"],
            "close": CLOSES,
            "cells": [
                [None, 10000.0, 12500.0],
                [None, 10000.0, 9000.0],
                None,
                [None, 10000.0, 10000.0],
            ],
        },
        "cells": [
            _cell(5, 20, 0.9, 30.0),
            _cell(10, 20, -0.2, 14.0),
            _cell(5, 40, None, None),
            _cell(10, 40, 0.5, 12.5),
        ],
    }
    payload.update(changes)
    (root / "state").mkdir(exist_ok=True)
    path = root / "state" / f"search-202601{stamp}T000000Z.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _exam(root: Path, stamp: str, **changes: Any) -> Path:
    """Write a validation artifact holding the fields the app reads."""
    payload: dict[str, Any] = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "generated_at": f"2026-02-{stamp}T00:00:00+00:00",
        "strategy": "ma_crossover",
        "params": {"fast": 10, "slow": 40},
        "outcome": "fail",
        "reason": "written by a test",
        "pooled": {"sharpe": 0.75, "max_drawdown_pct": 8.5, "trades": 31},
        "walk_forward_touches": 2,
        "folds": [{"index": 0}, {"index": 1}, {"index": 2}],
    }
    payload.update(changes)
    (root / "state").mkdir(exist_ok=True)
    path = root / "state" / f"validation-202602{stamp}T000000Z.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The list of searches.
# --------------------------------------------------------------------------


def test_searches_are_listed_newest_first_as_recorded(
    project: Path, strategy: StrategyFile
) -> None:
    older = _search(project, "01", winner=None)
    newer = _search(project, "02")

    listed = list_searches(project, strategy)

    assert [entry["artifact"] for entry in listed] == [newer.name, older.name]
    assert listed[0] == {
        "artifact": newer.name,
        "readable": True,
        "generated_at": "2026-01-02T00:00:00+00:00",
        "cells": 4,
        "candidate": {"fast": 10, "slow": 40},
        "sharpe": 0.5,
        "max_drawdown_pct": 12.5,
        "gate": {
            "status": "failed",
            "beaten": 25,
            "required": 95,
            "scoreable_trials": 100,
            "summary": "written by a test",
        },
        "config_changed_since": False,
    }
    assert listed[1]["candidate"] is None
    assert listed[1]["sharpe"] is None


def test_a_search_in_an_older_format_is_listed_as_not_read(
    project: Path, strategy: StrategyFile
) -> None:
    older = _search(project, "01", schema_version=SEARCH_SCHEMA_VERSION - 1)

    (entry,) = list_searches(project, strategy)

    assert entry == {
        "artifact": older.name,
        "readable": False,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "reason": (
            f"written in another format (version {SEARCH_SCHEMA_VERSION - 1}); "
            f"this app reads version {SEARCH_SCHEMA_VERSION}"
        ),
    }


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "the file is not valid JSON"),
        ('{"sharpe": NaN}', "the file is not valid JSON"),
        ("[1, 2]", "the file does not hold a JSON object"),
    ],
)
def test_a_file_that_is_not_an_artifact_is_listed_with_why(
    project: Path, strategy: StrategyFile, text: str, reason: str
) -> None:
    (project / "state").mkdir()
    (project / "state" / "search-20260101T000000Z.json").write_text(text, encoding="utf-8")

    (entry,) = list_searches(project, strategy)

    assert entry["readable"] is False
    assert entry["reason"] == reason
    assert entry["generated_at"] is None


def test_a_search_without_a_gate_is_listed_as_not_read(
    project: Path, strategy: StrategyFile
) -> None:
    _search(project, "01", noise_gate=None)

    (entry,) = list_searches(project, strategy)

    assert entry["readable"] is False
    assert entry["reason"] == "the file records no noise gate this app knows"


def test_another_strategys_search_is_not_listed(project: Path, strategy: StrategyFile) -> None:
    _search(project, "01", strategy="something_else")
    mine = _search(project, "02")

    assert [entry["artifact"] for entry in list_searches(project, strategy)] == [mine.name]


def test_an_unreadable_search_naming_another_strategy_is_not_listed(
    project: Path, strategy: StrategyFile
) -> None:
    _search(project, "01", strategy="something_else", schema_version=1)

    assert list_searches(project, strategy) == []


def test_a_search_made_before_the_config_changed_says_so(
    project: Path, strategy: StrategyFile
) -> None:
    _search(project, "01", config_content_hash="0" * 64)

    (entry,) = list_searches(project, strategy)

    assert entry["config_changed_since"] is True


def test_a_project_with_no_state_directory_has_no_searches(
    project: Path, strategy: StrategyFile
) -> None:
    assert list_searches(project, strategy) == []


# --------------------------------------------------------------------------
# One search in full.
# --------------------------------------------------------------------------


def test_a_search_is_laid_out_with_its_axes_and_window(
    project: Path, strategy: StrategyFile
) -> None:
    path = _search(project, "01")

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    assert detail["available"] is True
    assert detail["artifact"] == path.name
    assert detail["instrument"] == "SPY"
    assert detail["window"] == {"start": "2005-01-01", "end": "2018-12-31", "bars": 3523}
    assert detail["starting_equity"] == 10000.0
    assert detail["max_drawdown_ceiling_pct"] == 25.0
    assert detail["parameters"] == ["fast", "slow"]
    assert detail["axes"] == {"fast": [5, 10], "slow": [20, 40]}
    assert detail["dates"] == ["2005-01-03", "2010-01-04", "2018-12-31"]
    assert detail["gate"]["beaten"] == 25
    assert detail["rejections"] == [
        {"params": {"fast": 5, "slow": 20}, "reason": "edge_winner", "detail": "on the wall"}
    ]


def test_each_cell_carries_its_own_curve_and_figures(project: Path, strategy: StrategyFile) -> None:
    path = _search(project, "01")

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    by_params = {(cell["params"]["fast"], cell["params"]["slow"]): cell for cell in detail["cells"]}
    assert by_params[(5, 20)] == {
        "params": {"fast": 5, "slow": 20},
        "sharpe": 0.9,
        "max_drawdown_pct": 30.0,
        "trade_count": 25,
        "plateau_score": 0.25,
        "neighbour_count": 3,
        "total_return_pct": 25.0,
        "mark": "edge_winner",
        "curve": [None, 10000.0, 12500.0],
    }
    assert by_params[(10, 20)]["curve"] == [None, 10000.0, 9000.0]
    assert by_params[(10, 20)]["trade_count"] == 30
    assert by_params[(10, 40)]["curve"] == [None, 10000.0, 10000.0]


def test_total_return_is_the_last_point_against_the_starting_equity(
    project: Path, strategy: StrategyFile
) -> None:
    # 12,500 on 10,000 is +25%; 9,000 is -10%; 10,000 is nothing. Starting from
    # 8,000 instead, 12,500 is +56.25%, which a formula that ignored the
    # starting equity would not produce.
    first = _search(project, "01")
    second = _search(project, "02", starting_equity=8000.0)

    one = search_detail(project, strategy, first.name)
    two = search_detail(project, strategy, second.name)

    assert one is not None and two is not None
    assert [cell["total_return_pct"] for cell in one["cells"]] == pytest.approx(
        [25.0, -10.0, None, 0.0]
    )
    assert two["cells"][0]["total_return_pct"] == pytest.approx(56.25)


def test_a_cell_with_no_curve_has_no_return_and_not_a_zero(
    project: Path, strategy: StrategyFile
) -> None:
    path = _search(project, "01")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["equity_curves"]["cells"][0] = [None, 10000.0, None]
    path.write_text(json.dumps(payload), encoding="utf-8")

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    assert detail["cells"][0]["total_return_pct"] is None
    assert detail["cells"][2]["total_return_pct"] is None
    assert detail["cells"][2]["curve"] is None
    assert detail["cells"][2]["sharpe"] is None


def test_cells_are_marked_candidate_rejected_or_neither(
    project: Path, strategy: StrategyFile
) -> None:
    path = _search(project, "01")

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    assert [cell["mark"] for cell in detail["cells"]] == ["edge_winner", None, None, CANDIDATE]


def test_a_search_with_no_candidate_marks_no_cell_as_one(
    project: Path, strategy: StrategyFile
) -> None:
    path = _search(project, "01", winner=None)

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    assert CANDIDATE not in [cell["mark"] for cell in detail["cells"]]


def test_the_buy_and_hold_line_is_not_passed_on(project: Path, strategy: StrategyFile) -> None:
    path = _search(project, "01")

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    text = json.dumps(detail)
    for close in CLOSES:
        assert str(close) not in text
    assert "buy_and_hold" not in text
    assert "benchmark" not in detail
    assert "0.4832" not in text


def test_the_ceiling_is_passed_on_without_its_hundred_trial_values(
    project: Path, strategy: StrategyFile
) -> None:
    with_one = _search(project, "01")
    without = _search(project, "02", noise_ceiling=None)

    one = search_detail(project, strategy, with_one.name)
    two = search_detail(project, strategy, without.name)

    assert one is not None and two is not None
    assert one["ceiling"] == {
        "trials": 100,
        "scoreable_trials": 100,
        "percentiles": {"p50": 0.7, "p90": 1.0, "p95": 1.1},
        "max": 1.2,
    }
    assert two["ceiling"] is None


def test_a_name_that_is_not_a_listed_search_finds_nothing(
    project: Path, strategy: StrategyFile
) -> None:
    listed = _search(project, "01")
    # A real artifact one directory up, and names written to reach it.
    shutil.copyfile(listed, project / "search-20260109T000000Z.json")

    assert search_detail(project, strategy, listed.name) is not None
    for name in (
        "../search-20260109T000000Z.json",
        "..\\search-20260109T000000Z.json",
        "search-20260109T000000Z.json",
        "state/" + listed.name,
        str(listed),
        "",
    ):
        assert search_detail(project, strategy, name) is None


def test_another_strategys_search_cannot_be_opened_under_this_one(
    project: Path, strategy: StrategyFile
) -> None:
    other = _search(project, "01", strategy="something_else")

    assert search_detail(project, strategy, other.name) is None


def test_a_search_in_an_older_format_is_missing_with_the_reason(
    project: Path, strategy: StrategyFile
) -> None:
    older = _search(project, "01", schema_version=4)

    detail = search_detail(project, strategy, older.name)

    assert detail == {
        "available": False,
        "reason": (
            "This search could not be read: written in another format (version 4); "
            f"this app reads version {SEARCH_SCHEMA_VERSION}."
        ),
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"cells": None},
        {"equity_curves": None},
        {"equity_curves": {"dates": [], "close": [], "cells": []}},
        {"starting_equity": "ten thousand"},
        {"window": {"start": "2005-01-01"}},
        {"rejections": [{"params": {"fast": 5}}]},
        {"noise_gate": None},
    ],
)
def test_a_search_lacking_a_field_is_missing_not_an_error(
    project: Path, strategy: StrategyFile, changes: dict[str, Any]
) -> None:
    path = _search(project, "01", **changes)

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    assert detail["available"] is False
    assert detail["reason"].startswith("This search could not be read:")
    assert str(project) not in detail["reason"]


# --------------------------------------------------------------------------
# Reads of the held-back window.
# --------------------------------------------------------------------------


def test_a_project_that_has_never_validated_lists_no_exam(
    project: Path, strategy: StrategyFile
) -> None:
    _search(project, "01")

    assert list_exams(project, strategy) == []


def test_exams_are_listed_newest_first_as_recorded(project: Path, strategy: StrategyFile) -> None:
    _exam(project, "01", outcome="insufficient_data", walk_forward_touches=1)
    newer = _exam(project, "02")

    listed = list_exams(project, strategy)

    assert listed[0] == {
        "artifact": newer.name,
        "readable": True,
        "generated_at": "2026-02-02T00:00:00+00:00",
        "params": {"fast": 10, "slow": 40},
        "outcome": "fail",
        "reason": "written by a test",
        "sharpe": 0.75,
        "max_drawdown_pct": 8.5,
        "trades": 31,
        "folds": 3,
        "read_number": 2,
    }
    assert [entry["outcome"] for entry in listed] == ["fail", "insufficient_data"]
    assert listed[1]["read_number"] == 1


def test_an_exam_in_another_format_is_listed_as_not_read(
    project: Path, strategy: StrategyFile
) -> None:
    _exam(project, "01", schema_version=VALIDATION_SCHEMA_VERSION + 1)

    (entry,) = list_exams(project, strategy)

    assert entry["readable"] is False
    assert f"version {VALIDATION_SCHEMA_VERSION + 1}" in entry["reason"]
    assert "outcome" not in entry


def test_another_strategys_exam_is_not_listed(project: Path, strategy: StrategyFile) -> None:
    _exam(project, "01", strategy="something_else")

    assert list_exams(project, strategy) == []


def test_a_search_is_not_listed_as_an_exam(project: Path, strategy: StrategyFile) -> None:
    _search(project, "01", schema_version=VALIDATION_SCHEMA_VERSION)

    assert list_exams(project, strategy) == []


# --------------------------------------------------------------------------
# The graveyard.
# --------------------------------------------------------------------------


def _bury(root: Path, line: dict[str, Any] | str) -> None:
    (root / "state").mkdir(exist_ok=True)
    text = line if isinstance(line, str) else json.dumps(line)
    with (root / "state" / "graveyard.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def _record(fast: int, reason: str, day: int, sharpe: float | None = 0.5) -> dict[str, Any]:
    return {
        "candidate": {"fast": fast, "slow": 80},
        "metrics": {"max_drawdown_pct": 20.0 + day, "sharpe": sharpe},
        "reason": reason,
        "recorded_at": f"2026-03-{day:02d}T00:00:00+00:00",
        "schema_version": GRAVEYARD_SCHEMA_VERSION,
    }


def test_a_project_with_no_graveyard_has_buried_nothing(project: Path) -> None:
    assert read_graveyard(project) == {
        "available": True,
        "records": 0,
        "unread_lines": 0,
        "entries": [],
    }


def test_the_same_rejection_recorded_three_times_is_one_entry(project: Path) -> None:
    _bury(project, _record(5, "edge_winner", 1, sharpe=0.1))
    _bury(project, _record(5, "edge_winner", 3, sharpe=0.3))
    _bury(project, _record(5, "edge_winner", 2, sharpe=0.2))

    graveyard = read_graveyard(project)

    assert graveyard["records"] == 3
    assert graveyard["entries"] == [
        {
            "candidate": {"fast": 5, "slow": 80},
            "reason": "edge_winner",
            "times": 3,
            "last_recorded_at": "2026-03-03T00:00:00+00:00",
            "sharpe": 0.3,
            "max_drawdown_pct": 23.0,
        }
    ]


def test_entries_are_distinct_by_candidate_and_by_reason_newest_first(project: Path) -> None:
    _bury(project, _record(5, "edge_winner", 1))
    _bury(project, _record(10, "edge_winner", 3))
    _bury(project, _record(5, "below_noise_ceiling", 2))

    entries = read_graveyard(project)["entries"]

    assert [(entry["candidate"]["fast"], entry["reason"]) for entry in entries] == [
        (10, "edge_winner"),
        (5, "below_noise_ceiling"),
        (5, "edge_winner"),
    ]
    assert [entry["times"] for entry in entries] == [1, 1, 1]


def test_what_the_search_writes_is_what_the_app_reads(project: Path) -> None:
    record_rejection(
        {"fast": 5, "slow": 80},
        {"sharpe": 0.25, "max_drawdown_pct": 31.5},
        "drawdown_ceiling",
        project / "state" / "graveyard.jsonl",
    )

    (entry,) = read_graveyard(project)["entries"]

    assert entry["candidate"] == {"fast": 5, "slow": 80}
    assert entry["reason"] == "drawdown_ceiling"
    assert entry["sharpe"] == 0.25
    assert entry["max_drawdown_pct"] == 31.5


def test_a_metric_recorded_as_null_stays_null(project: Path) -> None:
    _bury(project, _record(5, "backtest_failed", 1, sharpe=None))

    (entry,) = read_graveyard(project)["entries"]

    assert entry["sharpe"] is None


def test_lines_that_are_not_rejections_are_counted_and_not_shown(project: Path) -> None:
    _bury(project, _record(5, "edge_winner", 1))
    _bury(project, "not json")
    _bury(project, "[1, 2]")
    _bury(project, "")
    _bury(project, {**_record(10, "edge_winner", 1), "schema_version": 99})
    _bury(project, _record(15, "edge_winnner", 1))
    _bury(project, json.dumps(_record(20, "edge_winner", 1)).replace("0.5", "NaN"))
    _bury(project, {"reason": "edge_winner", "schema_version": GRAVEYARD_SCHEMA_VERSION})

    graveyard = read_graveyard(project)

    assert graveyard["records"] == 1
    assert graveyard["unread_lines"] == 6
    assert [entry["candidate"]["fast"] for entry in graveyard["entries"]] == [5]


def test_a_graveyard_too_large_to_be_one_is_missing_not_read(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bury(project, _record(5, "edge_winner", 1))
    size = (project / "state" / "graveyard.jsonl").stat().st_size
    monkeypatch.setattr("strategylab.api.history.MAX_GRAVEYARD_BYTES", size - 1)

    graveyard = read_graveyard(project)

    assert graveyard["available"] is False
    assert f"{size} bytes" in graveyard["reason"]


# --------------------------------------------------------------------------
# The routes.
# --------------------------------------------------------------------------


@pytest.fixture
def client(project: Path) -> TestClient:
    """A client for an application serving `project`, presenting itself as local."""
    return TestClient(create_app(project, TOKEN, {}), base_url="http://127.0.0.1")


def test_the_routes_return_what_the_readers_return(
    project: Path, strategy: StrategyFile, client: TestClient
) -> None:
    path = _search(project, "01")
    _exam(project, "01")
    _bury(project, _record(5, "edge_winner", 1))

    searches = client.get("/api/strategies/ma_crossover/searches", headers=AUTH)
    detail = client.get(f"/api/strategies/ma_crossover/searches/{path.name}", headers=AUTH)
    exams = client.get("/api/strategies/ma_crossover/exams", headers=AUTH)
    graveyard = client.get("/api/graveyard", headers=AUTH)

    assert searches.json() == list_searches(project, strategy)
    assert detail.json() == search_detail(project, strategy, path.name)
    assert exams.json() == list_exams(project, strategy)
    assert graveyard.json() == read_graveyard(project)
    for response in (searches, detail, exams, graveyard):
        assert response.status_code == 200


@pytest.mark.parametrize(
    "route",
    [
        "/api/strategies/ma_crossover/searches",
        "/api/strategies/ma_crossover/searches/search-20260101T000000Z.json",
        "/api/strategies/ma_crossover/exams",
        "/api/graveyard",
    ],
)
def test_no_route_answers_without_the_token(project: Path, client: TestClient, route: str) -> None:
    _search(project, "01")

    assert client.get(route).status_code == 401


@pytest.mark.parametrize(
    "strategy_id",
    ["no_such", "risk_limits", "MA_CROSSOVER", "ma_crossover.yaml", "..%2Fconfigs%2Fma_crossover"],
)
@pytest.mark.parametrize("tail", ["searches", "searches/search-20260101T000000Z.json", "exams"])
def test_an_id_that_is_not_a_listed_strategy_is_not_found(
    project: Path, client: TestClient, strategy_id: str, tail: str
) -> None:
    _search(project, "01")

    response = client.get(f"/api/strategies/{strategy_id}/{tail}", headers=AUTH)

    assert response.status_code == 404


def test_a_search_that_is_not_listed_is_not_found(project: Path, client: TestClient) -> None:
    _search(project, "01")
    shutil.copyfile(
        project / "state" / "search-20260101T000000Z.json",
        project / "search-20260109T000000Z.json",
    )

    absent = client.get(
        "/api/strategies/ma_crossover/searches/search-20260102T000000Z.json", headers=AUTH
    )
    # An encoded slash is decoded before routing, so this names no route at all.
    climbing = client.get(
        "/api/strategies/ma_crossover/searches/..%2Fsearch-20260109T000000Z.json", headers=AUTH
    )

    assert absent.status_code == 404
    assert absent.json() == {"detail": "no such search"}
    assert climbing.status_code == 404


# --------------------------------------------------------------------------
# Files written to break the reader. Each of these once took a route down.
# --------------------------------------------------------------------------

#: Damaged files, as the bytes written. None is a search this app can read.
DAMAGED = {
    "bytes that are not text": b"\xff",
    "nesting deeper than any artifact": b"[" * 200_000,
    "nesting a response cannot carry": (
        b'{"schema_version": 8, "noise_gate": {}, "grid_size": ' + b"[" * 100 + b"]" * 100 + b"}"
    ),
    "a number that reads as infinity": b'{"schema_version": 8, "starting_equity": 1e999}',
    "text that is not Unicode": b'{"schema_version": 8, "generated_at": "\\ud800"}',
    "a whole number of four hundred digits": b'{"schema_version": 8, "grid_size": 1'
    + b"0" * 400
    + b"}",
}


@pytest.mark.parametrize("damage", sorted(DAMAGED))
def test_a_damaged_file_is_listed_as_not_read_and_takes_no_route_down(
    project: Path, client: TestClient, damage: str
) -> None:
    good = _search(project, "01")
    for name in ("search-20260102T000000Z.json", "validation-20260102T000000Z.json"):
        (project / "state" / name).write_bytes(DAMAGED[damage])
    (project / "state" / "graveyard.jsonl").write_bytes(
        DAMAGED[damage] + b"\n" + json.dumps(_record(5, "edge_winner", 1)).encode() + b"\n"
    )

    answers = {
        route: client.get(route, headers=AUTH)
        for route in (
            "/api/overview",
            "/api/strategies/ma_crossover/searches",
            "/api/strategies/ma_crossover/searches/search-20260102T000000Z.json",
            "/api/strategies/ma_crossover/exams",
            "/api/graveyard",
        )
    }

    assert {route: answer.status_code for route, answer in answers.items()} == dict.fromkeys(
        answers, 200
    )
    searches = answers["/api/strategies/ma_crossover/searches"].json()
    assert [(entry["artifact"], entry["readable"]) for entry in searches] == [
        ("search-20260102T000000Z.json", False),
        (good.name, True),
    ]
    assert searches[0]["reason"] == "the file is not valid JSON"
    detail = answers["/api/strategies/ma_crossover/searches/search-20260102T000000Z.json"].json()
    assert detail["available"] is False
    (exam,) = answers["/api/strategies/ma_crossover/exams"].json()
    assert exam["readable"] is False
    graveyard = answers["/api/graveyard"].json()
    assert (graveyard["records"], graveyard["unread_lines"]) == (1, 1)
    latest = answers["/api/overview"].json()["strategies"][0]["latest_search"]
    assert latest["artifact"] == good.name


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"generated_at": {"a": 1}}, "generated_at"),
        ({"grid_size": "ninety"}, "cells"),
        ({"winner": {"params": {"fast": "ten"}, "sharpe": 0.5}}, "candidate"),
        ({"winner": {"params": {"fast": 1.5}, "sharpe": 0.5}}, "candidate"),
        ({"winner": {"params": {"fast": 10}, "sharpe": [0.5]}}, "sharpe"),
        ({"winner": {"params": {"fast": 10}, "sharpe": True}}, "sharpe"),
        ({"winner": {"params": {"fast": 10}, "max_drawdown_pct": "12"}}, "max_drawdown_pct"),
    ],
)
def test_a_field_of_the_wrong_kind_is_none_not_passed_on(
    project: Path, strategy: StrategyFile, changes: dict[str, Any], field: str
) -> None:
    _search(project, "01", **changes)

    (entry,) = list_searches(project, strategy)

    assert entry["readable"] is True
    assert entry[field] is None


def test_gate_fields_of_the_wrong_kind_are_none_and_the_summary_is_empty(
    project: Path, strategy: StrategyFile
) -> None:
    _search(
        project,
        "01",
        noise_gate={"status": "failed", "beaten": "25", "required": 9.5, "summary": ["x"]},
    )

    (entry,) = list_searches(project, strategy)

    assert entry["gate"] == {
        "status": "failed",
        "beaten": None,
        "required": None,
        "scoreable_trials": None,
        "summary": "",
    }


@pytest.mark.parametrize("status", ["PASSED", "", None, 1, ["passed"]])
def test_a_gate_with_a_status_outside_the_vocabulary_is_no_gate(
    project: Path, strategy: StrategyFile, status: Any
) -> None:
    _search(project, "01", noise_gate={"status": status, "beaten": 100, "required": 95})

    (entry,) = list_searches(project, strategy)

    assert entry["readable"] is False
    assert entry["reason"] == "the file records no noise gate this app knows"


@pytest.mark.parametrize("outcome", ["passed", "PASS", "", None, 1])
def test_an_exam_with_an_outcome_outside_the_vocabulary_is_not_read(
    project: Path, strategy: StrategyFile, outcome: Any
) -> None:
    _exam(project, "01", outcome=outcome)

    (entry,) = list_exams(project, strategy)

    assert entry["readable"] is False
    assert entry["reason"] == "the file records no outcome this app knows"


def test_exam_fields_of_the_wrong_kind_are_none(project: Path, strategy: StrategyFile) -> None:
    _exam(
        project,
        "01",
        params=[10, 40],
        reason={"a": 1},
        pooled={"sharpe": "0.75", "max_drawdown_pct": None, "trades": 31.5},
        walk_forward_touches="two",
        folds="three",
    )

    (entry,) = list_exams(project, strategy)

    assert entry["readable"] is True
    assert entry["params"] is None
    assert entry["reason"] == ""
    assert (entry["sharpe"], entry["max_drawdown_pct"], entry["trades"]) == (None, None, None)
    assert entry["read_number"] is None
    assert entry["folds"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"starting_equity": "inf"},
        {"starting_equity": "10000"},
        {"max_drawdown_ceiling_pct": "nan"},
        {"starting_equity": True},
    ],
)
def test_text_that_spells_a_number_is_not_a_number(
    project: Path, strategy: StrategyFile, changes: dict[str, Any]
) -> None:
    path = _search(project, "01", **changes)

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    assert detail["available"] is False


def test_cell_figures_of_the_wrong_kind_are_none(project: Path, strategy: StrategyFile) -> None:
    path = _search(project, "01")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cells"][0].update(sharpe="0.9", max_drawdown_pct=[30.0], plateau_score={"a": 1})
    payload["equity_curves"]["cells"][0] = [None, "10000", 12500.0]
    path.write_text(json.dumps(payload), encoding="utf-8")

    detail = search_detail(project, strategy, path.name)

    assert detail is not None
    first = detail["cells"][0]
    assert (first["sharpe"], first["max_drawdown_pct"], first["plateau_score"]) == (
        None,
        None,
        None,
    )
    assert first["curve"] == [None, None, 12500.0]


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate": {"fast": 1.5}},
        {"candidate": {"fast": "five"}},
        {"candidate": [5, 80]},
        {"recorded_at": 20260301},
        {"metrics": "none"},
    ],
)
def test_a_graveyard_record_with_a_field_of_the_wrong_kind_is_not_shown(
    project: Path, changes: dict[str, Any]
) -> None:
    _bury(project, {**_record(5, "edge_winner", 1), **changes})

    graveyard = read_graveyard(project)

    assert (graveyard["records"], graveyard["unread_lines"]) == (0, 1)


def test_graveyard_metrics_of_the_wrong_kind_are_none(project: Path) -> None:
    record = _record(5, "edge_winner", 1)
    record["metrics"] = {"sharpe": "0.5", "max_drawdown_pct": [21.0]}
    _bury(project, record)

    (entry,) = read_graveyard(project)["entries"]

    assert (entry["sharpe"], entry["max_drawdown_pct"]) == (None, None)

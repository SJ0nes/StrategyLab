"""Tests for the final exam from the window: what unlocks it, what it runs, and what stops it.

NOTHING HERE RUNS ``strategylab validate``. Every job is started with a
stand-in that records the command and starts nothing, in a throwaway project,
and the project's own ``protocol.yaml`` is never opened for writing. A test
that ran the exam would spend a held-back read in its temporary copy, and the
habit of running it is the thing to avoid.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.drafts import confirm
from strategylab.api.jobs import (
    EXAM_PHRASE,
    FINAL_EXAM,
    JOB_KINDS,
    JOBS_DIR,
    JobRefused,
    command_for,
    exam_refusal,
    request_stop,
    start_job,
)
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.core.config import config_content_hash
from strategylab.search.loop import SEARCH_SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]
TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NOW = datetime.now(UTC).replace(microsecond=0)
PASSED = "search-20261111T000000Z.json"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding the committed protocol, configs and limits."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    return tmp_path


def _search(root: Path, name: str, status: str, **changes: Any) -> None:
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": "2026-11-11T00:00:00+00:00",
        "strategy": "ma_crossover",
        "grid_size": 90,
        "config_content_hash": config_content_hash(root / "configs" / "ma_crossover.yaml"),
        "winner": {
            "params": {"fast": 10, "slow": 80, "trend": 150},
            "sharpe": 1.4,
            "max_drawdown_pct": 9.0,
        },
        "noise_gate": {
            "status": status,
            "beaten": 97,
            "required": 95,
            "scoreable_trials": 100,
            "summary": "x",
        },
    }
    payload.update(changes)
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / name).write_text(json.dumps(payload), encoding="utf-8")


def _strategy(root: Path) -> StrategyFile:
    found = find_strategy(root, "ma_crossover")
    assert found is not None
    confirm(root, found, found.config_hash, NOW)
    return found


class Spawned:
    """Stands in for starting a process: records what it was asked to start."""

    def __init__(self) -> None:
        """Start with nothing asked for."""
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], root: Path, log: Path) -> None:
        """Record the command; start nothing."""
        self.calls.append(command)


def test_the_exam_is_a_kind_of_job_that_runs_no_trials() -> None:
    assert JOB_KINDS[FINAL_EXAM] == 0
    assert EXAM_PHRASE == "SPEND ONE LOOK"


def test_the_exam_is_locked_until_the_newest_search_passed_the_luck_test(project: Path) -> None:
    strategy = _strategy(project)
    assert exam_refusal(project, strategy) == ("no search on the strategy file as it is now", None)

    _search(project, "search-20261110T000000Z.json", "failed")
    assert exam_refusal(project, strategy) == ("its newest search did not pass the luck test", None)

    _search(project, PASSED, "passed")
    assert exam_refusal(project, strategy) == (None, PASSED)


def test_a_pass_on_an_earlier_version_of_the_file_does_not_unlock_it(project: Path) -> None:
    _search(project, PASSED, "passed", config_content_hash="0" * 64)

    assert (
        exam_refusal(project, _strategy(project))[0]
        == "no search on the strategy file as it is now"
    )


def test_a_newer_search_that_failed_locks_it_again(project: Path) -> None:
    _search(project, PASSED, "passed")
    _search(project, "search-20261112T000000Z.json", "failed")

    assert (
        exam_refusal(project, _strategy(project))[0]
        == "its newest search did not pass the luck test"
    )


def test_the_exam_is_refused_in_a_git_worktree(project: Path) -> None:
    _search(project, PASSED, "passed")
    (project / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    reason, artifact = exam_refusal(project, _strategy(project))

    assert artifact is None
    assert reason is not None and "worktree" in reason


def test_the_exam_is_allowed_in_a_main_checkout(project: Path) -> None:
    _search(project, PASSED, "passed")
    (project / ".git").mkdir()

    assert exam_refusal(project, _strategy(project)) == (None, PASSED)


@pytest.mark.parametrize("phrase", [None, "", "spend one look", "SPEND ONE", "ma_crossover", "yes"])
def test_the_exam_starts_only_with_the_phrase_typed(project: Path, phrase: str | None) -> None:
    _search(project, PASSED, "passed")
    spawn = Spawned()

    with pytest.raises(JobRefused, match="SPEND ONE LOOK"):
        start_job(project, _strategy(project), FINAL_EXAM, NOW, spawn=spawn, phrase=phrase)

    assert spawn.calls == []
    assert not (project / JOBS_DIR).exists()


def test_the_exam_runs_validate_on_the_search_it_was_started_on(project: Path) -> None:
    _search(project, PASSED, "passed")
    spawn = Spawned()

    job = start_job(
        project, _strategy(project), FINAL_EXAM, NOW, spawn=spawn, phrase=f"  {EXAM_PHRASE} "
    )

    record = json.loads((project / JOBS_DIR / f"{job['id']}.json").read_text(encoding="utf-8"))
    assert record["artifact"] == PASSED
    assert command_for(record, project) == [
        sys.executable,
        "-P",
        "-m",
        "strategylab.cli",
        "validate",
        f"state/{PASSED}",
    ]
    assert len(spawn.calls) == 1


def test_a_locked_exam_starts_nothing_even_with_the_phrase(project: Path) -> None:
    _search(project, "search-20261110T000000Z.json", "failed")
    spawn = Spawned()

    with pytest.raises(JobRefused, match="did not pass the luck test"):
        start_job(project, _strategy(project), FINAL_EXAM, NOW, spawn=spawn, phrase=EXAM_PHRASE)

    assert spawn.calls == []


@pytest.mark.parametrize("artifact", ["../protocol.yaml", "search-x.json", None, PASSED])
def test_a_record_naming_anything_but_a_search_that_is_there_builds_no_command(
    project: Path, artifact: Any
) -> None:
    strategy = _strategy(project)
    record = {
        "id": "20261111T120000Z-final_exam",
        "kind": FINAL_EXAM,
        "strategy_id": "ma_crossover",
        "config_hash": strategy.config_hash,
        "artifact": artifact,
    }

    with pytest.raises(JobRefused):
        command_for(record, project)


def test_the_exam_cannot_be_asked_to_stop_once_started(project: Path) -> None:
    _search(project, PASSED, "passed")
    job = start_job(
        project, _strategy(project), FINAL_EXAM, NOW, spawn=Spawned(), phrase=EXAM_PHRASE
    )

    with pytest.raises(JobRefused, match="cannot be stopped"):
        request_stop(project, job["id"], NOW)


# --------------------------------------------------------------------------
# The routes.
# --------------------------------------------------------------------------


@pytest.fixture
def client(project: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    started: list[list[str]] = []
    monkeypatch.setattr(
        "strategylab.api.jobs._spawn_detached",
        lambda command, *args, **kwargs: started.append(command),
    )
    return TestClient(create_app(project, TOKEN, {}), base_url="http://127.0.0.1")


def test_the_exam_status_says_what_locks_it(project: Path, client: TestClient) -> None:
    _strategy(project)
    locked = client.get("/api/strategies/ma_crossover/final-exam", headers=AUTH).json()
    _search(project, PASSED, "passed")
    open_ = client.get("/api/strategies/ma_crossover/final-exam", headers=AUTH).json()

    assert locked == {"refusal": "no search on the strategy file as it is now", "artifact": None}
    assert open_ == {"refusal": None, "artifact": PASSED}


def test_the_exam_cannot_be_started_through_the_ordinary_job_route(
    project: Path, client: TestClient
) -> None:
    _search(project, PASSED, "passed")
    _strategy(project)

    answer = client.post("/api/strategies/ma_crossover/jobs/final_exam", headers=AUTH)

    assert answer.status_code == 409
    assert "SPEND ONE LOOK" in answer.json()["detail"]
    assert not (project / JOBS_DIR).exists()


def test_the_exam_route_starts_it_with_the_phrase(project: Path, client: TestClient) -> None:
    _search(project, PASSED, "passed")
    _strategy(project)

    wrong = client.post(
        "/api/strategies/ma_crossover/final-exam", json={"typed": "go"}, headers=AUTH
    )
    right = client.post(
        "/api/strategies/ma_crossover/final-exam", json={"typed": EXAM_PHRASE}, headers=AUTH
    )

    assert wrong.status_code == 409
    assert right.status_code == 201
    assert right.json()["kind"] == FINAL_EXAM


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {**AUTH, "Origin": "https://evil.example"}]
)
def test_the_exam_never_starts_without_the_token_from_this_machine(
    project: Path, client: TestClient, headers: dict[str, str]
) -> None:
    _search(project, PASSED, "passed")
    _strategy(project)

    answer = client.post(
        "/api/strategies/ma_crossover/final-exam", json={"typed": EXAM_PHRASE}, headers=headers
    )

    assert answer.status_code in (401, 403)
    assert not (project / JOBS_DIR).exists()


def test_the_projects_protocol_is_not_touched(project: Path, client: TestClient) -> None:
    _search(project, PASSED, "passed")
    _strategy(project)
    before = (project / "protocol.yaml").read_bytes()

    client.post(
        "/api/strategies/ma_crossover/final-exam", json={"typed": EXAM_PHRASE}, headers=AUTH
    )

    assert (project / "protocol.yaml").read_bytes() == before
    assert (REPOSITORY / "protocol.yaml").read_bytes().count(b"walk_forward_touches: 0") == 1

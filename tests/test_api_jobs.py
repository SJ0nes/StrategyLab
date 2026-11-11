"""Tests for the work the app starts: what may be started, and how its state is read.

Almost every test starts no process. `start_job` is handed a stand-in that
records what it was asked to start, and a job's files are then written by hand
to put it in the state under test - so "running", "lost" and "stopped" are
read from files the test wrote, at a time the test chose.

Two tests start a real process: one shows that the detached start writes the
child's output to the log, and one runs the runner module as the server would,
on a job whose record names nothing runnable, so no search is ever started.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.drafts import confirm
from strategylab.api.job_runner import COULD_NOT_RUN, _beat, run_job
from strategylab.api.jobs import (
    JOB_KINDS,
    JOBS_DIR,
    MAX_LAST_LINE,
    RECENT_JOBS,
    STALE_SECONDS,
    STOPPED_EXIT_CODE,
    JobRefused,
    _spawn_detached,
    command_for,
    job_environment,
    list_jobs,
    parse_trial_line,
    read_job,
    request_stop,
    start_job,
    stop_file,
    trial_line,
)
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.core.config import config_content_hash
from strategylab.search.noise import RELIABLE_TRIALS

REPOSITORY = Path(__file__).resolve().parents[1]

TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: The moment the tests call "now": the real one, to the second. A job's files
#: are written by the real clock, and a job is read as running or lost by how
#: old they are, so a made-up date would make every fresh file look stale.
NOW = datetime.now(UTC).replace(microsecond=0)
STAMP = f"{NOW:%Y%m%dT%H%M%SZ}"
SEARCH_ID = f"{STAMP}-search"
LUCK_ID = f"{STAMP}-luck_test"

#: Content hash of the committed strategy, which every test project copies.
MA_HASH = config_content_hash(REPOSITORY / "configs" / "ma_crossover.yaml")


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
    """The committed strategy, its rules confirmed, as a job may only start for one."""
    found = find_strategy(project, "ma_crossover")
    assert found is not None
    confirm(project, found, found.config_hash, NOW)
    return found


class Spawned:
    """Stands in for starting a process: records what it was asked to start."""

    def __init__(self) -> None:
        """Start with nothing asked for."""
        self.calls: list[tuple[list[str], Path, Path]] = []
        self.venue: list[bool] = []

    def __call__(self, command: list[str], root: Path, log: Path, venue: bool = False) -> None:
        """Record one request to start a process, and start none."""
        self.calls.append((command, root, log))
        self.venue.append(venue)


def _start(project: Path, strategy: StrategyFile, kind: str = "luck_test") -> dict[str, Any]:
    return start_job(project, strategy, kind, NOW, spawn=Spawned())


def _done(project: Path, job_id: str, code: int | None) -> None:
    (project / JOBS_DIR / f"{job_id}.done").write_text(
        json.dumps({"exit_code": code, "finished_at": "2026-03-01T12:30:00+00:00"}),
        encoding="utf-8",
    )


def _log(project: Path, job_id: str, text: str) -> None:
    (project / JOBS_DIR / f"{job_id}.log").write_text(text, encoding="utf-8")


def _written_at(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


# --------------------------------------------------------------------------
# The trial line: one writer, one reader.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("finished", "total"), [(1, 1), (37, 100), (100, 100), (5, 2000)])
def test_the_line_the_search_prints_is_the_line_the_app_reads(finished: int, total: int) -> None:
    assert parse_trial_line(trial_line(finished, total)) == (finished, total)
    assert parse_trial_line(f"  {trial_line(finished, total)}  ") == (finished, total)


def test_the_trial_line_reads_as_a_sentence() -> None:
    assert trial_line(37, 100) == "surrogate 37 of 100 done"


@pytest.mark.parametrize(
    "line", ["", "Searching ma_crossover - 90 cells", "surrogate of 100 done", "37 of 100"]
)
def test_a_line_that_is_not_a_trial_line_is_not_progress(line: str) -> None:
    assert parse_trial_line(line) is None


# --------------------------------------------------------------------------
# What a job may run.
# --------------------------------------------------------------------------


def test_the_kinds_are_two_searches_and_the_daily_check() -> None:
    assert JOB_KINDS == {"search": 0, "luck_test": RELIABLE_TRIALS, "daily_check": 0}


@pytest.mark.parametrize(("kind", "trials"), [("search", "0"), ("luck_test", str(RELIABLE_TRIALS))])
def test_the_command_is_built_from_the_kind_and_the_registry(
    project: Path, kind: str, trials: str
) -> None:
    job_id = f"{STAMP}-{kind}"
    record = {"id": job_id, "kind": kind, "strategy_id": "ma_crossover", "config_hash": MA_HASH}

    assert command_for(record, project) == [
        sys.executable,
        "-P",
        "-m",
        "strategylab.cli",
        "search",
        "configs/ma_crossover.yaml",
        "--trials",
        trials,
        "--stop-file",
        f"state/jobs/{job_id}.stop",
    ]


def test_nothing_a_record_says_about_the_config_or_the_trials_reaches_the_command(
    project: Path,
) -> None:
    honest = {
        "id": SEARCH_ID,
        "kind": "search",
        "strategy_id": "ma_crossover",
        "config_hash": MA_HASH,
    }
    doctored = {**honest, "config": "../elsewhere.yaml", "trials": 1, "command": ["validate"]}

    assert command_for(doctored, project) == command_for(honest, project)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "validate"},
        {"kind": "run"},
        {"kind": None},
        {"kind": ["search"]},
        {"id": "../../protocol"},
        {"id": "20260301T120000Z-search --trials 1"},
        {"id": None},
        {"strategy_id": "no_such"},
        {"strategy_id": "risk_limits"},
        {"strategy_id": "../configs/ma_crossover"},
        {"strategy_id": None},
    ],
)
def test_a_record_naming_anything_else_builds_no_command(
    project: Path, changes: dict[str, Any]
) -> None:
    record = {"id": SEARCH_ID, "kind": "search", "strategy_id": "ma_crossover", **changes}

    with pytest.raises(JobRefused):
        command_for(record, project)


# --------------------------------------------------------------------------
# Starting.
# --------------------------------------------------------------------------


def test_starting_writes_the_record_and_starts_the_runner_on_it(
    project: Path, strategy: StrategyFile
) -> None:
    spawn = Spawned()

    job = start_job(project, strategy, "luck_test", NOW, spawn=spawn)

    assert job == {
        "id": LUCK_ID,
        "kind": "luck_test",
        "strategy_id": "ma_crossover",
        "trials": RELIABLE_TRIALS,
        "started_at": NOW.isoformat(),
        "status": "running",
        "progress": None,
        "last_line": None,
    }
    assert spawn.calls == [
        (
            [sys.executable, "-P", "-m", "strategylab.api.job_runner", LUCK_ID],
            project,
            project / JOBS_DIR / f"{LUCK_ID}.log",
        )
    ]
    record = json.loads((project / JOBS_DIR / f"{LUCK_ID}.json").read_text(encoding="utf-8"))
    assert record["config"] == "configs/ma_crossover.yaml"
    assert record["strategy"] == "ma_crossover"


def test_a_job_is_named_in_utc_whatever_zone_the_clock_is_in(
    project: Path, strategy: StrategyFile
) -> None:
    elsewhere = NOW.astimezone(timezone(timedelta(hours=5)))

    job = start_job(project, strategy, "search", elsewhere, spawn=Spawned())

    assert job["id"] == SEARCH_ID
    assert job["started_at"] == NOW.isoformat()
    assert job["started_at"].endswith("+00:00")


@pytest.mark.parametrize("kind", ["validate", "run", "", "SEARCH", "search "])
def test_a_kind_outside_the_list_is_refused_and_starts_nothing(
    project: Path, strategy: StrategyFile, kind: str
) -> None:
    spawn = Spawned()

    with pytest.raises(JobRefused):
        start_job(project, strategy, kind, NOW, spawn=spawn)

    assert spawn.calls == []
    assert not (project / JOBS_DIR).exists()


def test_a_second_job_is_refused_while_one_runs_and_names_it(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    spawn = Spawned()

    with pytest.raises(JobRefused, match=LUCK_ID):
        start_job(project, strategy, "search", NOW + timedelta(seconds=30), spawn=spawn)

    assert spawn.calls == []
    assert sorted(path.name for path in (project / JOBS_DIR).iterdir()) == [f"{LUCK_ID}.json"]


@pytest.mark.parametrize("code", [0, 1, STOPPED_EXIT_CODE])
def test_a_job_that_has_ended_does_not_block_the_next(
    project: Path, strategy: StrategyFile, code: int
) -> None:
    _start(project, strategy, "luck_test")
    _done(project, LUCK_ID, code)

    later = start_job(project, strategy, "search", NOW + timedelta(seconds=30), spawn=Spawned())

    assert later["status"] == "running"


def test_a_job_that_went_silent_does_not_block_the_next(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    long_ago = NOW - timedelta(seconds=STALE_SECONDS + 1)
    _written_at(project / JOBS_DIR / f"{LUCK_ID}.json", long_ago)

    later = start_job(project, strategy, "search", NOW, spawn=Spawned())

    assert later["status"] == "running"
    assert read_job(project, LUCK_ID, NOW)["status"] == "lost"  # type: ignore[index]


def test_a_runner_that_cannot_be_started_is_a_refusal_and_a_failed_job(
    project: Path, strategy: StrategyFile
) -> None:
    def cannot(command: list[str], root: Path, log: Path) -> None:
        raise OSError("no such interpreter at " + str(root))

    with pytest.raises(JobRefused) as refusal:
        start_job(project, strategy, "search", NOW, spawn=cannot)

    assert str(project) not in str(refusal.value)
    assert read_job(project, SEARCH_ID, NOW)["status"] == "failed"  # type: ignore[index]
    assert list_jobs(project, NOW)["current"] is None


# --------------------------------------------------------------------------
# Reading a job's state.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (0, "finished"),
        (STOPPED_EXIT_CODE, "stopped"),
        (1, "failed"),
        (2, "failed"),
        (None, "failed"),
    ],
)
def test_a_job_that_ended_is_read_from_its_exit_code(
    project: Path, strategy: StrategyFile, code: int | None, status: str
) -> None:
    _start(project, strategy)
    _done(project, LUCK_ID, code)

    assert read_job(project, LUCK_ID, NOW)["status"] == status  # type: ignore[index]


def test_a_done_file_that_cannot_be_read_is_a_failure_not_a_success(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy)
    (project / JOBS_DIR / f"{LUCK_ID}.done").write_text("not json", encoding="utf-8")

    assert read_job(project, LUCK_ID, NOW)["status"] == "failed"  # type: ignore[index]


def test_a_job_with_a_recent_heartbeat_is_running_however_long_ago_it_started(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy)
    directory = project / JOBS_DIR
    hours_later = NOW + timedelta(hours=3)
    _written_at(directory / f"{LUCK_ID}.json", NOW)
    (directory / f"{LUCK_ID}.beat").touch()
    _written_at(directory / f"{LUCK_ID}.beat", hours_later - timedelta(seconds=STALE_SECONDS - 1))

    assert read_job(project, LUCK_ID, hours_later)["status"] == "running"  # type: ignore[index]


def test_a_job_whose_heartbeat_stopped_is_lost(project: Path, strategy: StrategyFile) -> None:
    _start(project, strategy)
    directory = project / JOBS_DIR
    hours_later = NOW + timedelta(hours=3)
    _written_at(directory / f"{LUCK_ID}.json", NOW)
    (directory / f"{LUCK_ID}.beat").touch()
    _written_at(directory / f"{LUCK_ID}.beat", hours_later - timedelta(seconds=STALE_SECONDS + 1))

    assert read_job(project, LUCK_ID, hours_later)["status"] == "lost"  # type: ignore[index]


def test_a_job_just_started_is_running_before_its_first_heartbeat(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy)
    _written_at(project / JOBS_DIR / f"{LUCK_ID}.json", NOW)

    soon = NOW + timedelta(seconds=STALE_SECONDS - 1)
    assert read_job(project, LUCK_ID, soon)["status"] == "running"  # type: ignore[index]


def test_progress_is_the_last_trial_line_in_the_log(project: Path, strategy: StrategyFile) -> None:
    _start(project, strategy)
    _log(
        project,
        LUCK_ID,
        "Searching ma_crossover - 90 cells\n"
        f"  {trial_line(1, 100)}\n  {trial_line(2, 100)}\n\n  {trial_line(3, 100)}\n",
    )

    job = read_job(project, LUCK_ID, NOW)

    assert job is not None
    assert job["progress"] == {"finished": 3, "total": 100}
    assert job["last_line"] == trial_line(3, 100)


def test_a_log_with_no_trial_line_has_no_progress_and_not_a_zero(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "search")
    _log(project, SEARCH_ID, "Searching ma_crossover - 90 cells\n")

    job = read_job(project, SEARCH_ID, NOW)

    assert job is not None
    assert job["progress"] is None
    assert job["last_line"] == "Searching ma_crossover - 90 cells"


def test_the_last_line_names_no_directory_and_is_cut_short(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "search")
    _log(
        project,
        SEARCH_ID,
        f"FileNotFoundError: {project}{os.sep}data{os.sep}SPY.parquet " + "x" * 900,
    )

    job = read_job(project, SEARCH_ID, NOW)

    assert job is not None
    assert str(project) not in job["last_line"]
    assert job["last_line"].startswith("FileNotFoundError: .")
    assert len(job["last_line"]) == MAX_LAST_LINE


def test_a_log_that_is_not_text_is_still_read(project: Path, strategy: StrategyFile) -> None:
    _start(project, strategy)
    (project / JOBS_DIR / f"{LUCK_ID}.log").write_bytes(
        b"\xff\xfe\n" + trial_line(4, 100).encode() + b"\n"
    )

    job = read_job(project, LUCK_ID, NOW)

    assert job is not None
    assert job["progress"] == {"finished": 4, "total": 100}


@pytest.mark.parametrize(
    "job_id",
    [
        "",
        "no-such",
        "20260301T120000Z-search",
        "../../protocol",
        "..\\..\\protocol",
        "protocol.yaml",
    ],
)
def test_an_id_that_names_no_job_reads_as_none(project: Path, job_id: str) -> None:
    (project / JOBS_DIR).mkdir(parents=True)
    shutil.copyfile(project / "protocol.yaml", project / "protocol.json")

    assert read_job(project, job_id, NOW) is None


@pytest.mark.parametrize("changes", [{"schema_version": 2}, {"kind": "validate"}, {"kind": None}])
def test_a_record_this_code_does_not_know_is_not_a_job(
    project: Path, strategy: StrategyFile, changes: dict[str, Any]
) -> None:
    _start(project, strategy)
    path = project / JOBS_DIR / f"{LUCK_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, **changes}), encoding="utf-8")

    assert read_job(project, LUCK_ID, NOW) is None
    assert list_jobs(project, NOW) == {"current": None, "recent": []}


def test_jobs_are_listed_newest_first_with_the_running_one_named(
    project: Path, strategy: StrategyFile
) -> None:
    start_job(project, strategy, "search", NOW, spawn=Spawned())
    _done(project, SEARCH_ID, 0)
    later = NOW + timedelta(seconds=30)
    second = start_job(project, strategy, "luck_test", later, spawn=Spawned())

    listed = list_jobs(project, later)

    assert [job["id"] for job in listed["recent"]] == [second["id"], SEARCH_ID]
    assert [job["status"] for job in listed["recent"]] == ["running", "finished"]
    assert listed["current"] == second


def test_only_the_most_recent_jobs_are_listed(project: Path, strategy: StrategyFile) -> None:
    for minute in range(RECENT_JOBS + 3):
        moment = NOW + timedelta(minutes=minute)
        job = start_job(project, strategy, "search", moment, spawn=Spawned())
        _done(project, job["id"], 0)

    listed = list_jobs(project, NOW + timedelta(hours=1))["recent"]

    assert len(listed) == RECENT_JOBS
    newest = NOW + timedelta(minutes=RECENT_JOBS + 2)
    assert listed[0]["id"] == f"{newest:%Y%m%dT%H%M%SZ}-search"


def test_a_project_that_has_run_no_job_lists_none(project: Path) -> None:
    assert list_jobs(project, NOW) == {"current": None, "recent": []}


# --------------------------------------------------------------------------
# Stopping.
# --------------------------------------------------------------------------


def test_asking_a_luck_test_to_stop_writes_its_stop_file_and_nothing_else(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    before = sorted(path.name for path in (project / JOBS_DIR).iterdir())

    job = request_stop(project, LUCK_ID, NOW)

    assert job["status"] == "stopping"
    assert stop_file(LUCK_ID) == JOBS_DIR / f"{LUCK_ID}.stop"
    after = sorted(path.name for path in (project / JOBS_DIR).iterdir())
    assert after == sorted([*before, f"{LUCK_ID}.stop"])
    assert list_jobs(project, NOW)["current"]["status"] == "stopping"


def test_a_stopping_job_still_blocks_the_next(project: Path, strategy: StrategyFile) -> None:
    _start(project, strategy, "luck_test")
    request_stop(project, LUCK_ID, NOW)

    with pytest.raises(JobRefused, match=LUCK_ID):
        start_job(project, strategy, "search", NOW + timedelta(seconds=5), spawn=Spawned())


def test_a_plain_search_cannot_be_asked_to_stop(project: Path, strategy: StrategyFile) -> None:
    _start(project, strategy, "search")

    with pytest.raises(JobRefused, match="cannot be stopped"):
        request_stop(project, SEARCH_ID, NOW)

    assert not (project / stop_file(SEARCH_ID)).exists()


def test_a_job_that_has_ended_cannot_be_asked_to_stop(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    _done(project, LUCK_ID, 0)

    with pytest.raises(JobRefused, match="not running"):
        request_stop(project, LUCK_ID, NOW)

    assert not (project / stop_file(LUCK_ID)).exists()


@pytest.mark.parametrize("job_id", ["no-such", "../../protocol", LUCK_ID])
def test_a_job_that_does_not_exist_cannot_be_asked_to_stop(project: Path, job_id: str) -> None:
    with pytest.raises(JobRefused, match="no such job"):
        request_stop(project, job_id, NOW)

    assert not (project / "state").exists()


# --------------------------------------------------------------------------
# The runner.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, STOPPED_EXIT_CODE])
def test_the_runner_runs_the_registrys_command_and_records_how_it_ended(
    project: Path, strategy: StrategyFile, code: int
) -> None:
    _start(project, strategy, "luck_test")
    ran: list[tuple[list[str], Path]] = []

    def run(command: list[str], root: Path) -> int:
        ran.append((command, root))
        assert (project / JOBS_DIR / f"{LUCK_ID}.beat").exists()
        return code

    returned = run_job(project, LUCK_ID, run, interval=0.01)

    record = {
        "id": LUCK_ID,
        "kind": "luck_test",
        "strategy_id": "ma_crossover",
        "config_hash": MA_HASH,
    }
    assert ran == [(command_for(record, project), project)]
    assert returned == code
    done = json.loads((project / JOBS_DIR / f"{LUCK_ID}.done").read_text(encoding="utf-8"))
    assert done["exit_code"] == code
    assert datetime.fromisoformat(done["finished_at"]).tzinfo is not None


def test_the_runner_records_an_end_even_when_the_command_could_not_be_run(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "search")

    def run(command: list[str], root: Path) -> int:
        raise OSError("cannot start")

    returned = run_job(project, SEARCH_ID, run, interval=0.01)

    assert returned == COULD_NOT_RUN
    done = json.loads((project / JOBS_DIR / f"{SEARCH_ID}.done").read_text(encoding="utf-8"))
    assert done["exit_code"] == COULD_NOT_RUN
    assert read_job(project, SEARCH_ID, NOW)["status"] == "failed"  # type: ignore[index]


@pytest.mark.parametrize("changes", [{"kind": "validate"}, {"strategy_id": "no_such"}])
def test_the_runner_runs_nothing_for_a_record_that_names_no_job(
    project: Path, strategy: StrategyFile, changes: dict[str, Any]
) -> None:
    _start(project, strategy, "search")
    path = project / JOBS_DIR / f"{SEARCH_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, **changes}), encoding="utf-8")
    ran: list[list[str]] = []

    returned = run_job(project, SEARCH_ID, lambda command, root: ran.append(command) or 0)

    assert ran == []
    assert returned == COULD_NOT_RUN
    done = json.loads((project / JOBS_DIR / f"{SEARCH_ID}.done").read_text(encoding="utf-8"))
    assert done["exit_code"] == COULD_NOT_RUN


def test_the_runner_writes_nothing_for_a_job_with_no_record(project: Path) -> None:
    (project / JOBS_DIR).mkdir(parents=True)

    returned = run_job(project, SEARCH_ID, lambda command, root: 0)

    assert returned == COULD_NOT_RUN
    assert list((project / JOBS_DIR).iterdir()) == []


def test_the_runner_module_run_as_a_process_ends_a_job_it_may_not_run(
    project: Path, strategy: StrategyFile
) -> None:
    # The real entry point, on a record doctored to name `validate`. Nothing is
    # searched and nothing is validated: the runner refuses and says so.
    _start(project, strategy, "search")
    path = project / JOBS_DIR / f"{SEARCH_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, "kind": "validate"}), encoding="utf-8")

    done = subprocess.run(
        [sys.executable, "-m", "strategylab.api.job_runner", SEARCH_ID],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == COULD_NOT_RUN
    assert "job not run" in done.stdout
    assert (
        json.loads((project / JOBS_DIR / f"{SEARCH_ID}.done").read_text(encoding="utf-8"))[
            "exit_code"
        ]
        == COULD_NOT_RUN
    )


def test_a_detached_process_writes_its_output_to_the_log(tmp_path: Path) -> None:
    log = tmp_path / "out.log"

    _spawn_detached([sys.executable, "-c", "print('from the child')"], tmp_path, log)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and "from the child" not in log.read_text(encoding="utf-8"):
        time.sleep(0.1)
    assert "from the child" in log.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The routes.
# --------------------------------------------------------------------------


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> Spawned:
    """Replace the real detached start, so no route test starts a process."""
    stand_in = Spawned()
    monkeypatch.setattr("strategylab.api.jobs._spawn_detached", stand_in)
    return stand_in


@pytest.fixture
def client(project: Path, strategy: StrategyFile, spawned: Spawned) -> TestClient:
    return TestClient(create_app(project, TOKEN, {}), base_url="http://127.0.0.1")


def test_starting_over_the_route_starts_one_runner_and_reports_the_job(
    project: Path, client: TestClient, spawned: Spawned
) -> None:
    answer = client.post("/api/strategies/ma_crossover/jobs/luck_test", headers=AUTH)

    assert answer.status_code == 201
    job = answer.json()
    assert job["kind"] == "luck_test"
    assert job["status"] == "running"
    assert [call[0][:4] for call in spawned.calls] == [
        [sys.executable, "-P", "-m", "strategylab.api.job_runner"]
    ]
    assert client.get("/api/jobs", headers=AUTH).json()["current"]["id"] == job["id"]


def test_a_second_start_over_the_route_is_a_conflict_that_names_the_first(
    client: TestClient, spawned: Spawned
) -> None:
    first = client.post("/api/strategies/ma_crossover/jobs/luck_test", headers=AUTH).json()

    second = client.post("/api/strategies/ma_crossover/jobs/search", headers=AUTH)

    assert second.status_code == 409
    assert first["id"] in second.json()["detail"]
    assert len(spawned.calls) == 1


@pytest.mark.parametrize("kind", ["validate", "run", "fetch", "SEARCH"])
def test_no_route_starts_anything_but_a_listed_kind(
    project: Path, client: TestClient, spawned: Spawned, kind: str
) -> None:
    answer = client.post(f"/api/strategies/ma_crossover/jobs/{kind}", headers=AUTH)

    assert answer.status_code == 404
    assert spawned.calls == []
    assert not (project / JOBS_DIR).exists()


def test_a_strategy_that_is_not_listed_starts_nothing(client: TestClient, spawned: Spawned) -> None:
    answer = client.post("/api/strategies/risk_limits/jobs/search", headers=AUTH)

    assert answer.status_code == 404
    assert spawned.calls == []


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {**AUTH, "Origin": "https://evil.example"},
        {**AUTH, "Host": "evil.example"},
    ],
)
@pytest.mark.parametrize(
    "route", ["/api/strategies/ma_crossover/jobs/search", f"/api/jobs/{LUCK_ID}/stop"]
)
def test_nothing_is_started_or_stopped_without_the_token_from_this_machine(
    project: Path, client: TestClient, spawned: Spawned, headers: dict[str, str], route: str
) -> None:
    answer = client.post(route, headers=headers)

    assert answer.status_code in (401, 403)
    assert spawned.calls == []
    assert not (project / JOBS_DIR).exists()


def test_a_job_cannot_be_started_with_a_get(client: TestClient, spawned: Spawned) -> None:
    answer = client.get("/api/strategies/ma_crossover/jobs/search", headers=AUTH)

    assert answer.status_code == 405
    assert spawned.calls == []


def test_stopping_over_the_route(project: Path, client: TestClient) -> None:
    job = client.post("/api/strategies/ma_crossover/jobs/luck_test", headers=AUTH).json()

    stopped = client.post(f"/api/jobs/{job['id']}/stop", headers=AUTH)
    missing = client.post("/api/jobs/20200101T000000Z-luck_test/stop", headers=AUTH)

    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopping"
    assert (project / stop_file(job["id"])).exists()
    assert missing.status_code == 409
    assert missing.json() == {"detail": "there is no such job"}


# --------------------------------------------------------------------------
# What a review of this code found, each pinned.
# --------------------------------------------------------------------------


def test_every_command_tells_python_not_to_import_from_where_it_runs(
    project: Path, strategy: StrategyFile
) -> None:
    spawn = Spawned()
    start_job(project, strategy, "search", NOW, spawn=spawn)
    record = {
        "id": SEARCH_ID,
        "kind": "search",
        "strategy_id": "ma_crossover",
        "config_hash": MA_HASH,
    }

    assert spawn.calls[0][0][1] == "-P"
    assert command_for(record, project)[1] == "-P"


def test_a_module_lying_in_the_served_directory_is_not_importable_by_a_job(
    tmp_path: Path,
) -> None:
    # A file planted where the job runs. Started the ordinary way, `-m` finds
    # it and runs it. Started as a job is - with the flag, or with the
    # variable its worker processes inherit - Python cannot find it at all.
    (tmp_path / "planted.py").write_text("raise SystemExit(7)\n", encoding="utf-8")

    def run(arguments: list[str], environ: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *arguments, "-m", "planted"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            env=environ,
        )

    plain = {name: value for name, value in os.environ.items() if name != "PYTHONSAFEPATH"}
    unguarded = run([], plain)
    by_flag = run(["-P"], plain)
    by_variable = run([], job_environment(plain))

    assert unguarded.returncode == 7
    for guarded in (by_flag, by_variable):
        assert guarded.returncode != 7
        assert "No module named planted" in guarded.stderr


def test_a_job_is_started_without_the_token_and_without_any_venue_setting() -> None:
    # Names only: the values are the same word throughout, so that this file
    # holds nothing shaped like a credential.
    withheld = [
        "STRATEGYLAB_APP_TOKEN",
        "strategylab_app_token",
        "T212_API_KEY",
        "T212_API_SECRET",
        "T212_ENVIRONMENT",
        "t212_api_key",
    ]
    kept = ["PATH", "NOT_T212_API_KEY", "STRATEGYLAB_APP_TOKEN_OTHER"]
    server = dict.fromkeys([*withheld, *kept], "present")

    started_with = job_environment(server)

    assert started_with == {**dict.fromkeys(kept, "present"), "PYTHONSAFEPATH": "1"}


def test_the_detached_process_does_not_see_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRATEGYLAB_APP_TOKEN", "planted-value-for-this-test")
    log = tmp_path / "out.log"
    script = (
        "import os; print('token seen:', 'STRATEGYLAB_APP_TOKEN' in os.environ, "
        "os.environ.get('PYTHONSAFEPATH'))"
    )

    _spawn_detached([sys.executable, "-c", script], tmp_path, log)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and "token seen" not in log.read_text(encoding="utf-8"):
        time.sleep(0.1)
    assert "token seen: False 1" in log.read_text(encoding="utf-8")


def test_a_log_line_of_thousands_of_digits_is_not_progress_and_breaks_nothing(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy)
    _log(project, LUCK_ID, "surrogate " + "9" * 5000 + " of 100 done\n")

    job = read_job(project, LUCK_ID, NOW)

    assert job is not None
    assert job["progress"] is None
    assert parse_trial_line("surrogate 1234567 of 100 done") is None
    assert parse_trial_line("surrogate 999999 of 999999 done") == (999999, 999999)


def test_a_running_job_is_found_behind_any_number_of_later_records(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    for minute in range(1, RECENT_JOBS + 2):
        later = NOW + timedelta(minutes=minute)
        job_id = f"{later:%Y%m%dT%H%M%SZ}-search"
        record = {
            "schema_version": 1,
            "id": job_id,
            "kind": "search",
            "strategy_id": "ma_crossover",
        }
        (project / JOBS_DIR / f"{job_id}.json").write_text(json.dumps(record), encoding="utf-8")
        _done(project, job_id, 0)

    listed = list_jobs(project, NOW)

    assert len(listed["recent"]) == RECENT_JOBS
    assert LUCK_ID not in [job["id"] for job in listed["recent"]]
    assert listed["current"]["id"] == LUCK_ID
    with pytest.raises(JobRefused, match=LUCK_ID):
        start_job(project, strategy, "search", NOW + timedelta(seconds=1), spawn=Spawned())


def test_a_heartbeat_dated_years_ahead_does_not_keep_a_dead_job_running(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy)
    directory = project / JOBS_DIR
    (directory / f"{LUCK_ID}.beat").touch()
    _written_at(directory / f"{LUCK_ID}.beat", NOW + timedelta(days=3650))
    _written_at(directory / f"{LUCK_ID}.json", NOW)

    much_later = NOW + timedelta(hours=1)
    assert read_job(project, LUCK_ID, much_later)["status"] == "lost"  # type: ignore[index]


def test_a_file_written_a_moment_after_now_was_read_is_fresh(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy)
    _written_at(project / JOBS_DIR / f"{LUCK_ID}.json", NOW + timedelta(seconds=2))

    assert read_job(project, LUCK_ID, NOW)["status"] == "running"  # type: ignore[index]


def test_a_record_that_names_a_different_job_is_not_that_job(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    path = project / JOBS_DIR / f"{LUCK_ID}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**record, "id": SEARCH_ID}), encoding="utf-8")
    ran: list[list[str]] = []

    returned = run_job(project, LUCK_ID, lambda command, root: ran.append(command) or 0)

    assert read_job(project, LUCK_ID, NOW) is None
    assert ran == []
    assert returned == COULD_NOT_RUN


@pytest.mark.parametrize("suffix", ["\n", " ", "\n.json", "/../x"])
def test_a_job_id_with_anything_after_it_is_not_a_job_id(
    project: Path, strategy: StrategyFile, suffix: str
) -> None:
    _start(project, strategy)

    assert read_job(project, LUCK_ID, NOW) is not None
    assert read_job(project, LUCK_ID + suffix, NOW) is None
    with pytest.raises(JobRefused):
        command_for(
            {
                "id": LUCK_ID + suffix,
                "kind": "luck_test",
                "strategy_id": "ma_crossover",
                "config_hash": MA_HASH,
            },
            project,
        )


def test_a_stop_file_that_cannot_be_written_is_a_refusal_not_an_error(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "luck_test")
    (project / stop_file(LUCK_ID)).mkdir()

    with pytest.raises(JobRefused, match="could not be written"):
        request_stop(project, LUCK_ID, NOW)


def test_a_jobs_folder_that_cannot_be_made_is_a_refusal_not_an_error(
    project: Path, strategy: StrategyFile
) -> None:
    (project / JOBS_DIR).write_text("in the way", encoding="utf-8")
    spawn = Spawned()

    with pytest.raises(JobRefused, match="could not be made"):
        start_job(project, strategy, "search", NOW, spawn=spawn)

    assert spawn.calls == []


def test_the_last_line_names_no_source_or_home_directory_either(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "search")
    places = [REPOSITORY / "src" / "strategylab" / "x.py", Path.home() / "elsewhere" / "y.py"]
    for place in places:
        _log(project, SEARCH_ID, f'  File "{place}", line 3, in run\n')

        job = read_job(project, SEARCH_ID, NOW)

        assert job is not None
        assert str(Path.home()) not in job["last_line"]
        assert str(REPOSITORY) not in job["last_line"]
        assert job["last_line"].endswith(f'{place.name}", line 3, in run')


def test_a_heartbeat_that_is_refused_once_goes_on_beating(tmp_path: Path) -> None:
    touched: list[int] = []
    stop = threading.Event()

    class Refusing:
        """A heartbeat file whose first two touches fail."""

        def touch(self) -> None:
            """Fail twice, then count."""
            touched.append(len(touched))
            if len(touched) <= 2:
                raise PermissionError("held by something else")
            if len(touched) == 4:
                stop.set()

    _beat(Refusing(), stop, 0.001)  # type: ignore[arg-type]

    assert len(touched) == 4


def test_an_id_written_to_climb_out_of_the_jobs_folder_reads_nothing(project: Path) -> None:
    # A well-formed record one folder up from the project, whose own id is the
    # path that reaches it. Only the shape of the id stands in the way.
    (project / JOBS_DIR).mkdir(parents=True)
    climbing = "../../outside"
    record = {
        "schema_version": 1,
        "id": climbing,
        "kind": "search",
        "strategy_id": "ma_crossover",
        "config_hash": MA_HASH,
    }
    (project / "outside.json").write_text(json.dumps(record), encoding="utf-8")
    assert (project / JOBS_DIR / f"{climbing}.json").exists()

    assert read_job(project, climbing, NOW) is None


def test_the_runner_refuses_a_job_whose_strategy_file_changed_after_it_started(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "search")
    path = project / "configs" / "ma_crossover.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("max: 30", "max: 25"), encoding="utf-8"
    )
    ran: list[list[str]] = []

    returned = run_job(project, SEARCH_ID, lambda command, root: ran.append(command) or 0)

    assert ran == []
    assert returned == COULD_NOT_RUN


def test_a_job_record_names_the_content_it_was_started_for(
    project: Path, strategy: StrategyFile
) -> None:
    _start(project, strategy, "search")

    record = json.loads((project / JOBS_DIR / f"{SEARCH_ID}.json").read_text(encoding="utf-8"))

    assert record["config_hash"] == strategy.config_hash == MA_HASH

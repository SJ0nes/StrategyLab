"""Tests for deploying to the practice account: record, daily check, schedule, live figures.

No test reaches a venue or starts a process. A search artifact, the account
log and the decision log are written by hand, so each figure asserted is one
the test can work out on paper; the daily check is started with a stand-in
that records the command it was given.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.deployment import (
    DEPLOYMENT_FILE,
    STOP_ALL_FILE,
    DeployRefused,
    check_refusal,
    deploy,
    read_deployment,
    set_paused,
    set_stop_all,
    stop_all,
    undeploy,
    venue_ready,
)
from strategylab.api.drafts import confirm
from strategylab.api.jobs import (
    DAILY_CHECK,
    JOBS_DIR,
    JobRefused,
    command_for,
    job_environment,
    list_job_records,
    request_stop,
    start_job,
)
from strategylab.api.live import MIN_CHECKS_FOR_SHARPE, read_live
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.api.schedule import (
    CHECK_AFTER,
    checked_days,
    due,
    missed_days,
    next_check,
    schedule_report,
    start_if_due,
)
from strategylab.backtest.metrics import max_drawdown, sharpe
from strategylab.core.config import config_content_hash
from strategylab.search.loop import SEARCH_SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]
TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: A Thursday, after the check time.
THURSDAY = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)

#: The practice account, set. Names only matter; the value is never a key.
READY = dict.fromkeys(["T212_API_KEY"], "present") | {"T212_ENVIRONMENT": "demo"}

ARTIFACT = "search-20261110T184030Z.json"


@pytest.fixture(autouse=True)
def practice_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the practice account in this process, as the daily check's runner would have it."""
    for name, value in READY.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root with the committed protocol, configs and limits, and one search on it."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    _search(tmp_path, ARTIFACT, status="failed")
    return tmp_path


def _dated(root: Path, moment: datetime) -> None:
    """Date every job file at ``moment``: a job is running or lost by how old its files are."""
    import os  # noqa: PLC0415

    for path in (root / JOBS_DIR).glob("*"):
        os.utime(path, (moment.timestamp(), moment.timestamp()))


def _search(root: Path, name: str, status: str = "failed", **changes: Any) -> None:
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": "2026-11-10T18:40:30+00:00",
        "strategy": "ma_crossover",
        "grid_size": 90,
        "config_content_hash": config_content_hash(root / "configs" / "ma_crossover.yaml"),
        "winner": {
            "params": {"fast": 10, "slow": 80, "trend": 150},
            "sharpe": 0.64,
            "max_drawdown_pct": 19.4,
        },
        "noise_gate": {
            "status": status,
            "beaten": 25,
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
    return found


def _confirmed(root: Path) -> StrategyFile:
    strategy = _strategy(root)
    confirm(root, strategy, strategy.config_hash, THURSDAY - timedelta(days=3))
    return strategy


def _deployed(root: Path, when: datetime = THURSDAY - timedelta(days=2)) -> StrategyFile:
    strategy = _confirmed(root)
    deploy(root, strategy, "ma_crossover", READY, when)
    return strategy


class Spawned:
    """Stands in for starting a process: records what it was asked to start."""

    def __init__(self) -> None:
        """Start with nothing asked for."""
        self.calls: list[list[str]] = []

    def __call__(self, command: list[str], root: Path, log: Path) -> None:
        """Record the command; start nothing."""
        self.calls.append(command)


# --------------------------------------------------------------------------
# The practice account.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "reason"),
    [
        ({}, "T212_ENVIRONMENT must be demo"),
        ({"T212_ENVIRONMENT": "live"} | dict.fromkeys(["T212_API_KEY"], "present"), "must be demo"),
        ({"T212_ENVIRONMENT": "demo"}, "T212_API_KEY is missing"),
        ({"T212_ENVIRONMENT": "demo"} | dict.fromkeys(["T212_API_KEY"], "  "), "is missing"),
        (READY, None),
    ],
)
def test_the_practice_account_is_set_only_by_demo_and_a_key(
    environ: dict[str, str], reason: str | None
) -> None:
    said = venue_ready(environ)

    assert said is None if reason is None else reason in (said or "")


# --------------------------------------------------------------------------
# Deploying.
# --------------------------------------------------------------------------


def test_a_confirmed_strategy_with_a_search_deploys_as_unproven_when_its_luck_test_failed(
    project: Path,
) -> None:
    strategy = _confirmed(project)

    deployed = deploy(project, strategy, "ma_crossover", READY, THURSDAY)

    assert deployed == {
        "available": True,
        "strategy_id": "ma_crossover",
        "artifact": ARTIFACT,
        "params": {"fast": 10, "slow": 80, "trend": 150},
        "proven": False,
        "deployed_at": THURSDAY.isoformat(),
        "paused": False,
        "paused_at": None,
        "current": True,
        "problem": None,
    }
    record = json.loads((project / DEPLOYMENT_FILE).read_text(encoding="utf-8"))
    assert record["config_hash"] == strategy.config_hash


def test_a_strategy_whose_luck_test_passed_deploys_as_proven(project: Path) -> None:
    _search(project, "search-20261111T000000Z.json", status="passed")

    deployed = deploy(project, _confirmed(project), "ma_crossover", READY, THURSDAY)

    assert deployed["proven"] is True
    assert deployed["artifact"] == "search-20261111T000000Z.json"


@pytest.mark.parametrize("typed", ["", "MA_CROSSOVER", "ma crossover", "risk_limits", "yes"])
def test_nothing_deploys_unless_its_name_is_typed(project: Path, typed: str) -> None:
    with pytest.raises(DeployRefused, match="type the strategy's name"):
        deploy(project, _confirmed(project), typed, READY, THURSDAY)

    assert not (project / DEPLOYMENT_FILE).exists()


def test_the_name_typed_may_carry_stray_space(project: Path) -> None:
    assert deploy(project, _confirmed(project), "  ma_crossover \n", READY, THURSDAY)["available"]


def test_nothing_deploys_without_the_practice_account(project: Path) -> None:
    with pytest.raises(DeployRefused, match="practice account is not set"):
        deploy(project, _confirmed(project), "ma_crossover", {"T212_ENVIRONMENT": "live"}, THURSDAY)

    assert not (project / DEPLOYMENT_FILE).exists()


def test_nothing_deploys_before_its_rules_are_confirmed(project: Path) -> None:
    with pytest.raises(DeployRefused, match="not been confirmed"):
        deploy(project, _strategy(project), "ma_crossover", READY, THURSDAY)


@pytest.mark.parametrize(
    "changes",
    [{"winner": None}, {"config_content_hash": "0" * 64}],
)
def test_nothing_deploys_without_a_version_from_a_search_on_the_file_as_it_is(
    project: Path, changes: dict[str, Any]
) -> None:
    _search(project, ARTIFACT, **changes)

    with pytest.raises(DeployRefused, match="run a backtest"):
        deploy(project, _confirmed(project), "ma_crossover", READY, THURSDAY)


def test_only_one_strategy_is_deployed_at_a_time(project: Path) -> None:
    _deployed(project)
    before = (project / DEPLOYMENT_FILE).read_text(encoding="utf-8")

    with pytest.raises(DeployRefused, match="already deployed"):
        deploy(project, _strategy(project), "ma_crossover", READY, THURSDAY)

    assert (project / DEPLOYMENT_FILE).read_text(encoding="utf-8") == before


@pytest.mark.parametrize("text", ["not json", "[]", '{"schema_version": 2}'])
def test_a_damaged_deployment_is_never_read_as_nothing_deployed(project: Path, text: str) -> None:
    (project / DEPLOYMENT_FILE).write_text(text, encoding="utf-8")

    reported = read_deployment(project)

    assert reported is not None
    assert reported["available"] is False
    with pytest.raises(DeployRefused):
        deploy(project, _confirmed(project), "ma_crossover", READY, THURSDAY)
    assert check_refusal(project, "ma_crossover", READY) is not None


def test_an_edit_after_deploying_is_reported_and_stops_the_check(project: Path) -> None:
    _deployed(project)
    path = project / "configs" / "ma_crossover.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("max: 30", "max: 25"), encoding="utf-8"
    )

    deployed = read_deployment(project)

    assert deployed is not None
    assert deployed["current"] is False
    assert "changed since it was deployed" in deployed["problem"]
    assert "changed since it was deployed" in (check_refusal(project, "ma_crossover", READY) or "")


def test_pausing_and_resuming(project: Path) -> None:
    _deployed(project)

    paused = set_paused(project, True, THURSDAY)
    assert (paused["paused"], paused["paused_at"]) == (True, THURSDAY.isoformat())
    assert check_refusal(project, "ma_crossover", READY) == "the deployed strategy is paused"

    resumed = set_paused(project, False, THURSDAY)
    assert (resumed["paused"], resumed["paused_at"]) == (False, None)
    assert check_refusal(project, "ma_crossover", READY) is None


def test_undeploying_removes_the_record_and_allows_another_deployment(project: Path) -> None:
    _deployed(project)

    undeploy(project)

    assert read_deployment(project) is None
    assert deploy(project, _strategy(project), "ma_crossover", READY, THURSDAY)["available"]


def test_a_damaged_record_can_be_undeployed(project: Path) -> None:
    (project / DEPLOYMENT_FILE).write_text("not json", encoding="utf-8")

    undeploy(project)

    assert read_deployment(project) is None


@pytest.mark.parametrize("change", [lambda root: set_paused(root, True, THURSDAY), undeploy])
def test_nothing_to_pause_or_undeploy_is_a_refusal(project: Path, change: Any) -> None:
    with pytest.raises(DeployRefused, match="nothing is deployed"):
        change(project)


# --------------------------------------------------------------------------
# Stop all.
# --------------------------------------------------------------------------


def test_stop_all_stops_the_check_and_touches_no_risk_file(project: Path) -> None:
    _deployed(project)
    limits_before = (project / "configs" / "risk_limits.yaml").read_bytes()

    assert set_stop_all(project, True, THURSDAY)["stopped"] is True
    assert check_refusal(project, "ma_crossover", READY) == "all trading from the app is stopped"
    assert set_stop_all(project, False, THURSDAY) == {
        "stopped": False,
        "stopped_at": None,
        "damaged": False,
    }
    assert check_refusal(project, "ma_crossover", READY) is None
    assert (project / "configs" / "risk_limits.yaml").read_bytes() == limits_before


def test_with_no_switch_on_record_nothing_is_stopped(project: Path) -> None:
    assert stop_all(project) == {"stopped": False, "stopped_at": None, "damaged": False}


@pytest.mark.parametrize("text", ["not json", '{"schema_version": 9, "stopped": false}', "[]"])
def test_a_damaged_switch_reads_as_stopped(project: Path, text: str) -> None:
    (project / STOP_ALL_FILE).write_text(text, encoding="utf-8")

    assert stop_all(project) == {"stopped": True, "stopped_at": None, "damaged": True}


@pytest.mark.parametrize("value", [None, "no", 0, True])
def test_only_a_switch_that_says_false_reads_as_not_stopped(project: Path, value: Any) -> None:
    (project / STOP_ALL_FILE).write_text(
        json.dumps({"schema_version": 1, "stopped": value}), encoding="utf-8"
    )

    assert stop_all(project)["stopped"] is True


# --------------------------------------------------------------------------
# The daily check.
# --------------------------------------------------------------------------


def _record(project: Path, kind: str = DAILY_CHECK) -> dict[str, Any]:
    strategy = _strategy(project)
    return {
        "id": f"20261112T180000Z-{kind}",
        "kind": kind,
        "strategy_id": "ma_crossover",
        "config_hash": strategy.config_hash,
    }


def test_the_daily_check_runs_the_deployed_search_and_nothing_else(project: Path) -> None:
    _deployed(project)

    assert command_for(_record(project), project) == [
        sys.executable,
        "-P",
        "-m",
        "strategylab.cli",
        "run",
        f"state/{ARTIFACT}",
    ]


def test_a_daily_check_for_nothing_deployed_builds_no_command(project: Path) -> None:
    _confirmed(project)

    with pytest.raises(JobRefused, match="nothing is deployed"):
        command_for(_record(project), project)


@pytest.mark.parametrize("artifact", ["../protocol.yaml", "search-x.json", "graveyard.jsonl", ""])
def test_a_deployment_naming_anything_but_a_search_file_builds_no_command(
    project: Path, artifact: str
) -> None:
    _deployed(project)
    record = json.loads((project / DEPLOYMENT_FILE).read_text(encoding="utf-8"))
    (project / DEPLOYMENT_FILE).write_text(
        json.dumps({**record, "artifact": artifact}), encoding="utf-8"
    )

    with pytest.raises(JobRefused, match="file name is not one this app reads"):
        command_for(_record(project), project)


def test_a_deployed_search_that_has_gone_builds_no_command(project: Path) -> None:
    _deployed(project)
    (project / "state" / ARTIFACT).unlink()

    with pytest.raises(JobRefused, match="not in state/"):
        command_for(_record(project), project)


def test_only_the_daily_check_sees_the_practice_account() -> None:
    server = dict.fromkeys(
        ["STRATEGYLAB_APP_TOKEN", "T212_API_KEY", "T212_ENVIRONMENT", "PATH"], "present"
    )

    search = job_environment(server)
    check = job_environment(server, venue=True)

    assert sorted(search) == ["PATH", "PYTHONSAFEPATH"]
    assert sorted(check) == ["PATH", "PYTHONSAFEPATH", "T212_API_KEY", "T212_ENVIRONMENT"]


def test_the_daily_check_starts_only_when_it_may(project: Path) -> None:
    _deployed(project)
    spawn = Spawned()
    set_paused(project, True, THURSDAY)

    with pytest.raises(JobRefused, match="paused"):
        start_job(project, _strategy(project), DAILY_CHECK, THURSDAY, spawn=spawn, environ=READY)
    assert spawn.calls == []

    set_paused(project, False, THURSDAY)
    with pytest.raises(JobRefused, match="practice account is not set"):
        start_job(project, _strategy(project), DAILY_CHECK, THURSDAY, spawn=spawn, environ={})
    assert spawn.calls == []

    job = start_job(project, _strategy(project), DAILY_CHECK, THURSDAY, spawn=spawn, environ=READY)
    assert job["kind"] == DAILY_CHECK
    assert len(spawn.calls) == 1


def test_a_daily_check_cannot_be_asked_to_stop(project: Path) -> None:
    _deployed(project)
    job = start_job(
        project, _strategy(project), DAILY_CHECK, THURSDAY, spawn=Spawned(), environ=READY
    )
    _dated(project, THURSDAY)

    with pytest.raises(JobRefused, match="cannot be stopped"):
        request_stop(project, job["id"], THURSDAY)


# --------------------------------------------------------------------------
# The schedule.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 12, 9, 0, tzinfo=UTC), datetime(2026, 11, 12, 17, 30, tzinfo=UTC)),
        (datetime(2026, 11, 12, 17, 30, tzinfo=UTC), datetime(2026, 11, 13, 17, 30, tzinfo=UTC)),
        (datetime(2026, 11, 13, 20, 0, tzinfo=UTC), datetime(2026, 11, 16, 17, 30, tzinfo=UTC)),
        (datetime(2026, 11, 14, 12, 0, tzinfo=UTC), datetime(2026, 11, 16, 17, 30, tzinfo=UTC)),
    ],
)
def test_the_next_check_is_the_next_weekday_after_the_close(
    now: datetime, expected: datetime
) -> None:
    assert next_check(now) == expected
    assert CHECK_AFTER.hour == 17 and CHECK_AFTER.minute == 30


def test_a_check_is_due_on_a_weekday_after_the_close_once(project: Path) -> None:
    _deployed(project)
    spawn = Spawned()

    assert due(project, THURSDAY.replace(hour=17, minute=29), READY) == (
        None,
        "not yet time for today's check",
    )
    assert due(project, THURSDAY, READY) == ("ma_crossover", "due")
    assert start_if_due(project, THURSDAY, READY, spawn=spawn) is not None
    assert (
        due(project, THURSDAY + timedelta(minutes=5), READY)[1]
        == "today's check has already been started"
    )
    assert start_if_due(project, THURSDAY + timedelta(minutes=5), READY, spawn=spawn) is None
    assert len(spawn.calls) == 1
    assert checked_days(project) == {date(2026, 11, 12)}


def test_no_check_on_a_weekend(project: Path) -> None:
    _deployed(project)
    saturday = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)

    assert due(project, saturday, READY) == (None, "no check on a weekend")


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda root: None, "nothing is deployed"),
        (lambda root: (_deployed(root), set_paused(root, True, THURSDAY)), "paused"),
        (lambda root: (_deployed(root), set_stop_all(root, True, THURSDAY)), "stopped"),
    ],
)
def test_no_check_is_due_when_it_may_not_run(project: Path, setup: Any, reason: str) -> None:
    setup(project)
    spawn = Spawned()

    strategy_id, why = due(project, THURSDAY, READY)

    assert strategy_id is None
    assert reason in why
    assert start_if_due(project, THURSDAY, READY, spawn=spawn) is None
    assert spawn.calls == []


def test_a_check_is_not_due_while_another_job_runs_and_starts_after(project: Path) -> None:
    _deployed(project)
    spawn = Spawned()
    start_job(project, _strategy(project), "search", THURSDAY - timedelta(minutes=1), spawn=spawn)
    _dated(project, THURSDAY - timedelta(minutes=1))

    assert start_if_due(project, THURSDAY, READY, spawn=spawn) is None
    assert [record["kind"] for record in list_job_records(project)] == ["search"]


def test_missed_days_are_weekdays_after_deploying_with_no_check() -> None:
    deployed_at = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # a Monday, before its check
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # the next Monday, before its check

    missed = missed_days(deployed_at, now, {date(2026, 11, 10), date(2026, 11, 12)})

    assert missed == [date(2026, 11, 9), date(2026, 11, 11), date(2026, 11, 13)]


def test_a_day_deployed_after_its_check_time_is_not_missed() -> None:
    deployed_at = datetime(2026, 11, 9, 19, 0, tzinfo=UTC)

    assert missed_days(deployed_at, datetime(2026, 11, 9, 23, 0, tzinfo=UTC), set()) == []


def test_the_schedule_report_says_why_nothing_runs(project: Path) -> None:
    assert schedule_report(project, THURSDAY, READY)["why_not"] == "nothing is deployed"
    _deployed(project, when=datetime(2026, 11, 10, 12, 0, tzinfo=UTC))

    report = schedule_report(project, THURSDAY, {})

    assert "practice account is not set" in report["why_not"]
    assert report["next_check"] is None
    assert report["missed"] == ["2026-11-10", "2026-11-11", "2026-11-12"]

    ready = schedule_report(project, THURSDAY, READY)
    assert ready["why_not"] is None
    assert ready["next_check"] == "2026-11-13T17:30:00+00:00"


# --------------------------------------------------------------------------
# The live figures.
# --------------------------------------------------------------------------


def _check(project: Path, day: int, worth: float, outcome: str = "none", **changes: Any) -> None:
    line = {
        "schema_version": 1,
        "recorded_at": f"2026-11-{day:02d}T17:45:00+00:00",
        "bar_date": f"2026-11-{day:02d}",
        "strategy": "ma_crossover",
        "params": {"fast": 10},
        "currency": "GBP",
        "equity": worth,
        "cash": 10.0,
        "ticker": "VUAG.L",
        "position_quantity": 0.0,
        "order_side": "buy" if outcome != "none" else None,
        "order_quantity": 1.0 if outcome != "none" else None,
        "order_outcome": outcome,
        "venue_order_id": "1" if outcome == "sent" else None,
        **changes,
    }
    with (project / "state" / "account-live.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


def _live(project: Path) -> dict[str, Any]:
    deployed = read_deployment(project)
    assert deployed is not None
    return read_live(project, deployed, "ma_crossover", THURSDAY)


def test_return_and_largest_fall_are_worked_out_from_the_equity_reported(project: Path) -> None:
    _deployed(project, when=datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    # 10,000 -> 11,000 -> 9,900 -> 10,500: up 5%, and a fall of 10% from 11,000.
    for day, equity in [(2, 10000.0), (3, 11000.0), (4, 9900.0), (5, 10500.0)]:
        _check(project, day, equity)

    figures = _live(project)["figures"]

    assert figures["return_pct"] == pytest.approx(5.0)
    assert figures["max_drawdown_pct"] == pytest.approx(10.0)
    assert figures["checks"] == 4


def test_a_sharpe_needs_enough_checks_and_is_the_backtests_own(project: Path) -> None:
    _deployed(project, when=datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
    equities = [
        10000.0 * (1 + 0.001 * ((index % 3) - 0.5)) ** index
        for index in range(MIN_CHECKS_FOR_SHARPE)
    ]
    for index, equity in enumerate(equities[:-1]):
        _check(project, 1, equity, recorded_at=f"2026-10-{index + 2:02d}T17:45:00+00:00")

    short = _live(project)["figures"]
    assert short["sharpe"] is None
    assert (
        short["why_missing"]["sharpe"]
        == f"needs {MIN_CHECKS_FOR_SHARPE} checks; {MIN_CHECKS_FOR_SHARPE - 1} so far"
    )

    _check(project, 1, equities[-1], recorded_at="2026-10-31T17:45:00+00:00")
    import pandas as pd  # noqa: PLC0415

    series = pd.Series(equities)
    full = _live(project)["figures"]
    assert full["sharpe"] == pytest.approx(sharpe(series.pct_change().dropna()))
    assert full["max_drawdown_pct"] == pytest.approx(max_drawdown(series))


def test_one_check_shows_no_return_and_says_why(project: Path) -> None:
    _deployed(project, when=datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    _check(project, 2, 10000.0)

    figures = _live(project)["figures"]

    assert figures["return_pct"] is None
    assert figures["why_missing"]["return_pct"] == "needs two checks"


def test_only_this_deployments_checks_count(project: Path) -> None:
    _deployed(project, when=datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    _check(project, 3, 1.0)
    _check(project, 4, 100.0, strategy="another")
    _check(project, 5, 200.0)
    _check(project, 6, 300.0, schema_version=2)
    _check(project, 9, 210.0)

    live = _live(project)

    assert [check["equity"] for check in live["checks"]] == [200.0, 210.0]
    assert live["figures"]["return_pct"] == pytest.approx(5.0)


def test_orders_sent_and_refused_are_counted(project: Path) -> None:
    _deployed(project, when=datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    for day, outcome in [(2, "sent"), (3, "none"), (4, "failed"), (5, "sent")]:
        _check(project, day, 10000.0, outcome)

    figures = _live(project)["figures"]

    assert (figures["sent"], figures["failed"]) == (2, 1)


def test_lines_that_are_not_checks_are_passed_over(project: Path) -> None:
    _deployed(project, when=datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    with (project / "state" / "account-live.jsonl").open("ab") as handle:
        handle.write(b"not json\n\xff\xfe\n[1]\n")
    _check(project, 2, 10000.0, recorded_at="no time")
    _check(project, 3, 10000.0, equity="lots")
    _check(project, 4, 10000.0, recorded_at="2026-11-04T17:45:00")
    _check(project, 5, 10000.0)

    assert len(_live(project)["checks"]) == 1


def test_decisions_since_deploying_are_listed_newest_first(project: Path) -> None:
    _deployed(project, when=datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    lines = [
        {
            "source": "live",
            "recorded_at": "2026-11-03T17:45:00+00:00",
            "bar_timestamp": "2026-11-03T00:00:00",
            "action": "hold",
        },
        {
            "source": "live",
            "recorded_at": "2026-11-04T17:45:00+00:00",
            "bar_timestamp": "2026-11-04T00:00:00",
            "action": "enter_long",
            "risk_verdict": "allow",
            "risk_reason": "ok",
            "conditions_met": ["a", 3],
            "conditions_failed": [],
        },
        {"source": "backtest", "recorded_at": "2026-11-05T17:45:00+00:00", "action": "hold"},
        {
            "source": "live",
            "recorded_at": "2026-11-05T17:45:00+00:00",
            "bar_timestamp": "2026-11-05T00:00:00",
            "action": "hold",
        },
    ]
    (project / "state" / "decisions-live.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )

    decisions = _live(project)["decisions"]

    assert [(item["bar_date"], item["action"]) for item in decisions] == [
        ("2026-11-05", "hold"),
        ("2026-11-04", "enter_long"),
    ]
    assert decisions[1]["conditions_met"] == ["a"]


@pytest.mark.parametrize(
    ("session", "locked"),
    [
        ({"day": "2026-11-12", "sod": 1.0, "tripped": True}, True),
        ({"day": "2026-11-12", "sod": 1.0, "tripped": False}, False),
        ({"day": "2026-11-11", "sod": 1.0, "tripped": True}, False),
        (None, None),
    ],
)
def test_the_loss_lock_is_todays_only(project: Path, session: Any, locked: bool | None) -> None:
    _deployed(project)
    if session is not None:
        (project / "state" / "session.json").write_text(json.dumps(session), encoding="utf-8")

    assert _live(project)["loss_lock"] is locked


# --------------------------------------------------------------------------
# The routes.
# --------------------------------------------------------------------------


@pytest.fixture
def client(project: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("strategylab.api.jobs._spawn_detached", lambda *args, **kwargs: None)
    return TestClient(create_app(project, TOKEN, READY), base_url="http://127.0.0.1")


def test_deploying_pausing_and_undeploying_over_the_routes(
    project: Path, client: TestClient
) -> None:
    _confirmed(project)

    wrong = client.post("/api/strategies/ma_crossover/deploy", json={"typed": "no"}, headers=AUTH)
    right = client.post(
        "/api/strategies/ma_crossover/deploy", json={"typed": "ma_crossover"}, headers=AUTH
    )
    paused = client.post("/api/deployment/pause", headers=AUTH)
    status = client.get("/api/deployment", headers=AUTH).json()
    resumed = client.post("/api/deployment/resume", headers=AUTH)
    gone = client.post("/api/deployment/undeploy", headers=AUTH)
    nothing = client.post("/api/deployment/pause", headers=AUTH)
    unknown = client.post("/api/deployment/sell_everything", headers=AUTH)

    assert wrong.status_code == 409
    assert right.status_code == 200 and right.json()["proven"] is False
    assert paused.json()["paused"] is True
    assert status["deployment"]["paused"] is True
    assert status["live"]["available"] is True
    assert status["schedule"]["why_not"] == "the deployed strategy is paused"
    assert status["venue_unready"] is None
    assert resumed.json()["paused"] is False
    assert gone.json() == {"deployment": None}
    assert nothing.status_code == 409
    assert unknown.status_code == 404


def test_the_daily_check_can_be_started_over_the_route_only_when_deployed(
    project: Path, client: TestClient
) -> None:
    _confirmed(project)
    refused = client.post("/api/strategies/ma_crossover/jobs/daily_check", headers=AUTH)
    client.post("/api/strategies/ma_crossover/deploy", json={"typed": "ma_crossover"}, headers=AUTH)
    started = client.post("/api/strategies/ma_crossover/jobs/daily_check", headers=AUTH)

    assert refused.status_code == 409
    assert "nothing is deployed" in refused.json()["detail"]
    assert started.status_code == 201


def test_stop_all_over_the_route(project: Path, client: TestClient) -> None:
    on = client.post("/api/stop-all", json={"stopped": True}, headers=AUTH)
    off = client.post("/api/stop-all", json={"stopped": False}, headers=AUTH)
    bad = client.post("/api/stop-all", json={"stopped": "please"}, headers=AUTH)

    assert on.json()["stopped"] is True
    assert off.json()["stopped"] is False
    assert bad.status_code == 422


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("post", "/api/strategies/ma_crossover/deploy"),
        ("post", "/api/deployment/undeploy"),
        ("post", "/api/stop-all"),
        ("get", "/api/deployment"),
    ],
)
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {**AUTH, "Origin": "https://evil.example"}]
)
def test_nothing_about_deploying_answers_without_the_token_from_this_machine(
    project: Path, client: TestClient, method: str, route: str, headers: dict[str, str]
) -> None:
    _confirmed(project)

    body = {"typed": "ma_crossover", "stopped": True} if method == "post" else None
    answer = client.request(method.upper(), route, json=body, headers=headers)

    assert answer.status_code in (401, 403)
    assert not (project / DEPLOYMENT_FILE).exists()
    assert not (project / STOP_ALL_FILE).exists()


def test_the_deployment_status_never_holds_the_key(project: Path, client: TestClient) -> None:
    _confirmed(project)
    client.post("/api/strategies/ma_crossover/deploy", json={"typed": "ma_crossover"}, headers=AUTH)

    text = client.get("/api/deployment", headers=AUTH).text

    assert "present" not in text
    assert "T212_API_KEY" not in text
    assert not list((project / JOBS_DIR).glob("*")) if (project / JOBS_DIR).exists() else True


def test_a_check_for_a_strategy_that_is_not_the_deployed_one_may_not_run(project: Path) -> None:
    other = (project / "configs" / "ma_crossover.yaml").read_text(encoding="utf-8")
    (project / "configs" / "other.yaml").write_text(
        other.replace("name: ma_crossover", "name: other"), encoding="utf-8"
    )
    _deployed(project)

    assert check_refusal(project, "other", READY) == "that strategy is not the one deployed"
    assert check_refusal(project, "ma_crossover", READY) is None


# --------------------------------------------------------------------------
# The scheduler's thread.
# --------------------------------------------------------------------------


def test_the_scheduler_asks_each_period_and_stops_when_told(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    from strategylab.api import server  # noqa: PLC0415

    asked: list[datetime] = []
    stopping = threading.Event()

    def counting(root: Path, now: datetime, environ: Any) -> None:
        asked.append(now)
        if len(asked) == 3:
            stopping.set()

    monkeypatch.setattr(server, "SCHEDULE_EVERY_SECONDS", 0.001)
    monkeypatch.setattr(server, "start_if_due", counting)

    server._check_each_minute(project, READY, stopping)

    assert len(asked) == 3
    assert all(moment.tzinfo is not None for moment in asked)


def test_a_file_error_does_not_end_the_scheduler(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    from strategylab.api import server  # noqa: PLC0415

    calls: list[int] = []
    stopping = threading.Event()

    def failing(root: Path, now: datetime, environ: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            stopping.set()
        raise OSError("held by something else")

    monkeypatch.setattr(server, "SCHEDULE_EVERY_SECONDS", 0.001)
    monkeypatch.setattr(server, "start_if_due", failing)

    server._check_each_minute(project, READY, stopping)

    assert len(calls) == 2


def test_no_error_of_any_kind_ends_the_scheduler(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading  # noqa: PLC0415

    from strategylab.api import server  # noqa: PLC0415

    calls: list[int] = []
    stopping = threading.Event()

    def failing(root: Path, now: datetime, environ: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            stopping.set()
        raise ValueError("a damaged record")

    monkeypatch.setattr(server, "SCHEDULE_EVERY_SECONDS", 0.001)
    monkeypatch.setattr(server, "start_if_due", failing)

    server._check_each_minute(project, READY, stopping)

    assert len(calls) == 2


def test_the_scheduler_stops_with_the_server(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io  # noqa: PLC0415
    import threading  # noqa: PLC0415

    from strategylab.api import server  # noqa: PLC0415

    seen: list[threading.Event] = []
    monkeypatch.setattr(
        server, "_check_each_minute", lambda root, environ, stopping: seen.append(stopping)
    )

    server.serve(
        project,
        {server.TOKEN_VARIABLE: TOKEN},
        lifeline=io.StringIO(""),
        announce=io.StringIO(),
    )

    (stopping,) = seen
    assert stopping.is_set()


# --------------------------------------------------------------------------
# What a review of this code found, each pinned.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        lambda root: set_stop_all(root, True, THURSDAY),
        lambda root: set_paused(root, True, THURSDAY),
    ],
)
def test_stop_all_or_pause_pressed_after_a_check_started_still_stops_it(
    project: Path, change: Any
) -> None:
    _deployed(project)
    start_job(project, _strategy(project), DAILY_CHECK, THURSDAY, spawn=Spawned(), environ=READY)

    change(project)

    with pytest.raises(JobRefused, match="stopped|paused"):
        command_for(_record(project), project)


def test_the_runner_refuses_a_check_without_the_practice_account_in_its_own_environment(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deployed(project)
    monkeypatch.setenv("T212_ENVIRONMENT", "live")

    with pytest.raises(JobRefused, match="must be demo"):
        command_for(_record(project), project)


@pytest.mark.parametrize("value", ["true", "yes", 1, None, [False]])
def test_a_paused_value_that_is_not_plain_false_reads_as_paused(project: Path, value: Any) -> None:
    _deployed(project)
    record = json.loads((project / DEPLOYMENT_FILE).read_text(encoding="utf-8"))
    (project / DEPLOYMENT_FILE).write_text(
        json.dumps({**record, "paused": value}), encoding="utf-8"
    )

    assert read_deployment(project)["paused"] is True  # type: ignore[index]
    assert check_refusal(project, "ma_crossover", READY) == "the deployed strategy is paused"


@pytest.mark.parametrize(
    ("now", "reason"),
    [
        (datetime(2026, 11, 12, 14, 0, tzinfo=UTC), "after 17:30 UTC"),
        (datetime(2026, 11, 14, 18, 0, tzinfo=UTC), "weekend"),
    ],
)
def test_check_now_is_refused_before_the_close_and_at_weekends(
    project: Path, now: datetime, reason: str
) -> None:
    _deployed(project)
    spawn = Spawned()

    with pytest.raises(JobRefused, match=reason):
        start_job(project, _strategy(project), DAILY_CHECK, now, spawn=spawn, environ=READY)

    assert spawn.calls == []


def test_check_now_is_refused_once_todays_check_has_started(project: Path) -> None:
    _deployed(project)
    spawn = Spawned()
    start_job(project, _strategy(project), DAILY_CHECK, THURSDAY, spawn=spawn, environ=READY)
    record = list((project / JOBS_DIR).glob("*.json"))[0]
    (record.with_suffix(".done")).write_text('{"exit_code": 0}', encoding="utf-8")

    with pytest.raises(JobRefused, match="already been started"):
        start_job(
            project,
            _strategy(project),
            DAILY_CHECK,
            THURSDAY + timedelta(hours=1),
            spawn=spawn,
            environ=READY,
        )

    assert len(spawn.calls) == 1


def test_a_job_id_that_is_not_a_date_names_no_day_and_breaks_nothing(project: Path) -> None:
    _deployed(project)
    (project / JOBS_DIR).mkdir(parents=True, exist_ok=True)
    bad = "20261399T000000Z-daily_check"
    (project / JOBS_DIR / f"{bad}.json").write_text(
        json.dumps({"schema_version": 1, "id": bad, "kind": "daily_check"}), encoding="utf-8"
    )

    assert checked_days(project) == set()
    assert schedule_report(project, THURSDAY, READY)["why_not"] is None


@pytest.mark.parametrize(
    ("file", "change"),
    [
        ("deployment", {"deployed_at": "0001-01-01T00:00:00+05:00"}),
        ("account", {"recorded_at": "9999-12-31T23:59:59-05:00"}),
    ],
)
def test_a_time_at_the_edge_of_the_calendar_breaks_no_route(
    project: Path, client: TestClient, file: str, change: dict[str, Any]
) -> None:
    _deployed(project)
    if file == "deployment":
        record = json.loads((project / DEPLOYMENT_FILE).read_text(encoding="utf-8"))
        (project / DEPLOYMENT_FILE).write_text(json.dumps({**record, **change}), encoding="utf-8")
    else:
        _check(project, 12, 10000.0, **change)

    answer = client.get("/api/deployment", headers=AUTH)

    assert answer.status_code == 200

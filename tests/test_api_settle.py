"""Tests for settling a disagreement between the strategies' units and the venue's.

No test reaches a venue. The account and fills logs are written by hand, so the
venue's position, the recorded units and the difference are each worked out on
paper. The example throughout is the operator's own: the records say a strategy
holds 10 units, and the venue holds 8 because 2 were sold by hand.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.deployment import DeployRefused, deploy, read_deployment, undeploy
from strategylab.api.drafts import confirm
from strategylab.api.reading import without_root
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.api.settle import SettleRefused, settle, settlement
from strategylab.core.config import config_content_hash
from strategylab.live.ledger import read_corrections, read_ledger
from strategylab.search.loop import SEARCH_SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]
TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}
READY = dict.fromkeys(["T212_API_KEY"], "present") | {"T212_ENVIRONMENT": "demo"}
DEPLOYED = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def _search(root: Path, name: str, strategy: str, config: Path) -> None:
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": "2026-11-10T08:00:00+00:00",
        "strategy": strategy,
        "grid_size": 90,
        "config_content_hash": config_content_hash(config),
        "winner": {"params": {"fast": 10, "slow": 80, "trend": 150}, "sharpe": 0.5},
        "noise_gate": {"status": "failed", "beaten": 25, "required": 95},
    }
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / name).write_text(json.dumps(payload), encoding="utf-8")


def _deploy(root: Path, strategy_id: str) -> StrategyFile:
    found = find_strategy(root, strategy_id)
    assert found is not None
    confirm(root, found, found.config_hash, DEPLOYED - timedelta(hours=1))
    deploy(root, found, found.name, READY, DEPLOYED)
    return found


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with ma_crossover deployed, and a second strategy searched but not deployed."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    text = (tmp_path / "configs" / "ma_crossover.yaml").read_text(encoding="utf-8")
    second = tmp_path / "configs" / "second.yaml"
    second.write_text(text.replace("name: ma_crossover", "name: second"), encoding="utf-8")
    _search(
        tmp_path,
        "search-20261110T080000Z.json",
        "ma_crossover",
        tmp_path / "configs" / "ma_crossover.yaml",
    )
    _search(tmp_path, "search-20261110T080001Z.json", "second", second)
    _deploy(tmp_path, "ma_crossover")
    return tmp_path


def _append(path: Path, line: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


def _check(
    root: Path,
    day: int,
    venue_units: float,
    outcome: str = "none",
    order_id: str | None = None,
    strategy_id: str = "ma_crossover",
) -> None:
    _append(
        root / "state" / "account-live.jsonl",
        {
            "schema_version": 1,
            "recorded_at": f"2026-11-{day:02d}T17:45:00+00:00",
            "strategy": strategy_id,
            "strategy_id": strategy_id,
            "ticker": "VUAG.L",
            "position_quantity": venue_units,
            "order_outcome": outcome,
            "venue_order_id": order_id,
        },
    )


def _fill(root: Path, day: int, order_id: str, units: float, side: str = "buy") -> None:
    _append(
        root / "state" / "fills-live.jsonl",
        {
            "schema_version": 1,
            "recorded_at": f"2026-11-{day:02d}T17:40:00+00:00",
            "venue_order_id": order_id,
            "ticker": "VUAG.L",
            "side": side,
            "quantity": units,
            "price": 100.0,
            "currency": "GBP",
            "filled_at": f"2026-11-{day:02d}T08:00:00+00:00",
            "charges": [],
        },
    )


def _ten_bought_eight_held(root: Path) -> StrategyFile:
    """ma_crossover bought 10; the next check found the venue holding 8."""
    _check(root, 10, 0.0, "sent", "1")
    _fill(root, 11, "1", 10.0)
    _check(root, 11, 8.0)
    found = find_strategy(root, "ma_crossover")
    assert found is not None
    return found


def _held(root: Path, strategy_id: str) -> float:
    state = root / "state"
    ledger = read_ledger(
        state / "account-live.jsonl", state / "fills-live.jsonl", state / "corrections-live.jsonl"
    )
    return ledger.held(strategy_id)


def test_nothing_to_settle_before_any_check_or_when_they_agree(project: Path) -> None:
    assert settlement(project, NOW) == {"state": "agree"}

    _check(project, 10, 0.0, "sent", "1")
    _fill(project, 11, "1", 10.0)
    _check(project, 11, 10.0)

    assert settlement(project, NOW) == {"state": "agree"}


def test_a_disagreement_reports_both_figures_and_the_difference(project: Path) -> None:
    _ten_bought_eight_held(project)

    report = settlement(project, NOW)

    assert report["state"] == "settle"
    # The venue's 8 less the recorded 10: a correction of -2.
    assert (report["venue_units"], report["recorded_units"], report["difference"]) == (
        8.0,
        10.0,
        -2.0,
    )
    assert report["snapshot_at"] == "2026-11-11T17:45:00+00:00"
    assert "8" in report["why"] and "10" in report["why"]


def test_settling_gives_the_difference_to_the_strategy_named(project: Path) -> None:
    strategy = _ten_bought_eight_held(project)

    after = settle(project, strategy, "ma_crossover", -2.0, NOW)

    assert after == {"state": "agree"}
    assert _held(project, "ma_crossover") == 8.0
    [correction] = read_corrections(project / "state" / "corrections-live.jsonl")
    assert (correction.strategy_id, correction.units) == ("ma_crossover", -2.0)
    assert (correction.venue_units, correction.recorded_units) == (8.0, 10.0)
    assert correction.snapshot_at == "2026-11-11T17:45:00+00:00"
    assert correction.recorded_at == NOW.isoformat()


def test_a_second_press_finds_nothing_to_settle(project: Path) -> None:
    strategy = _ten_bought_eight_held(project)
    settle(project, strategy, "ma_crossover", -2.0, NOW)

    with pytest.raises(SettleRefused, match="nothing to settle"):
        settle(project, strategy, "ma_crossover", -2.0, NOW)
    assert len(read_corrections(project / "state" / "corrections-live.jsonl")) == 1


@pytest.mark.parametrize(
    ("typed", "shown", "why"),
    [
        ("ma crossover", -2.0, "type the strategy's name, ma_crossover"),
        ("ma_crossover", -3.0, "has changed since it was shown"),
        ("ma_crossover", float("nan"), "has changed since it was shown"),
    ],
)
def test_a_wrong_name_or_another_difference_is_refused(
    project: Path, typed: str, shown: float, why: str
) -> None:
    strategy = _ten_bought_eight_held(project)

    with pytest.raises(SettleRefused, match=why):
        settle(project, strategy, typed, shown, NOW)
    assert not (project / "state" / "corrections-live.jsonl").exists()


def test_a_strategy_that_is_not_deployed_cannot_be_given_it(project: Path) -> None:
    _ten_bought_eight_held(project)
    second = find_strategy(project, "second")
    assert second is not None

    with pytest.raises(SettleRefused, match="only a deployed strategy"):
        settle(project, second, "second", -2.0, NOW)


def test_a_strategy_left_below_zero_is_refused(project: Path) -> None:
    _ten_bought_eight_held(project)
    second = _deploy(project, "second")

    with pytest.raises(SettleRefused, match="holds 0 units, and giving it -2"):
        settle(project, second, "second", -2.0, NOW)


def test_units_bought_by_hand_can_be_given_to_a_strategy(project: Path) -> None:
    # The venue holds 3 more than the records: a correction of +3.
    _check(project, 10, 0.0, "sent", "1")
    _fill(project, 11, "1", 10.0)
    _check(project, 11, 13.0)
    second = _deploy(project, "second")

    settle(project, second, "second", 3.0, NOW)

    assert (_held(project, "second"), _held(project, "ma_crossover")) == (3.0, 10.0)


def test_a_fill_recorded_after_the_newest_check_means_run_a_check_first(project: Path) -> None:
    strategy = _ten_bought_eight_held(project)
    _check(project, 12, 8.0, "sent", "2")
    _fill(project, 13, "2", 1.0, side="sell")

    report = settlement(project, NOW)

    assert report["state"] == "wait"
    assert report["why"] == "something was recorded after the newest check; run a check first"
    with pytest.raises(SettleRefused, match="run a check first"):
        settle(project, strategy, "ma_crossover", report["difference"], NOW)


def test_an_order_waiting_for_its_fill_means_wait(project: Path) -> None:
    _ten_bought_eight_held(project)
    _check(project, 12, 8.0, "sent", "2")

    report = settlement(project, NOW)

    assert report["state"] == "wait"
    assert report["why"].startswith("an order has no recorded fill yet")


def test_a_strategy_below_zero_is_settled_in_the_venues_app(project: Path) -> None:
    _check(project, 10, 0.0, "sent", "1")
    _fill(project, 11, "1", 2.0, side="sell")
    _check(project, 11, 0.0)

    report = settlement(project, NOW)

    assert report["state"] == "wait"
    assert "fewer than zero" in report["why"] and "venue's own app" in report["why"]


def test_a_running_job_means_wait(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ten_bought_eight_held(project)
    monkeypatch.setattr(
        "strategylab.api.settle.list_jobs", lambda root, now: {"current": {"id": "x"}}
    )

    assert settlement(project, NOW)["why"] == "a job is running; settle once it has ended"


def test_a_damaged_correction_line_is_reported_without_the_full_path(project: Path) -> None:
    _ten_bought_eight_held(project)
    _append(project / "state" / "corrections-live.jsonl", {"schema_version": 1})

    report = settlement(project, NOW)

    assert report == {
        "state": "unreadable",
        "why": "the logs could not be read: state/corrections-live.jsonl holds a line that is not "
        "a correction",
    }


def test_the_newest_check_is_the_one_recorded_last(project: Path) -> None:
    _ten_bought_eight_held(project)
    # Another strategy's check, later, found the venue holding 7.
    _check(project, 12, 7.0, strategy_id="second")

    assert settlement(project, NOW)["venue_units"] == 7.0


def test_settling_over_the_route(project: Path) -> None:
    _ten_bought_eight_held(project)
    client = TestClient(create_app(project, TOKEN, READY), base_url="http://127.0.0.1")

    status = client.get("/api/deployment?strategy_id=ma_crossover", headers=AUTH).json()
    first = client.post(
        "/api/strategies/ma_crossover/settle",
        json={"typed": "ma_crossover", "units": -2.0},
        headers=AUTH,
    )
    second = client.post(
        "/api/strategies/ma_crossover/settle",
        json={"typed": "ma_crossover", "units": -2.0},
        headers=AUTH,
    )
    unknown = client.post(
        "/api/strategies/nothing/settle", json={"typed": "x", "units": 0}, headers=AUTH
    )

    assert status["settlement"]["state"] == "settle"
    assert status["settlement"]["difference"] == -2.0
    assert (first.status_code, first.json()) == (200, {"state": "agree"})
    assert second.status_code == 409
    assert "nothing to settle" in second.json()["detail"]
    assert unknown.status_code == 404


def test_a_correction_recorded_after_the_newest_check_means_run_a_check_first(
    project: Path,
) -> None:
    # A correction of -1, recorded after the check, leaves 9 against the venue's
    # 8: the check's figure no longer says what the ledger now counts.
    _ten_bought_eight_held(project)
    _append(
        project / "state" / "corrections-live.jsonl",
        {
            "schema_version": 1,
            "recorded_at": "2026-11-12T09:00:00+00:00",
            "strategy_id": "ma_crossover",
            "units": -1.0,
            "venue_units": 8.0,
            "recorded_units": 10.0,
            "snapshot_at": "2026-11-11T17:45:00+00:00",
        },
    )

    report = settlement(project, NOW)

    assert (report["state"], report["recorded_units"]) == ("wait", 9.0)
    assert report["why"] == "something was recorded after the newest check; run a check first"


def test_a_strategy_whose_units_were_all_corrected_away_can_be_undeployed(project: Path) -> None:
    # All 10 were sold by hand: the venue holds none, and a correction of -10
    # leaves the strategy holding nothing, so it may be undeployed.
    strategy = _ten_bought_eight_held(project)
    _check(project, 12, 0.0)
    with pytest.raises(DeployRefused, match="holds 10 units"):
        undeploy(project, "ma_crossover")

    settle(project, strategy, "ma_crossover", -10.0, NOW)
    undeploy(project, "ma_crossover")

    assert read_deployment(project, "ma_crossover") is None


def test_the_project_root_is_taken_out_of_a_message_in_either_slash_style(tmp_path: Path) -> None:
    text = f"{tmp_path}{os.sep}state{os.sep}a.jsonl, then {tmp_path.as_posix()}/state/b.jsonl"

    said = without_root(text, tmp_path)

    assert said == f"state{os.sep}a.jsonl, then state/b.jsonl"
    assert tmp_path.name not in said


def test_undeploying_over_a_damaged_correction_says_why_without_the_full_path(
    project: Path,
) -> None:
    _append(project / "state" / "corrections-live.jsonl", {"schema_version": 1})

    with pytest.raises(DeployRefused) as refused:
        undeploy(project, "ma_crossover")

    assert str(refused.value) == (
        "what it holds cannot be read: state/corrections-live.jsonl holds a line that is not "
        "a correction"
    )

"""Tests for what the app's first screen is told.

`read_overview` only reads, so every test builds a small project in a temporary
directory, calls it, and checks what came back against what was put there. No
test changes the working directory: the function is handed a root and must not
depend on where it was called from.

Nothing here runs a search. A search artifact is written by hand with the few
fields the overview reads, so that each value asserted is one the test chose.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from strategylab.api.overview import read_overview
from strategylab.core.config import config_content_hash
from strategylab.data.source import cache_write
from strategylab.protocol import ProtocolError
from strategylab.risk.limits import load_limits
from strategylab.search.loop import SEARCH_SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE_BARS = REPOSITORY / "tests" / "fixtures" / "SPY.parquet"

#: A value no test asserts on directly, so that finding it anywhere in a result
#: is unambiguous.
PLANTED = "k3y-that-must-never-appear"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding the committed protocol, strategy config and risk limits."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    return tmp_path


def _cache(root: Path) -> None:
    """Write the recorded price fixture into the project's cache."""
    cache_write(
        pd.read_parquet(FIXTURE_BARS),
        "SPY",
        root / "data",
        source="fixture",
        fetched_at=datetime(2026, 8, 21, 13, 13, tzinfo=UTC),
    )


def _artifact(root: Path, stamp: str, **changes: Any) -> Path:
    """Write a search artifact holding only the fields the overview reads."""
    payload: dict[str, Any] = {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "strategy": "ma_crossover",
        "generated_at": f"2026-01-{stamp}T00:00:00+00:00",
        "grid_size": 90,
        "config_content_hash": config_content_hash(root / "configs" / "ma_crossover.yaml"),
        "winner": {"params": {"fast": 10, "slow": 80}, "sharpe": 0.5, "max_drawdown_pct": 12.5},
        "noise_gate": {
            "status": "failed",
            "beaten": 25,
            "required": 95,
            "scoreable_trials": 100,
            "summary": "written by a test",
        },
    }
    payload.update(changes)
    (root / "state").mkdir(exist_ok=True)
    path = root / "state" / f"search-202601{stamp}T000000Z.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The protocol's own facts.
# --------------------------------------------------------------------------


def test_the_protocol_version_and_the_read_counter_come_from_the_protocol(project: Path) -> None:
    overview = read_overview(project, {})

    assert overview["protocol_version"] == 1
    assert overview["held_back_reads"] == 0


def test_the_read_counter_is_reported_as_recorded_not_assumed(project: Path) -> None:
    path = project / "protocol.yaml"
    text = path.read_text(encoding="utf-8")
    assert "walk_forward_touches: 0" in text
    path.write_text(text.replace("walk_forward_touches: 0", "walk_forward_touches: 3"), "utf-8")

    assert read_overview(project, {})["held_back_reads"] == 3


def test_a_project_without_a_protocol_is_an_error_not_an_empty_screen(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError):
        read_overview(tmp_path, {})


# --------------------------------------------------------------------------
# Prices.
# --------------------------------------------------------------------------


def test_prices_that_were_never_fetched_are_missing_with_a_reason(project: Path) -> None:
    prices = read_overview(project, {})["prices"]

    assert prices == {"available": False, "reason": "No price data for SPY has been fetched."}


def test_cached_prices_are_described_from_their_provenance(project: Path) -> None:
    _cache(project)
    bars = pd.read_parquet(FIXTURE_BARS)

    prices = read_overview(project, {})["prices"]

    assert prices["available"] is True
    assert prices["instrument"] == "SPY"
    assert prices["bars"] == len(bars) == 253
    assert prices["first_bar"] == bars.index[0].date().isoformat()
    assert prices["last_bar"] == bars.index[-1].date().isoformat()
    assert len(prices["content_hash"]) == 64


def test_a_cache_altered_after_it_was_written_is_missing_not_trusted(project: Path) -> None:
    _cache(project)
    bars = pd.read_parquet(FIXTURE_BARS)
    bars.iloc[0, 0] += 1.0
    bars.to_parquet(project / "data" / "SPY.parquet")

    prices = read_overview(project, {})["prices"]

    assert prices["available"] is False
    assert "cannot be used" in prices["reason"]


# --------------------------------------------------------------------------
# The kill switch.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("flag", [True, False])
def test_the_kill_switch_is_read_from_the_limits_file(project: Path, flag: bool) -> None:
    path = project / "configs" / "risk_limits.yaml"
    text = path.read_text(encoding="utf-8")
    assert "kill_switch: false" in text
    path.write_text(
        text.replace("kill_switch: false", f"kill_switch: {str(flag).lower()}"), "utf-8"
    )

    assert read_overview(project, {})["kill_switch"] is flag


def test_a_limits_file_that_cannot_be_read_is_not_reported_as_off(project: Path) -> None:
    (project / "configs" / "risk_limits.yaml").unlink()

    switch = read_overview(project, {})["kill_switch"]

    assert switch is not False
    assert switch["available"] is False


# --------------------------------------------------------------------------
# The venue, and the credential that must not travel.
# --------------------------------------------------------------------------


def test_the_venue_block_says_a_key_is_present_and_never_what_it_is(project: Path) -> None:
    overview = read_overview(
        project, {"T212_ENVIRONMENT": "demo", "T212_API_KEY": PLANTED, "T212_API_SECRET": PLANTED}
    )

    assert overview["venue"] == {"environment": "demo", "key_present": True}
    assert PLANTED not in json.dumps(overview)


@pytest.mark.parametrize("environ", [{}, {"T212_API_KEY": ""}, {"T212_API_KEY": "   "}])
def test_an_absent_or_blank_key_is_not_a_key(project: Path, environ: dict[str, str]) -> None:
    assert read_overview(project, environ)["venue"] == {"environment": None, "key_present": False}


# --------------------------------------------------------------------------
# Strategies.
# --------------------------------------------------------------------------


def test_the_strategy_config_is_listed_as_a_draft_and_the_limits_file_is_not(project: Path) -> None:
    overview = read_overview(project, {})

    (strategy,) = overview["strategies"]
    assert strategy["id"] == "ma_crossover"
    assert strategy["name"] == "ma_crossover"
    assert strategy["config"] == "configs/ma_crossover.yaml"
    assert strategy["state"] == "draft"
    assert strategy["description"].startswith("Trend following.")
    assert "\n" not in strategy["description"]
    assert strategy["latest_search"] is None
    assert overview["unreadable_configs"] == []


def test_a_config_the_loader_refuses_is_reported_with_its_reason(project: Path) -> None:
    (project / "configs" / "from_a_video.yaml").write_text("name: broken\n", encoding="utf-8")

    overview = read_overview(project, {})

    assert [s["id"] for s in overview["strategies"]] == ["ma_crossover"]
    (refused,) = overview["unreadable_configs"]
    assert refused["config"] == "configs/from_a_video.yaml"
    assert refused["reason"]


def test_the_newest_search_for_the_strategy_is_reported_as_recorded(project: Path) -> None:
    _artifact(project, "01", winner={"params": {"fast": 5}, "sharpe": 0.1, "max_drawdown_pct": 1.0})
    newest = _artifact(project, "02")

    search = read_overview(project, {})["strategies"][0]["latest_search"]

    assert search == {
        "artifact": newest.name,
        "generated_at": "2026-01-02T00:00:00+00:00",
        "cells": 90,
        "candidate": {"fast": 10, "slow": 80},
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


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": SEARCH_SCHEMA_VERSION - 1},
        {"strategy": "another_strategy"},
        {"noise_gate": None},
    ],
    ids=["older-schema", "another-strategy", "no-gate-recorded"],
)
def test_an_artifact_this_code_cannot_vouch_for_is_passed_over(
    project: Path, changes: dict[str, Any]
) -> None:
    older = _artifact(project, "01")
    _artifact(project, "02", **changes)

    search = read_overview(project, {})["strategies"][0]["latest_search"]

    assert search["artifact"] == older.name


def test_a_file_that_is_not_an_artifact_does_not_hide_one_that_is(project: Path) -> None:
    older = _artifact(project, "01")
    (project / "state" / "search-20260103T000000Z.json").write_text("not json", encoding="utf-8")
    (project / "state" / "search-20260104T000000Z.json").write_text("[1, 2]", encoding="utf-8")

    assert read_overview(project, {})["strategies"][0]["latest_search"]["artifact"] == older.name


def test_a_search_with_no_candidate_reports_none_not_a_number(project: Path) -> None:
    _artifact(project, "01", winner=None)

    search = read_overview(project, {})["strategies"][0]["latest_search"]

    assert search["candidate"] is None
    assert search["sharpe"] is None
    assert search["max_drawdown_pct"] is None


def test_a_search_run_on_an_earlier_version_of_the_config_says_so(project: Path) -> None:
    _artifact(project, "01")
    config = project / "configs" / "ma_crossover.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("max: 30,  step: 5", "max: 25,  step: 5"),
        encoding="utf-8",
    )
    assert "max: 25,  step: 5" in config.read_text(encoding="utf-8")

    search = read_overview(project, {})["strategies"][0]["latest_search"]

    assert search["config_changed_since"] is True


# --------------------------------------------------------------------------
# What a hostile or broken file can do, and what leaves in an error message.
# --------------------------------------------------------------------------


def test_no_reason_names_the_directory_the_project_is_kept_in(project: Path) -> None:
    _cache(project)
    bars = pd.read_parquet(FIXTURE_BARS)
    bars.iloc[0, 0] += 1.0
    bars.to_parquet(project / "data" / "SPY.parquet")
    (project / "configs" / "risk_limits.yaml").unlink()
    (project / "configs" / "from_a_video.yaml").write_text("name: broken\n", encoding="utf-8")

    overview = read_overview(project, {})

    # Three reasons were produced, by three different loaders.
    assert overview["prices"]["available"] is False
    assert overview["kill_switch"]["available"] is False
    assert len(overview["unreadable_configs"]) == 1
    text = json.dumps(overview)
    for spelling in (str(project), project.as_posix(), json.dumps(str(project))[1:-1]):
        assert spelling not in text


def test_the_loaders_do_put_the_path_in_their_messages(project: Path) -> None:
    # The control for the test above: without the rewriting there is a path to find.
    (project / "configs" / "risk_limits.yaml").unlink()

    with pytest.raises((OSError, ValueError)) as caught:
        load_limits(project / "configs" / "risk_limits.yaml")

    assert str(project) in str(caught.value) or project.as_posix() in str(caught.value)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_an_artifact_holding_a_number_json_does_not_have_is_passed_over(
    project: Path, constant: str
) -> None:
    older = _artifact(project, "01")
    newer = _artifact(project, "02")
    newer.write_text(
        newer.read_text(encoding="utf-8").replace('"sharpe": 0.5', f'"sharpe": {constant}'),
        encoding="utf-8",
    )
    assert constant in newer.read_text(encoding="utf-8")

    overview = read_overview(project, {})

    assert overview["strategies"][0]["latest_search"]["artifact"] == older.name
    json.dumps(overview, allow_nan=False)


def test_an_artifact_too_large_to_be_one_is_not_opened(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = _artifact(project, "01")
    newer = _artifact(project, "02", padding="x" * 4000)
    monkeypatch.setattr("strategylab.api.overview.MAX_ARTIFACT_BYTES", newer.stat().st_size - 1)
    assert older.stat().st_size < newer.stat().st_size - 1

    search = read_overview(project, {})["strategies"][0]["latest_search"]

    assert search["artifact"] == older.name


def test_a_config_too_large_to_be_one_is_refused_with_its_size(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    size = (project / "configs" / "ma_crossover.yaml").stat().st_size
    monkeypatch.setattr("strategylab.api.overview.MAX_CONFIG_BYTES", size - 1)

    overview = read_overview(project, {})

    assert overview["strategies"] == []
    (refused,) = overview["unreadable_configs"]
    assert f"{size} bytes" in refused["reason"]

"""Tests for what the window remembers between launches: whether the tour has been seen."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.overview import read_overview
from strategylab.api.window_state import WINDOW_FILE, mark_tour_seen, tour_seen

REPOSITORY = Path(__file__).resolve().parents[1]
TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NOW = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding the committed protocol, strategy config and risk limits."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    return tmp_path


def test_a_window_that_has_never_opened_has_not_seen_the_tour(project: Path) -> None:
    assert tour_seen(project) is False
    assert read_overview(project, {})["tour_seen"] is False


def test_once_marked_the_tour_stays_seen(project: Path) -> None:
    assert mark_tour_seen(project, NOW) is True

    assert tour_seen(project) is True
    assert read_overview(project, {})["tour_seen"] is True
    assert sorted(path.name for path in (project / "state").iterdir()) == ["window.json"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"schema_version": 2, "tour_seen_at": "2026-11-12T09:30:00+00:00"}',
        '{"schema_version": 1, "tour_seen_at": true}',
        '{"schema_version": 1}',
    ],
)
def test_a_damaged_memory_offers_the_tour_again(project: Path, text: str) -> None:
    (project / "state").mkdir()
    (project / WINDOW_FILE).write_text(text, encoding="utf-8")

    assert tour_seen(project) is False


def test_a_write_that_fails_halfway_leaves_nothing_behind(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(source: object, target: object) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr("strategylab.api.window_state.os.replace", interrupted)
    with pytest.raises(OSError, match="went away"):
        mark_tour_seen(project, NOW)

    assert list((project / "state").iterdir()) == []


@pytest.fixture
def client(project: Path) -> TestClient:
    return TestClient(create_app(project, TOKEN, {}), base_url="http://127.0.0.1")


def test_marking_over_the_route(project: Path, client: TestClient) -> None:
    answer = client.post("/api/tour/seen", headers=AUTH)

    assert answer.status_code == 200
    assert answer.json() == {"tour_seen": True}
    assert client.get("/api/overview", headers=AUTH).json()["tour_seen"] is True


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {**AUTH, "Origin": "https://evil.example"}],
)
def test_nothing_is_marked_without_the_token_from_this_machine(
    project: Path, client: TestClient, headers: dict[str, str]
) -> None:
    assert client.post("/api/tour/seen", headers=headers).status_code in (401, 403)
    assert not (project / "state").exists()


def test_a_memory_that_cannot_be_written_says_so(project: Path, client: TestClient) -> None:
    (project / "state").mkdir()
    (project / WINDOW_FILE).mkdir()

    answer = client.post("/api/tour/seen", headers=AUTH)

    assert answer.status_code == 500
    assert answer.json() == {"detail": "the window's memory could not be written"}

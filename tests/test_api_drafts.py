"""Tests for what the app says about a draft: its rules in words, its source, its confirmation.

Every test builds a small project in a temporary directory. A second strategy,
written here, stands in for one read out of a video; its source file is
written by hand, so each value asserted is one the test chose.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from strategylab.api.app import MIN_TOKEN_CHARS, create_app
from strategylab.api.drafts import (
    CONFIRMED_FILE,
    LINK_PREFIX,
    MAX_ITEMS,
    MAX_TEXT,
    StaleConfirmation,
    confirm,
    confirmation,
    read_source,
    source_file,
)
from strategylab.api.jobs import JobRefused, start_job
from strategylab.api.overview import read_overview
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.api.rules import OPERATOR_WORDS, axis_size, condition_words, describe
from strategylab.core.config import INDICATORS, OPERATORS, load_strategy
from strategylab.core.types import Condition, IndicatorRef
from strategylab.search.grid import expand_grid

REPOSITORY = Path(__file__).resolve().parents[1]

TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}

NOW = datetime.now(UTC).replace(microsecond=0)

#: A strategy as the import skill would write one: fixed and searched
#: periods, a numeric target, every operator.
PULLBACK = """\
name: trend_pullback
version: 1
description: >
  Buy pullbacks in an uptrend. Read from a video; see trend_pullback.source.json.
entry:
  - {indicator: sma, period: 50, operator: above, target: {indicator: sma, period: 200}}
  - {indicator: close, operator: crosses_above, target: {indicator: sma, period: "$short"}}
exit:
  - {indicator: close, operator: crosses_below, target: {indicator: sma, period: 50}}
search:
  short: {min: 10, max: 30, step: 10}
fixed:
  position_size_pct: 50
"""

SOURCE: dict[str, Any] = {
    "schema_version": 1,
    "source": {
        "url": "https://www.youtube.com/watch?v=sample",
        "title": "A simple pullback strategy",
        "channel": "Sample Channel",
        "watched_at": "2026-11-12",
    },
    "rules": [
        {"rule": "Only buy when the 50-day average is above the 200-day.", "at": "2:10"},
        {"rule": "Buy when price closes back above the 20-day average.", "at": "3:45"},
    ],
    "left_out": [
        {
            "what": "Stop-loss 2% below the buy price",
            "why": "Stop-losses are not supported yet.",
            "at": "5:02",
        },
        {"what": "Only when volume is strong", "why": "Too vague to be a rule.", "at": "6:30"},
    ],
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding the committed protocol, config and limits, and a second strategy."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    (tmp_path / "configs" / "trend_pullback.yaml").write_text(PULLBACK, encoding="utf-8")
    return tmp_path


def _strategy(project: Path, strategy_id: str = "trend_pullback") -> StrategyFile:
    found = find_strategy(project, strategy_id)
    assert found is not None
    return found


def _source(project: Path, payload: Any) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (project / "configs" / "trend_pullback.source.json").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# Rules in words.
# --------------------------------------------------------------------------


def test_the_committed_strategy_reads_as_its_rules(project: Path) -> None:
    rules = describe(_strategy(project, "ma_crossover").definition)

    assert rules == {
        "entry": [
            "The average price over fast days crosses above the average price over slow days.",
            "The price is above the average price over trend days.",
        ],
        "exit": [
            "The average price over fast days crosses below the average price over slow days."
        ],
        "search": [
            "fast: 5 to 30 days, in steps of 5 (6 values).",
            "slow: 40 to 120 days, in steps of 20 (5 values).",
            "trend: 100 to 200 days, in steps of 50 (3 values).",
        ],
        "fixed": ["Each trade uses 95% of the account."],
        "versions": 90,
    }


def test_fixed_periods_are_written_as_numbers_and_searched_ones_by_name(project: Path) -> None:
    rules = describe(_strategy(project).definition)

    assert rules["entry"] == [
        "The 50-day average price is above the 200-day average price.",
        "The price crosses above the average price over short days.",
    ]
    assert rules["exit"] == ["The price crosses below the 50-day average price."]
    assert rules["search"] == ["short: 10 to 30 days, in steps of 10 (3 values)."]
    assert rules["fixed"] == ["Each trade uses 50% of the account."]
    assert rules["versions"] == 3


def test_a_number_on_the_right_is_written_without_a_trailing_point() -> None:
    whole = Condition(left=IndicatorRef("close"), operator="above", right=100.0)
    part = Condition(left=IndicatorRef("close"), operator="above", right=99.5)

    assert condition_words(whole) == "The price is above 100."
    assert condition_words(part) == "The price is above 99.5."


def test_a_range_of_one_value_says_one_value(tmp_path: Path) -> None:
    path = tmp_path / "one.yaml"
    path.write_text(
        PULLBACK.replace(
            "short: {min: 10, max: 30, step: 10}", "short: {min: 20, max: 20, step: 5}"
        ),
        encoding="utf-8",
    )

    assert describe(load_strategy(path))["search"] == [
        "short: 20 to 20 days, in steps of 5 (1 value)."
    ]


@pytest.mark.parametrize(
    "spec",
    [
        {"min": 10, "max": 30, "step": 10},
        {"min": 10, "max": 50, "step": 15},
        {"min": 5, "max": 5, "step": 1},
        {"min": 1, "max": 200, "step": 7},
        {"min": 3, "max": 4, "step": 5},
    ],
)
def test_a_range_is_counted_as_the_search_walks_it(spec: dict[str, float]) -> None:
    assert axis_size(spec) == len(expand_grid({"x": spec}))


def test_a_vast_grid_is_counted_without_being_built(tmp_path: Path) -> None:
    # Three ranges of a thousand values: a billion versions. Listing them to
    # count them would exhaust memory; counting them takes no time at all.
    path = tmp_path / "vast.yaml"
    vast = (
        PULLBACK.replace("period: 200}}", 'period: "$long"}}')
        .replace("period: 50, operator: above", 'period: "$mid", operator: above')
        .replace(
            "short: {min: 10, max: 30, step: 10}",
            "\n  ".join(
                f"{name}: {{min: 1, max: 1000, step: 1}}" for name in ("short", "mid", "long")
            ),
        )
    )
    path.write_text(vast, encoding="utf-8")

    assert describe(load_strategy(path))["versions"] == 1000**3


def test_a_range_counts_its_values_as_the_search_does(tmp_path: Path) -> None:
    # 10, 25, 40: max 50 is not on a step, and the search does not reach it.
    path = tmp_path / "uneven.yaml"
    path.write_text(
        PULLBACK.replace(
            "short: {min: 10, max: 30, step: 10}", "short: {min: 10, max: 50, step: 15}"
        ),
        encoding="utf-8",
    )

    assert describe(load_strategy(path))["search"] == [
        "short: 10 to 50 days, in steps of 15 (3 values)."
    ]


def test_every_operator_the_loader_accepts_has_words() -> None:
    assert set(OPERATOR_WORDS) == set(OPERATORS)


@pytest.mark.parametrize("indicator", sorted(INDICATORS))
def test_every_indicator_the_loader_accepts_has_words(indicator: str) -> None:
    ref = IndicatorRef(indicator, period=10 if INDICATORS[indicator] else None)

    words = condition_words(Condition(left=ref, operator="above", right=1.0))

    assert indicator not in words.lower().split()


def test_an_indicator_or_operator_without_words_is_refused_not_passed_through() -> None:
    with pytest.raises(ValueError, match="no words for indicator"):
        condition_words(Condition(left=IndicatorRef("ema", period=5), operator="above", right=1.0))
    with pytest.raises(ValueError, match="no words for operator"):
        condition_words(Condition(left=IndicatorRef("close"), operator="below", right=1.0))


# --------------------------------------------------------------------------
# Where a strategy came from.
# --------------------------------------------------------------------------


def test_a_strategy_written_by_hand_has_no_source(project: Path) -> None:
    assert read_source(project, _strategy(project, "ma_crossover")) is None


def test_the_source_file_is_never_taken_for_a_strategy(project: Path) -> None:
    _source(project, SOURCE)

    overview = read_overview(project, {})

    assert [strategy["id"] for strategy in overview["strategies"]] == [
        "ma_crossover",
        "trend_pullback",
    ]
    assert overview["unreadable_configs"] == []


def test_a_source_is_reported_as_recorded(project: Path) -> None:
    _source(project, SOURCE)
    strategy = _strategy(project)

    assert source_file(strategy) == Path("configs/trend_pullback.source.json")
    assert read_source(project, strategy) == {
        "available": True,
        "url": "https://www.youtube.com/watch?v=sample",
        "title": "A simple pullback strategy",
        "channel": "Sample Channel",
        "watched_at": "2026-11-12",
        "rules": SOURCE["rules"],
        "left_out": SOURCE["left_out"],
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "www.youtube.com/x",
        7,
        None,
    ],
)
def test_a_link_that_is_not_https_is_not_passed_on(project: Path, url: Any) -> None:
    _source(project, {**SOURCE, "source": {**SOURCE["source"], "url": url}})

    reported = read_source(project, _strategy(project))

    assert reported is not None
    assert reported["url"] is None
    assert LINK_PREFIX == "https://"


def test_fields_of_the_wrong_kind_are_none_or_skipped(project: Path) -> None:
    _source(
        project,
        {
            "schema_version": 1,
            "source": {"title": ["a list"], "channel": "  ", "watched_at": 20261112},
            "rules": [{"rule": "kept", "at": 12}, {"rule": None}, "not an object", {"at": "1:00"}],
            "left_out": {"what": "an object, not a list"},
        },
    )

    reported = read_source(project, _strategy(project))

    assert reported is not None
    assert (reported["title"], reported["channel"], reported["watched_at"]) == (None, None, None)
    assert reported["rules"] == [{"rule": "kept", "at": None}]
    assert reported["left_out"] == []


def test_long_text_and_long_lists_are_cut_short(project: Path) -> None:
    many = [{"rule": "x" * (MAX_TEXT * 3), "at": "0:01"} for _ in range(MAX_ITEMS * 2)]
    _source(project, {**SOURCE, "rules": many})

    reported = read_source(project, _strategy(project))

    assert reported is not None
    assert len(reported["rules"]) == MAX_ITEMS
    assert len(reported["rules"][0]["rule"]) == MAX_TEXT


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "could not be read"),
        ("[1, 2]", "could not be read"),
        ('{"schema_version": 2}', "format this app does not read"),
        ('{"rules": []}', "format this app does not read"),
    ],
)
def test_a_source_file_that_cannot_be_read_is_missing_with_a_reason(
    project: Path, text: str, reason: str
) -> None:
    _source(project, text)

    reported = read_source(project, _strategy(project))

    assert reported is not None
    assert reported["available"] is False
    assert reason in reported["reason"]
    assert str(project) not in reported["reason"]


# --------------------------------------------------------------------------
# Confirming the rules.
# --------------------------------------------------------------------------


def test_a_strategy_nobody_has_confirmed_is_not_confirmed(project: Path) -> None:
    assert confirmation(project, _strategy(project)) == {
        "confirmed": False,
        "confirmed_at": None,
        "stale": False,
    }


def test_confirming_records_the_file_as_it_is_now(project: Path) -> None:
    strategy = _strategy(project)

    result = confirm(project, strategy, strategy.config_hash, NOW)

    assert result == {"confirmed": True, "confirmed_at": NOW.isoformat(), "stale": False}
    record = json.loads((project / CONFIRMED_FILE).read_text(encoding="utf-8"))
    assert record["confirmed"]["trend_pullback"]["config_hash"] == strategy.config_hash
    assert not (project / CONFIRMED_FILE).with_suffix(".partial").exists()


def test_editing_the_file_undoes_the_confirmation(project: Path) -> None:
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)
    path = project / "configs" / "trend_pullback.yaml"
    path.write_text(PULLBACK.replace("period: 200", "period: 150"), encoding="utf-8")

    assert confirmation(project, _strategy(project)) == {
        "confirmed": False,
        "confirmed_at": None,
        "stale": True,
    }


def test_a_change_the_loader_does_not_see_keeps_the_confirmation(project: Path) -> None:
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)
    path = project / "configs" / "trend_pullback.yaml"
    path.write_text("# a comment added\n" + PULLBACK, encoding="utf-8")

    assert confirmation(project, _strategy(project))["confirmed"] is True


def test_confirming_one_strategy_does_not_confirm_another(project: Path) -> None:
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)
    confirm(
        project,
        _strategy(project, "ma_crossover"),
        _strategy(project, "ma_crossover").config_hash,
        NOW + timedelta(seconds=1),
    )

    assert confirmation(project, _strategy(project))["confirmed"] is True
    assert confirmation(project, _strategy(project, "ma_crossover"))["confirmed"] is True
    record = json.loads((project / CONFIRMED_FILE).read_text(encoding="utf-8"))
    assert sorted(record["confirmed"]) == ["ma_crossover", "trend_pullback"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"schema_version": 1, "confirmed": []}',
        '{"schema_version": 1, "confirmed": {"trend_pullback": "yes"}}',
    ],
)
def test_a_confirmations_file_that_cannot_be_read_confirms_nothing(
    project: Path, text: str
) -> None:
    (project / "state").mkdir()
    (project / CONFIRMED_FILE).write_text(text, encoding="utf-8")

    assert confirmation(project, _strategy(project))["confirmed"] is False


def test_a_confirmation_for_another_hash_is_not_a_confirmation(project: Path) -> None:
    (project / "state").mkdir()
    record = {"schema_version": 1, "confirmed": {"trend_pullback": {"config_hash": "0" * 64}}}
    (project / CONFIRMED_FILE).write_text(json.dumps(record), encoding="utf-8")

    assert confirmation(project, _strategy(project))["confirmed"] is False


def test_no_job_starts_for_a_strategy_whose_rules_are_not_confirmed(project: Path) -> None:
    started: list[list[str]] = []
    strategy = _strategy(project)

    with pytest.raises(JobRefused, match="not been confirmed"):
        start_job(
            project,
            strategy,
            "search",
            NOW,
            spawn=lambda command, root, log: started.append(command),
        )

    assert started == []
    assert not (project / "state" / "jobs").exists()


def test_no_job_starts_once_the_file_has_changed_since_it_was_confirmed(project: Path) -> None:
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)
    path = project / "configs" / "trend_pullback.yaml"
    path.write_text(PULLBACK.replace("max: 30", "max: 40"), encoding="utf-8")
    started: list[list[str]] = []

    with pytest.raises(JobRefused, match="not been confirmed"):
        start_job(
            project,
            _strategy(project),
            "search",
            NOW,
            spawn=lambda command, root, log: started.append(command),
        )

    assert started == []


def test_a_confirmed_strategy_can_start_a_job(project: Path) -> None:
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)
    started: list[list[str]] = []

    job = start_job(
        project,
        _strategy(project),
        "search",
        NOW,
        spawn=lambda command, root, log: started.append(command),
    )

    assert job["status"] == "running"
    assert len(started) == 1


# --------------------------------------------------------------------------
# The overview and the route.
# --------------------------------------------------------------------------


def test_the_overview_carries_rules_source_and_confirmation(project: Path) -> None:
    _source(project, SOURCE)
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)

    (hand, video) = read_overview(project, {})["strategies"]

    assert hand["source"] is None
    assert hand["config_hash"] == _strategy(project, "ma_crossover").config_hash
    assert hand["confirmed"] is False
    assert hand["rules"]["versions"] == 90
    assert video["source"]["left_out"] == SOURCE["left_out"]
    assert video["confirmed"] is True
    assert video["confirmed_at"] == NOW.isoformat()
    assert (
        video["rules"]["entry"][0] == "The 50-day average price is above the 200-day average price."
    )
    json.dumps(read_overview(project, {}), allow_nan=False)


@pytest.fixture
def client(project: Path) -> TestClient:
    return TestClient(create_app(project, TOKEN, {}), base_url="http://127.0.0.1")


def test_confirming_over_the_route(project: Path, client: TestClient) -> None:
    answer = client.post(
        "/api/strategies/trend_pullback/confirm",
        json={"config_hash": _strategy(project).config_hash},
        headers=AUTH,
    )

    assert answer.status_code == 200
    assert answer.json()["confirmed"] is True
    overview = client.get("/api/overview", headers=AUTH).json()
    assert [strategy["confirmed"] for strategy in overview["strategies"]] == [False, True]


@pytest.mark.parametrize(
    "strategy_id", ["no_such", "risk_limits", "trend_pullback.source", "..%2Fprotocol"]
)
def test_only_a_listed_strategy_can_be_confirmed(
    project: Path, client: TestClient, strategy_id: str
) -> None:
    answer = client.post(
        f"/api/strategies/{strategy_id}/confirm", json={"config_hash": "0" * 64}, headers=AUTH
    )

    assert answer.status_code == 404
    assert not (project / CONFIRMED_FILE).exists()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {**AUTH, "Origin": "https://evil.example"},
        {**AUTH, "Host": "evil.example"},
    ],
)
def test_nothing_is_confirmed_without_the_token_from_this_machine(
    project: Path, client: TestClient, headers: dict[str, str]
) -> None:
    answer = client.post(
        "/api/strategies/trend_pullback/confirm",
        json={"config_hash": _strategy(project).config_hash},
        headers=headers,
    )

    assert answer.status_code in (401, 403)
    assert not (project / CONFIRMED_FILE).exists()


def test_a_confirmation_that_cannot_be_written_says_so_without_a_path(
    project: Path, client: TestClient
) -> None:
    (project / "state").mkdir()
    (project / CONFIRMED_FILE).mkdir()

    answer = client.post(
        "/api/strategies/trend_pullback/confirm",
        json={"config_hash": _strategy(project).config_hash},
        headers=AUTH,
    )

    assert answer.status_code == 500
    assert answer.json() == {"detail": "the confirmation could not be recorded"}


def test_a_confirmation_cannot_be_made_with_a_get(project: Path, client: TestClient) -> None:
    answer = client.get("/api/strategies/trend_pullback/confirm", headers=AUTH)

    assert answer.status_code == 405
    assert not (project / CONFIRMED_FILE).exists()


def test_a_confirmations_file_of_another_schema_confirms_nothing_even_for_the_right_file(
    project: Path,
) -> None:
    strategy = _strategy(project)
    (project / "state").mkdir()
    record = {
        "schema_version": 2,
        "confirmed": {"trend_pullback": {"config_hash": strategy.config_hash}},
    }
    (project / CONFIRMED_FILE).write_text(json.dumps(record), encoding="utf-8")

    assert confirmation(project, strategy)["confirmed"] is False


def test_a_confirmation_that_fails_halfway_leaves_the_earlier_ones_intact(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    confirm(
        project,
        _strategy(project, "ma_crossover"),
        _strategy(project, "ma_crossover").config_hash,
        NOW,
    )
    before = (project / CONFIRMED_FILE).read_text(encoding="utf-8")

    def interrupted(source: Any, target: Any) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr("strategylab.api.drafts.os.replace", interrupted)
    with pytest.raises(OSError, match="went away"):
        confirm(project, _strategy(project), _strategy(project).config_hash, NOW)

    assert (project / CONFIRMED_FILE).read_text(encoding="utf-8") == before
    assert confirmation(project, _strategy(project, "ma_crossover"))["confirmed"] is True
    assert sorted(path.name for path in (project / "state").iterdir()) == ["confirmed.json"]


def test_rules_that_were_never_shown_cannot_be_confirmed(project: Path) -> None:
    shown = _strategy(project)
    path = project / "configs" / "trend_pullback.yaml"
    path.write_text(PULLBACK.replace("period: 200", "period: 150"), encoding="utf-8")

    with pytest.raises(StaleConfirmation, match="changed since its rules were shown"):
        confirm(project, _strategy(project), shown.config_hash, NOW)

    assert not (project / CONFIRMED_FILE).exists()


def test_the_route_refuses_a_confirmation_for_rules_no_longer_in_the_file(
    project: Path, client: TestClient
) -> None:
    shown = _strategy(project).config_hash
    path = project / "configs" / "trend_pullback.yaml"
    path.write_text(PULLBACK.replace("period: 200", "period: 150"), encoding="utf-8")

    answer = client.post(
        "/api/strategies/trend_pullback/confirm", json={"config_hash": shown}, headers=AUTH
    )

    assert answer.status_code == 409
    assert "read them again" in answer.json()["detail"]
    assert not (project / CONFIRMED_FILE).exists()


@pytest.mark.parametrize("body", [None, {}, {"config_hash": "short"}, {"config_hash": 7}])
def test_a_confirmation_without_the_hash_shown_is_refused(
    project: Path, client: TestClient, body: Any
) -> None:
    answer = client.post("/api/strategies/trend_pullback/confirm", json=body, headers=AUTH)

    assert answer.status_code == 422
    assert not (project / CONFIRMED_FILE).exists()


def test_changing_the_source_file_undoes_the_confirmation(project: Path) -> None:
    _source(project, SOURCE)
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)
    assert confirmation(project, _strategy(project))["confirmed"] is True

    _source(project, {**SOURCE, "left_out": []})

    assert confirmation(project, _strategy(project)) == {
        "confirmed": False,
        "confirmed_at": None,
        "stale": True,
    }


def test_adding_a_source_file_after_confirming_undoes_the_confirmation(project: Path) -> None:
    confirm(project, _strategy(project), _strategy(project).config_hash, NOW)

    _source(project, SOURCE)

    assert confirmation(project, _strategy(project))["confirmed"] is False


def test_many_confirmations_at_once_all_land(project: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    names = [f"copy_{index}" for index in range(8)]
    for name in names:
        (project / "configs" / f"{name}.yaml").write_text(
            PULLBACK.replace("name: trend_pullback", f"name: {name}"), encoding="utf-8"
        )
    strategies = [_strategy(project, name) for name in names] * 3

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda each: confirm(project, each, each.config_hash, NOW), strategies))

    for name in names:
        assert confirmation(project, _strategy(project, name))["confirmed"] is True
    leftovers = [
        path.name for path in (project / "state").iterdir() if path.name != "confirmed.json"
    ]
    assert leftovers == []


@pytest.mark.parametrize("hidden", ["\u202e", "\u0000", "\u200b", "\u0007"])
def test_characters_that_hide_or_turn_text_are_dropped(project: Path, hidden: str) -> None:
    title = f"Real{hidden}Title"
    _source(project, {**SOURCE, "source": {**SOURCE["source"], "title": title, "channel": hidden}})

    reported = read_source(project, _strategy(project))

    assert reported is not None
    assert reported["title"] == "RealTitle"
    assert reported["channel"] is None

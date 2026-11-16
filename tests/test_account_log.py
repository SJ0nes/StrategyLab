"""Tests for the account log: one line per daily check, the account and its order's end."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from strategylab.core.types import Side
from strategylab.execution.venue import Balance, Order, OrderType, Position
from strategylab.live.account import (
    ACCOUNT_SCHEMA_VERSION,
    ORDER_OUTCOMES,
    account_record,
    write_account,
)

BALANCE = Balance(currency="GBP", cash=250.5, equity=10125.25)
HELD = [
    Position(ticker="VUAG.L", quantity=101.5, average_price=95.0, market_value=9874.75),
    Position(ticker="OTHER.L", quantity=7.0, average_price=1.0, market_value=7.0),
]
ORDER = Order(
    ticker="VUAG.L", side=Side.SELL, quantity=101.5, order_type=OrderType.MARKET, client_ref="r1"
)
WHEN = datetime(2026, 11, 12, 17, 45, tzinfo=UTC)


def _record(**changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "recorded_at": WHEN,
        "bar_date": date(2026, 11, 12),
        "strategy": "ma_crossover",
        "params": {"fast": 10, "slow": 80, "trend": 150},
        "balance": BALANCE,
        "positions": HELD,
        "ticker": "VUAG.L",
        "order": ORDER,
        "outcome": "sent",
        "venue_order_id": "12345",
    }
    arguments.update(changes)
    return account_record(**arguments)


def test_a_check_that_sent_an_order_is_recorded_as_the_venue_reported_it() -> None:
    entry = _record()

    assert entry.schema_version == ACCOUNT_SCHEMA_VERSION == 1
    assert entry.recorded_at == "2026-11-12T17:45:00+00:00"
    assert entry.bar_date == "2026-11-12"
    assert (entry.currency, entry.equity, entry.cash) == ("GBP", 10125.25, 250.5)
    assert entry.position_quantity == 101.5
    assert (entry.order_side, entry.order_quantity) == ("sell", 101.5)
    assert (entry.order_outcome, entry.venue_order_id) == ("sent", "12345")


def test_a_check_run_by_hand_names_no_strategy_and_one_share() -> None:
    entry = _record()

    assert (entry.strategy_id, entry.strategies) == (None, 1)


def test_a_deployed_strategys_check_names_it_and_how_many_shared_the_account() -> None:
    entry = _record(strategy_id="ma_crossover", strategies=3)

    assert (entry.strategy_id, entry.strategies) == ("ma_crossover", 3)


def test_only_the_traded_instrument_counts_as_the_position() -> None:
    assert _record(positions=[*HELD, HELD[0]]).position_quantity == 203.0
    assert _record(positions=[]).position_quantity == 0


def test_a_check_with_no_order_records_none() -> None:
    entry = _record(order=None, outcome="none", venue_order_id=None)

    assert (entry.order_side, entry.order_quantity, entry.order_outcome) == (None, None, "none")


def test_a_refused_order_records_failed_with_no_id() -> None:
    entry = _record(outcome="failed", venue_order_id=None)

    assert (entry.order_outcome, entry.venue_order_id) == ("failed", None)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"recorded_at": datetime(2026, 11, 12, 17, 45)}, "no offset"),
        ({"outcome": "filled"}, "not one of"),
        ({"order": None, "outcome": "sent"}, "does not fit"),
        ({"outcome": "none", "venue_order_id": None}, "does not fit"),
        ({"outcome": "failed", "venue_order_id": "12345"}, "venue order id"),
        ({"venue_order_id": None}, "venue order id"),
        ({"balance": Balance(currency="GBP", cash=1.0, equity=float("nan"))}, "not a number"),
        ({"balance": Balance(currency="GBP", cash=float("inf"), equity=1.0)}, "not a number"),
        ({"strategies": 0}, "at least one"),
    ],
)
def test_a_record_that_contradicts_itself_is_refused(changes: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _record(**changes)


def test_the_outcomes_are_three() -> None:
    assert {"none", "sent", "failed"} == ORDER_OUTCOMES


def test_a_time_in_another_zone_keeps_its_offset() -> None:
    elsewhere = WHEN.astimezone(timezone(timedelta(hours=1)))

    assert _record(recorded_at=elsewhere).recorded_at == "2026-11-12T18:45:00+01:00"


def test_each_check_appends_one_line_of_json(tmp_path: Path) -> None:
    path = tmp_path / "state" / "account-live.jsonl"

    write_account(_record(), path)
    write_account(_record(order=None, outcome="none", venue_order_id=None), path)

    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b""
    first, second = (json.loads(line) for line in lines[:-1])
    assert first["order_outcome"] == "sent"
    assert first["params"] == {"fast": 10, "slow": 80, "trend": 150}
    assert second["order_outcome"] == "none"
    assert b"\r" not in path.read_bytes()

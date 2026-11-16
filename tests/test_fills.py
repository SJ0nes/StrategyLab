"""Tests for the fills log: each sent order's fill, learned at a later check."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from strategylab.core.types import Side
from strategylab.execution.venue import Balance, Charge, Fill, Order, OrderId, OrderType
from strategylab.live.account import account_record, write_account
from strategylab.live.fills import (
    FILLS_SCHEMA_VERSION,
    fill_record,
    record_fills,
    unfilled_orders,
    write_fill,
)

NOW = datetime(2026, 11, 18, 17, 40, tzinfo=UTC)
BALANCE = Balance(currency="GBP", cash=1000.0, equity=1000.0)


def _fill(order_id: str, price: float = 101.25, **changes: Any) -> Fill:
    values: dict[str, Any] = {
        "order_id": OrderId(order_id),
        "ticker": "VUAG.L",
        "side": Side.BUY,
        "quantity": 2.5,
        "price": price,
        "currency": "GBP",
        "filled_at": datetime(2026, 11, 18, 8, 0, 3, tzinfo=UTC),
        "charges": (Charge(name="TRANSACTION_FEE", amount=-0.1, currency="GBP"),),
    }
    values.update(changes)
    return Fill(**values)


def _check(log: Path, outcome: str, order_id: str | None) -> None:
    """Append one account line, as ``run`` does at the end of a check."""
    order = (
        None
        if outcome == "none"
        else Order(
            ticker="VUAG.L",
            side=Side.BUY,
            quantity=2.5,
            order_type=OrderType.MARKET,
            client_ref="r",
        )
    )
    write_account(
        account_record(
            recorded_at=NOW,
            bar_date=date(2026, 11, 17),
            strategy="ma_crossover",
            params={"fast": 10, "slow": 80, "trend": 150},
            balance=BALANCE,
            positions=[],
            ticker="VUAG.L",
            order=order,
            outcome=outcome,
            venue_order_id=order_id,
        ),
        log,
    )


class FakeVenue:
    """A venue that reports a fixed list of fills and counts how often it was asked."""

    def __init__(self, fills: list[Fill]) -> None:
        """Report ``fills`` whenever asked."""
        self.fills = fills
        self.asked: list[str] = []

    def get_fills(self, ticker: str) -> list[Fill]:
        """Note the ticker asked for and return the fills."""
        self.asked.append(ticker)
        return list(self.fills)


@pytest.fixture
def logs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "state" / "account-live.jsonl", tmp_path / "state" / "fills-live.jsonl"


# --------------------------------------------------------------------------
# unfilled_orders
# --------------------------------------------------------------------------


def test_no_logs_means_nothing_is_waiting(logs: tuple[Path, Path]) -> None:
    assert unfilled_orders(*logs) == []


def test_only_sent_orders_wait_for_a_fill_each_once_oldest_first(
    logs: tuple[Path, Path],
) -> None:
    account, fills = logs
    _check(account, "sent", "7")
    _check(account, "none", None)
    _check(account, "failed", None)
    _check(account, "sent", "3")
    _check(account, "sent", "7")

    assert unfilled_orders(account, fills) == ["7", "3"]


def test_an_order_whose_fill_is_recorded_waits_no_longer(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _check(account, "sent", "7")
    _check(account, "sent", "8")
    write_fill(fill_record(_fill("7"), NOW), fills)

    assert unfilled_orders(account, fills) == ["8"]


@pytest.mark.parametrize("bad", ["not json", "[1, 2]"], ids=["not-json", "not-an-object"])
def test_a_damaged_log_line_is_refused_with_its_number(logs: tuple[Path, Path], bad: str) -> None:
    account, fills = logs
    _check(account, "sent", "7")
    with account.open("a", encoding="utf-8") as handle:
        handle.write(bad + "\n")

    with pytest.raises(ValueError, match="line 2"):
        unfilled_orders(account, fills)


# --------------------------------------------------------------------------
# fill_record and write_fill
# --------------------------------------------------------------------------


def test_a_fill_is_recorded_as_the_venue_reported_it(logs: tuple[Path, Path]) -> None:
    _, fills = logs
    write_fill(fill_record(_fill("7"), NOW), fills)

    (line,) = fills.read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {
        "schema_version": FILLS_SCHEMA_VERSION,
        "recorded_at": "2026-11-18T17:40:00+00:00",
        "venue_order_id": "7",
        "ticker": "VUAG.L",
        "side": "buy",
        "quantity": 2.5,
        "price": 101.25,
        "currency": "GBP",
        "filled_at": "2026-11-18T08:00:03+00:00",
        "charges": [{"name": "TRANSACTION_FEE", "amount": -0.1, "currency": "GBP"}],
    }
    assert FILLS_SCHEMA_VERSION == 1


@pytest.mark.parametrize("which", ["recorded_at", "filled_at"])
def test_a_time_without_an_offset_is_refused(which: str) -> None:
    naive = datetime(2026, 11, 18, 8, 0)
    with pytest.raises(ValueError, match="offset"):
        if which == "recorded_at":
            fill_record(_fill("7"), naive)
        else:
            fill_record(_fill("7", filled_at=naive), NOW)


def test_a_price_that_is_not_a_number_is_not_written(logs: tuple[Path, Path]) -> None:
    _, fills = logs
    with pytest.raises(ValueError):
        write_fill(fill_record(_fill("7", price=float("nan")), NOW), fills)
    assert not fills.exists() or fills.read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------------
# record_fills
# --------------------------------------------------------------------------


def test_the_venue_is_not_asked_when_no_order_waits(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _check(account, "none", None)
    venue = FakeVenue([_fill("7")])

    assert record_fills(venue, "VUAG.L", NOW, account, fills) == []
    assert venue.asked == []
    assert not fills.exists()


def test_waiting_orders_get_their_fills_and_others_are_ignored(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _check(account, "sent", "7")
    _check(account, "sent", "8")
    venue = FakeVenue([_fill("99", price=1.0), _fill("8", price=102.0), _fill("7", price=101.0)])

    written = record_fills(venue, "VUAG.L", NOW, account, fills)

    assert venue.asked == ["VUAG.L"]
    assert [(e.venue_order_id, e.price) for e in written] == [("7", 101.0), ("8", 102.0)]
    lines = [json.loads(t) for t in fills.read_text(encoding="utf-8").splitlines()]
    assert [(e["venue_order_id"], e["price"]) for e in lines] == [("7", 101.0), ("8", 102.0)]


def test_an_order_not_yet_filled_is_looked_for_again_next_time(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _check(account, "sent", "7")

    assert record_fills(FakeVenue([]), "VUAG.L", NOW, account, fills) == []
    later = record_fills(FakeVenue([_fill("7")]), "VUAG.L", NOW, account, fills)

    assert [e.venue_order_id for e in later] == ["7"]


def test_a_fill_already_recorded_is_never_written_twice(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _check(account, "sent", "7")
    venue = FakeVenue([_fill("7")])

    record_fills(venue, "VUAG.L", NOW, account, fills)
    again = record_fills(venue, "VUAG.L", NOW, account, fills)

    assert again == []
    assert len(fills.read_text(encoding="utf-8").splitlines()) == 1


def test_an_order_reported_twice_is_recorded_by_the_fill_listed_first(
    logs: tuple[Path, Path],
) -> None:
    account, fills = logs
    _check(account, "sent", "7")
    venue = FakeVenue([_fill("7", price=103.0), _fill("7", price=100.0)])

    (entry,) = record_fills(venue, "VUAG.L", NOW, account, fills)

    assert entry.price == 103.0


def test_an_id_on_a_line_that_was_not_sent_is_not_waited_for(logs: tuple[Path, Path]) -> None:
    # account_record refuses to build such a line; a log edited by hand can hold one.
    account, fills = logs
    account.parent.mkdir(parents=True)
    account.write_text(
        json.dumps({"order_outcome": "failed", "venue_order_id": "5"}) + "\n", encoding="utf-8"
    )

    assert unfilled_orders(account, fills) == []

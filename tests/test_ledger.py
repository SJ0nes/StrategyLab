"""Tests for the ledger: each strategy's units, worked out from the account and fills logs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from strategylab.core.types import Side
from strategylab.execution.venue import Balance, Fill, Order, OrderId, OrderType, Position
from strategylab.live.account import account_record, write_account
from strategylab.live.fills import fill_record, write_fill
from strategylab.live.ledger import (
    AGREEMENT_TOLERANCE_UNITS,
    Correction,
    Ledger,
    deployed_holdings,
    disagreement,
    disagreement_with,
    read_corrections,
    read_ledger,
    write_correction,
)

NOW = datetime(2026, 11, 18, 17, 40, tzinfo=UTC)


@pytest.fixture
def logs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "account-live.jsonl", tmp_path / "fills-live.jsonl"


def _sent(log: Path, order_id: str, strategy_id: str | None, side: Side = Side.BUY) -> None:
    order = Order(
        ticker="VUAG.L", side=side, quantity=1.0, order_type=OrderType.MARKET, client_ref="r"
    )
    write_account(
        account_record(
            recorded_at=NOW,
            bar_date=date(2026, 11, 17),
            strategy="s",
            params={},
            balance=Balance(currency="GBP", cash=1.0, equity=1.0),
            positions=[],
            ticker="VUAG.L",
            order=order,
            outcome="sent",
            venue_order_id=order_id,
            strategy_id=strategy_id,
            strategies=2,
        ),
        log,
    )


def _filled(log: Path, order_id: str, quantity: float, side: Side = Side.BUY) -> None:
    fill = Fill(
        order_id=OrderId(order_id),
        ticker="VUAG.L",
        side=side,
        quantity=quantity,
        price=100.0,
        currency="GBP",
        filled_at=NOW,
        charges=(),
    )
    write_fill(fill_record(fill, NOW), log)


def _ledger(account: Path, fills: Path) -> Ledger:
    return read_ledger(account, fills, account.parent / "corrections-live.jsonl")


def test_no_logs_is_an_empty_ledger(logs: tuple[Path, Path]) -> None:
    ledger = _ledger(*logs)

    assert ledger == Ledger()
    assert (ledger.held("a"), ledger.total()) == (0.0, 0.0)


def test_each_strategy_holds_its_own_buys_less_its_own_sells(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    _sent(account, "2", "b")
    _sent(account, "3", "a", Side.SELL)
    _filled(fills, "1", 4.5)
    _filled(fills, "2", 2.25)
    _filled(fills, "3", 1.5, Side.SELL)

    ledger = _ledger(account, fills)

    assert ledger.units == {"a": 3.0, "b": 2.25}
    assert ledger.total() == 5.25
    assert ledger.waiting == frozenset()


def test_a_sent_order_with_no_fill_yet_counts_nothing_and_its_strategy_waits(
    logs: tuple[Path, Path],
) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    _sent(account, "2", "b")
    _filled(fills, "1", 4.0)

    ledger = _ledger(account, fills)

    assert ledger.units == {"a": 4.0}
    assert ledger.waiting == frozenset({"b"})


def test_a_check_run_by_hand_is_counted_under_none(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", None)
    _filled(fills, "1", 2.0)

    ledger = _ledger(account, fills)

    assert ledger.units == {None: 2.0}
    assert ledger.held(None) == 2.0


def test_a_fill_of_an_order_no_check_sent_is_not_counted(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    _filled(fills, "1", 2.0)
    _filled(fills, "99", 50.0)

    assert _ledger(account, fills).units == {"a": 2.0}


def test_an_order_filled_twice_in_the_log_counts_once(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    _filled(fills, "1", 2.0)
    _filled(fills, "1", 7.0)

    assert _ledger(account, fills).units == {"a": 2.0}


def test_an_order_id_sent_twice_belongs_to_the_first_line(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    _sent(account, "1", "b")
    _filled(fills, "1", 2.0)

    assert _ledger(account, fills).units == {"a": 2.0}


@pytest.mark.parametrize(
    "line",
    [
        {"venue_order_id": "1", "side": "short", "quantity": 1.0},
        {"venue_order_id": "1", "side": "buy", "quantity": -1.0},
        {"venue_order_id": "1", "side": "buy", "quantity": 0},
        {"venue_order_id": "1", "side": "buy", "quantity": "1"},
        {"venue_order_id": "1", "side": "buy", "quantity": True},
        {"venue_order_id": "1", "side": "buy"},
    ],
    ids=["side", "negative", "zero", "text", "bool", "absent"],
)
def test_a_damaged_fill_of_a_sent_order_is_refused(
    logs: tuple[Path, Path], line: dict[str, object]
) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    fills.write_text(json.dumps(line) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="side|quantity"):
        _ledger(account, fills)


def test_an_infinite_quantity_is_refused(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "a")
    fills.write_text('{"venue_order_id": "1", "side": "buy", "quantity": Infinity}\n', "utf-8")

    with pytest.raises(ValueError, match="quantity"):
        _ledger(account, fills)


VUAG = Position(ticker="VUAG.L", quantity=5.25, average_price=1.0, market_value=5.25)
OTHER = Position(ticker="OTHER.L", quantity=9.0, average_price=1.0, market_value=9.0)


def test_ledgers_that_add_up_to_the_venues_position_agree() -> None:
    ledger = Ledger(units={"a": 3.0, "b": 2.25})

    assert disagreement(ledger, [VUAG, OTHER], "VUAG.L") is None


def test_a_difference_within_the_tolerance_is_agreement_and_beyond_it_is_not() -> None:
    near = Ledger(units={"a": 5.25 + AGREEMENT_TOLERANCE_UNITS / 2})
    far = Ledger(units={"a": 5.25 + AGREEMENT_TOLERANCE_UNITS * 2})

    assert disagreement(near, [VUAG], "VUAG.L") is None
    assert disagreement(far, [VUAG], "VUAG.L") is not None


def test_units_held_outside_the_ledgers_are_a_disagreement_that_names_both_figures() -> None:
    said = disagreement(Ledger(units={"a": 3.0}), [VUAG], "VUAG.L")

    assert said is not None
    assert "5.25" in said and "3" in said and "VUAG.L" in said


def test_a_venue_that_holds_nothing_disagrees_with_a_ledger_that_holds_units() -> None:
    assert disagreement(Ledger(units={"a": 1.0}), [OTHER], "VUAG.L") is not None
    assert disagreement(Ledger(), [OTHER], "VUAG.L") is None


def test_an_id_on_a_line_that_was_not_sent_owns_nothing(logs: tuple[Path, Path]) -> None:
    # account_record refuses to build such a line; a log edited by hand can hold one.
    account, fills = logs
    account.write_text(
        json.dumps({"order_outcome": "failed", "venue_order_id": "5", "strategy_id": "a"}) + "\n",
        encoding="utf-8",
    )
    _filled(fills, "5", 3.0)

    ledger = _ledger(account, fills)

    assert (ledger.units, ledger.waiting) == ({}, frozenset())


def test_a_strategy_left_below_zero_is_a_disagreement_even_when_the_total_agrees() -> None:
    # A check run by hand sold alpha's 2.5 and beta's 4: the total is 0, as the venue's.
    ledger = Ledger(units={"alpha": 2.5, "beta": 4.0, None: -6.5})

    said = disagreement(ledger, [], "VUAG.L")

    assert said is not None
    assert "None" in said and "fewer than zero" in said


def test_a_holding_below_zero_within_the_tolerance_is_not_a_disagreement() -> None:
    ledger = Ledger(units={"alpha": -AGREEMENT_TOLERANCE_UNITS / 2})

    assert disagreement(ledger, [], "VUAG.L") is None


def test_units_held_or_awaited_by_deployed_strategies_are_named() -> None:
    assert deployed_holdings(Ledger()) is None
    assert deployed_holdings(Ledger(units={None: 3.0, "alpha": 0.0})) is None

    holding = deployed_holdings(Ledger(units={"beta": 1.0, "alpha": 2.0}))
    waiting = deployed_holdings(Ledger(waiting=frozenset({"gamma", None})))

    assert holding is not None and "alpha, beta" in holding
    assert waiting is not None and "gamma" in waiting and "None" not in waiting


def test_a_strategy_that_sold_all_it_bought_does_not_stop_a_hand_run(
    logs: tuple[Path, Path],
) -> None:
    # 0.1 + 0.2 - 0.3 is 5.55e-17 in floating point, not zero: a strategy that
    # has sold everything it bought can still show a remainder far below a
    # unit, and that remainder must not block a check run by hand for ever.
    account, fills = logs
    _sent(account, "1", "alpha")
    _sent(account, "2", "alpha")
    _sent(account, "3", "alpha", Side.SELL)
    _filled(fills, "1", 0.1)
    _filled(fills, "2", 0.2)
    _filled(fills, "3", 0.3, Side.SELL)

    ledger = _ledger(account, fills)

    assert ledger.held("alpha") != 0.0
    assert abs(ledger.held("alpha")) < AGREEMENT_TOLERANCE_UNITS
    assert deployed_holdings(ledger) is None


def _correction(strategy_id: str, units: float) -> Correction:
    return Correction(
        schema_version=1,
        recorded_at="2026-11-19T09:00:00+00:00",
        strategy_id=strategy_id,
        units=units,
        venue_units=8.0,
        recorded_units=10.0,
        snapshot_at="2026-11-18T17:40:00+00:00",
    )


def test_a_correction_gives_its_units_to_the_strategy_named(logs: tuple[Path, Path]) -> None:
    # The records say alpha holds 10; the venue holds 8 after 2 were sold by
    # hand. A correction of -2 to alpha leaves it 8, which agrees.
    account, fills = logs
    corrections = account.parent / "corrections-live.jsonl"
    _sent(account, "1", "alpha")
    _sent(account, "2", "beta")
    _filled(fills, "1", 10.0)
    _filled(fills, "2", 3.0)
    assert disagreement_with(_ledger(account, fills), 11.0, "VUAG.L") is not None

    write_correction(_correction("alpha", -2.0), corrections)
    ledger = _ledger(account, fills)

    assert ledger.units == {"alpha": 8.0, "beta": 3.0}
    assert disagreement_with(ledger, 11.0, "VUAG.L") is None
    assert read_corrections(corrections) == [_correction("alpha", -2.0)]


def test_a_correction_can_give_units_to_a_strategy_that_had_none(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    corrections = account.parent / "corrections-live.jsonl"
    write_correction(_correction("gamma", 1.5), corrections)

    assert _ledger(account, fills).units == {"gamma": 1.5}


def test_the_venues_units_are_compared_as_given() -> None:
    ledger = Ledger(units={"alpha": 4.0})

    assert disagreement_with(ledger, 4.0, "VUAG.L") is None
    said = disagreement_with(ledger, 6.5, "VUAG.L")
    assert said is not None and "6.5" in said and "4" in said


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"strategy_id": None},
        {"recorded_at": "2026-11-19T09:00:00"},
        {"recorded_at": "soon"},
        {"units": 0},
        {"units": "2"},
        {"units": float("inf")},
        {"units": True},
        {"venue_units": None},
        {"recorded_units": float("nan")},
    ],
)
def test_a_line_that_is_not_a_correction_is_refused(
    logs: tuple[Path, Path], change: dict[str, object]
) -> None:
    account, fills = logs
    corrections = account.parent / "corrections-live.jsonl"
    line = {**asdict(_correction("alpha", -2.0)), **change}
    corrections.write_text(json.dumps(line) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not a correction"):
        _ledger(account, fills)

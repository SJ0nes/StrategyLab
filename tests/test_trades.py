"""Tests for a strategy's own trades: pairing its fills and chaining their returns.

Expected returns are worked by hand from the prices in each test, never copied
from what the code printed. Prices vary from test to test, and one test quotes
the same trade in pence, because a return that is a ratio of two prices must not
depend on the unit they are quoted in.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from strategylab.core.types import Side
from strategylab.execution.venue import Balance, Charge, Fill, Order, OrderId, OrderType
from strategylab.live.account import account_record, write_account
from strategylab.live.fills import fill_record, write_fill
from strategylab.live.ledger import AGREEMENT_TOLERANCE_UNITS
from strategylab.live.trades import OwnFill, own_fills, own_trades

DEPLOYED = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)


@pytest.fixture
def logs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "account-live.jsonl", tmp_path / "fills-live.jsonl"


def _sent(
    log: Path,
    order_id: str,
    strategy_id: str | None,
    side: Side = Side.BUY,
    days: int = 1,
) -> None:
    order = Order(
        ticker="VUAG.L", side=side, quantity=1.0, order_type=OrderType.MARKET, client_ref="r"
    )
    write_account(
        account_record(
            recorded_at=DEPLOYED + timedelta(days=days),
            bar_date=date(2026, 11, 2),
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


def _filled(
    log: Path,
    order_id: str,
    quantity: float,
    price: float,
    side: Side = Side.BUY,
    charges: tuple[Charge, ...] = (),
) -> None:
    moment = DEPLOYED + timedelta(days=3)
    fill = Fill(
        order_id=OrderId(order_id),
        ticker="VUAG.L",
        side=side,
        quantity=quantity,
        price=price,
        currency="GBP",
        filled_at=moment,
        charges=charges,
    )
    write_fill(fill_record(fill, moment), log)


def _fill(side: str, quantity: float, price: float) -> OwnFill:
    return OwnFill(
        order_id=f"{side}-{price}",
        side=side,
        quantity=quantity,
        price=price,
        currency="GBP",
        filled_at=None,
        charges=(),
    )


# --------------------------------------------------------------------------
# Pairing and chaining.
# --------------------------------------------------------------------------


def test_one_trade_returns_its_sale_over_its_purchase() -> None:
    # 110 / 100 - 1 = 10%.
    trades = own_trades([_fill("buy", 10, 100.0), _fill("sell", 10, 110.0)])

    assert trades.return_pct == pytest.approx(10.0)
    assert (trades.closed, trades.wins, trades.open, trades.why_missing) == (1, 1, None, None)


def test_the_same_trade_quoted_in_pence_returns_the_same() -> None:
    trades = own_trades([_fill("buy", 10, 10000.0), _fill("sell", 10, 11000.0)])

    assert trades.return_pct == pytest.approx(10.0)


def test_trades_are_chained_not_added() -> None:
    # 200 / 250 = 0.8, then 100 / 80 = 1.25; 0.8 * 1.25 = 1.0, so 0%. Adding
    # the two, -20% + 25%, would say +5%.
    trades = own_trades(
        [
            _fill("buy", 2, 250.0),
            _fill("sell", 2, 200.0),
            _fill("buy", 5, 80.0),
            _fill("sell", 5, 100.0),
        ]
    )

    assert trades.return_pct == pytest.approx(0.0, abs=1e-12)
    assert (trades.closed, trades.wins) == (2, 1)


def test_two_gains_compound() -> None:
    # 5.00 / 4.00 = 1.25 and 60 / 50 = 1.2; 1.25 * 1.2 = 1.5, so +50%.
    trades = own_trades(
        [
            _fill("buy", 3, 4.0),
            _fill("sell", 3, 5.0),
            _fill("buy", 0.5, 50.0),
            _fill("sell", 0.5, 60.0),
        ]
    )

    assert trades.return_pct == pytest.approx(50.0)
    assert trades.wins == 2


def test_an_open_trade_is_reported_and_adds_nothing() -> None:
    # Only the closed trade counts: 90 / 120 - 1 = -25%.
    bought = _fill("buy", 4, 75.0)
    trades = own_trades([_fill("buy", 1, 120.0), _fill("sell", 1, 90.0), bought])

    assert trades.return_pct == pytest.approx(-25.0)
    assert (trades.closed, trades.wins, trades.open) == (1, 0, bought)


def test_no_closed_trade_gives_no_return_and_says_why() -> None:
    bought = _fill("buy", 4, 75.0)

    nothing = own_trades([])
    waiting = own_trades([bought])

    assert (nothing.return_pct, nothing.why_missing) == (None, "no trade has closed yet")
    assert waiting.return_pct is None
    assert waiting.why_missing == "no trade has closed yet; one is open"
    assert waiting.open == bought


@pytest.mark.parametrize(
    ("fills", "why"),
    [
        ([_fill("sell", 1, 100.0)], "a sale comes before any purchase"),
        ([_fill("buy", 1, 100.0), _fill("buy", 1, 101.0)], "two purchases follow each other"),
        (
            [_fill("buy", 2, 100.0), _fill("sell", 1, 101.0)],
            "a sale of 1 units closed a purchase of 2",
        ),
    ],
)
def test_fills_that_do_not_alternate_give_no_return(fills: list[OwnFill], why: str) -> None:
    trades = own_trades([_fill("buy", 1, 50.0), _fill("sell", 1, 60.0), *fills])

    assert trades.return_pct is None
    assert trades.why_missing is not None and why in trades.why_missing


def test_a_sale_within_the_tolerance_of_its_purchase_closes_it() -> None:
    quantity = 0.3
    trades = own_trades(
        [
            _fill("buy", quantity, 100.0),
            _fill("sell", quantity + AGREEMENT_TOLERANCE_UNITS / 2, 120.0),
        ]
    )

    assert trades.return_pct == pytest.approx(20.0)


# --------------------------------------------------------------------------
# Reading the strategy's own fills from the logs.
# --------------------------------------------------------------------------


def test_only_the_strategys_own_filled_orders_since_deploying_are_read(
    logs: tuple[Path, Path],
) -> None:
    account, fills = logs
    _sent(account, "before", "alpha", days=-1)
    _sent(account, "1", "alpha")
    _sent(account, "other", "beta")
    _sent(account, "hand", None)
    _sent(account, "2", "alpha", Side.SELL, days=2)
    _sent(account, "unfilled", "alpha", days=3)
    _filled(fills, "before", 1, 10.0)
    _filled(fills, "other", 1, 10.0)
    _filled(fills, "hand", 1, 10.0)
    _filled(fills, "2", 2, 66.0, Side.SELL)
    _filled(fills, "1", 2, 55.0)

    read = own_fills(account, fills, "alpha", DEPLOYED)

    assert [(fill.order_id, fill.side, fill.quantity, fill.price) for fill in read] == [
        ("1", "buy", 2.0, 55.0),
        ("2", "sell", 2.0, 66.0),
    ]
    # 66 / 55 - 1 = 20%.
    assert own_trades(read).return_pct == pytest.approx(20.0)


def test_an_order_filled_twice_in_the_log_is_read_once_at_its_first_price(
    logs: tuple[Path, Path],
) -> None:
    account, fills = logs
    _sent(account, "1", "alpha")
    _filled(fills, "1", 1, 40.0)
    _filled(fills, "1", 1, 99.0)

    assert [fill.price for fill in own_fills(account, fills, "alpha", DEPLOYED)] == [40.0]


def test_only_non_zero_charges_are_kept(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "alpha")
    _filled(
        fills,
        "1",
        1,
        40.0,
        charges=(Charge("FX_FEE", 0.0, "GBP"), Charge("STAMP_DUTY_RESERVE_TAX", -0.2, "GBP")),
    )

    read = own_fills(account, fills, "alpha", DEPLOYED)

    assert read[0].charges == (
        {"name": "STAMP_DUTY_RESERVE_TAX", "amount": -0.2, "currency": "GBP"},
    )
    assert own_trades(read).charges == read[0].charges


def _raw(path: Path, line: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


@pytest.mark.parametrize("price", [0, -1.0, float("inf"), "12", True, None])
def test_a_fill_whose_price_is_not_a_positive_number_is_refused(
    logs: tuple[Path, Path], price: object
) -> None:
    account, fills = logs
    _sent(account, "1", "alpha")
    _raw(fills, {"venue_order_id": "1", "side": "buy", "quantity": 1.0, "price": price})

    with pytest.raises(ValueError, match="price is not a positive number"):
        own_fills(account, fills, "alpha", DEPLOYED)


def test_a_fill_with_an_unknown_side_is_refused(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _sent(account, "1", "alpha")
    _raw(fills, {"venue_order_id": "1", "side": "short", "quantity": 1.0, "price": 10.0})

    with pytest.raises(ValueError, match="side is 'short'"):
        own_fills(account, fills, "alpha", DEPLOYED)


def test_a_sent_order_of_the_strategy_with_no_time_is_refused(logs: tuple[Path, Path]) -> None:
    account, fills = logs
    _raw(
        account,
        {
            "order_outcome": "sent",
            "venue_order_id": "1",
            "strategy_id": "alpha",
            "recorded_at": "2026-11-03T18:00:00",
        },
    )
    _sent(account, "2", "beta")
    _filled(fills, "2", 1, 30.0)

    with pytest.raises(ValueError, match="no time it was recorded"):
        own_fills(account, fills, "alpha", DEPLOYED)
    # Another strategy's line without a time does not stop this one being read.
    assert [fill.order_id for fill in own_fills(account, fills, "beta", DEPLOYED)] == ["2"]

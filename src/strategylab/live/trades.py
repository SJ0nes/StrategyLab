"""One deployed strategy's own trades, and the return they add up to.

With several strategies deployed the account's equity is all of theirs
together, so the account's return says nothing about any one of them
(``docs/decisions/0022``). This module works a strategy's own return out of
its own fills instead (``docs/decisions/0023``).

WHOSE ORDER IS WHOSE is read where the ledger reads it,
:func:`~strategylab.live.ledger.sent_orders`, so the two never disagree about
which strategy sent an order.

A TRADE is a filled buy and the filled sell that closes it, of the same units.
The daily check is long-only and holds one position, so a strategy's fills
alternate: buy, sell, buy, sell. A trade's return is the sell's price over the
buy's, less one. The trades are chained: ``(1 + r1) * (1 + r2) * ... - 1``. As
both prices are the same instrument's, from the same venue, the ratio does not
depend on whether the venue quotes in pounds or pence, nor on how large the
strategy's share of the account is.

ONLY CLOSED TRADES COUNT. No price for an open position is recorded anywhere
this project writes, so an open trade is reported as open, with what it was
bought at, and adds nothing to the return. The backtest's ``trade_count``
leaves an open position out for the same reason.

THE RETURN IS BEFORE CHARGES. The venue reports fees and taxes against a fill
with a sign its documentation does not give and no real reply has shown
(:class:`~strategylab.execution.venue.Charge`), so nothing here subtracts them.
Every non-zero charge on a counted fill is listed beside the return instead.

A CORRECTION (``docs/decisions/0024``) changes a strategy's units without a
price. One that takes units from an open trade leaves the trade open with fewer
units: the price ratio does not depend on how many. A trade that a correction
opens, adds units to, or closes has a price missing for some of its units, so
it is left out of the return and counted in ``left_out``; the trades after it
still count.

When the fills do not alternate - a sale with no purchase before it, two
purchases in a row, or a sale of a different number of units than were bought
- no return is given, and the reason says which. A guess at the pairing could
print a plausible wrong figure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from strategylab.live.fills import read_lines
from strategylab.live.ledger import (
    AGREEMENT_TOLERANCE_UNITS,
    fill_quantity,
    read_corrections,
    sent_orders,
)


@dataclass(frozen=True)
class OwnFill:
    """One of a strategy's filled orders, or a correction to its units.

    Attributes:
        order_id: The venue's order id; for a correction, ``correction``.
        side: ``buy``, ``sell``, or ``correction``.
        quantity: Units filled, above zero; for a correction, the units it
            added, below zero when it took units away.
        price: Price per unit, as the venue reported it, in the instrument's
            quoted unit; ``None`` for a correction, or for an open trade a
            correction has added units to, as no price for them is known.
        currency: The currency the venue named for the fill.
        filled_at: When the venue filled it, ISO-8601 text as recorded.
        charges: The fill's non-zero charges, each ``{"name", "amount",
            "currency"}`` with the venue's sign.
        at: When the order was sent, or the correction made; ``None`` when
            built by hand.
    """

    order_id: str
    side: str
    quantity: float
    price: float | None
    currency: str | None
    filled_at: str | None
    charges: tuple[dict[str, Any], ...]
    at: datetime | None = None


@dataclass(frozen=True)
class OwnTrades:
    """A strategy's own trades since it was deployed.

    Attributes:
        return_pct: The closed trades' returns chained, as a PERCENTAGE,
            before charges; ``None`` when no trade has closed or the fills
            cannot be paired, and ``why_missing`` says which.
        closed: How many trades have closed.
        wins: How many of them sold above their purchase price.
        open: The purchase of a trade not yet closed, or ``None``.
        charges: Every non-zero charge on the fills counted, with the
            venue's sign.
        why_missing: Why ``return_pct`` is ``None``; ``None`` when it is not.
        left_out: How many trades ended that a correction opened, added
            units to, or closed, and so were not counted.
    """

    return_pct: float | None
    closed: int
    wins: int
    open: OwnFill | None
    charges: tuple[dict[str, Any], ...]
    why_missing: str | None
    left_out: int = 0


def _instant(text: str | None, path: Path) -> datetime:
    """Read a sent line's time, refusing one without an offset.

    Args:
        text: The line's ``recorded_at``.
        path: The log, named in the error.

    Returns:
        The instant.

    Raises:
        ValueError: If the text is missing, not ISO-8601, or has no offset.
    """
    try:
        moment = datetime.fromisoformat(text or "")
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise ValueError(f"{path.as_posix()} holds a sent order with no time it was recorded")
    return moment


def _charges(entry: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Keep a fill line's charges whose amount is a non-zero number.

    Args:
        entry: One line of the fills log.

    Returns:
        The charges, as recorded.
    """
    raw = entry.get("charges")
    if not isinstance(raw, list):
        return ()
    kept = []
    for charge in raw:
        amount = charge.get("amount") if isinstance(charge, dict) else None
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount != 0:
            kept.append(charge)
    return tuple(kept)


def own_fills(
    account_log: Path,
    fills_log: Path,
    corrections_log: Path,
    strategy_id: str,
    since: datetime,
) -> list[OwnFill]:
    """Read a strategy's filled orders and corrections since a moment, in time order.

    Args:
        account_log: ``state/account-live.jsonl``.
        fills_log: ``state/fills-live.jsonl``.
        corrections_log: ``state/corrections-live.jsonl``.
        strategy_id: The strategy.
        since: When it was deployed, timezone-aware. Orders sent, and
            corrections made, before it belong to an earlier deployment and
            are left out.

    Returns:
        One entry per order that has a recorded fill, and one per correction,
        ordered by when each order was sent or correction made. An order with
        no fill yet is left out: it has not changed what the strategy holds.

    Raises:
        ValueError: If a line of any log is not a JSON object; one of the
            strategy's sent lines has no time with an offset; a fill of one of
            its orders has a side other than ``buy`` or ``sell``, or a
            quantity or price that is not a positive number; or a correction
            line is damaged.
        OSError: If a log exists and cannot be read.
    """
    mine = [
        (order_id, _instant(sent.recorded_at, account_log))
        for order_id, sent in sent_orders(account_log).items()
        if sent.strategy_id == strategy_id and _instant(sent.recorded_at, account_log) >= since
    ]
    first: dict[str, dict[str, Any]] = {}
    for entry in read_lines(fills_log):
        first.setdefault(str(entry.get("venue_order_id")), entry)
    fills: list[OwnFill] = []
    for order_id, sent_at in mine:
        if order_id not in first:
            continue
        entry = first[order_id]
        side = entry.get("side")
        if side not in ("buy", "sell"):
            raise ValueError(f"{fills_log.as_posix()} holds a fill whose side is {side!r}")
        price = entry.get("price")
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
        ):
            raise ValueError(
                f"{fills_log.as_posix()} holds a fill whose price is not a positive number"
            )
        currency = entry.get("currency")
        filled_at = entry.get("filled_at")
        fills.append(
            OwnFill(
                order_id=order_id,
                side=str(side),
                quantity=fill_quantity(entry, fills_log),
                price=float(price),
                currency=currency if isinstance(currency, str) else None,
                filled_at=filled_at if isinstance(filled_at, str) else None,
                charges=_charges(entry),
                at=sent_at,
            )
        )
    for correction in read_corrections(corrections_log):
        made = datetime.fromisoformat(correction.recorded_at)
        if correction.strategy_id == strategy_id and made >= since:
            fills.append(
                OwnFill(
                    order_id="correction",
                    side="correction",
                    quantity=correction.units,
                    price=None,
                    currency=None,
                    filled_at=None,
                    charges=(),
                    at=made,
                )
            )
    # Stable, so orders sent at the same instant keep the order they were sent in.
    return sorted(fills, key=lambda fill: fill.at or since)


def own_trades(fills: list[OwnFill]) -> OwnTrades:
    """Pair a strategy's fills into trades and chain the closed ones' returns.

    Args:
        fills: As :func:`own_fills` returns them, in the order sent.

    Returns:
        The trades. See :class:`OwnTrades`.
    """
    charges = tuple(charge for fill in fills for charge in fill.charges)
    growth = 1.0
    closed = wins = left_out = 0
    holding: OwnFill | None = None
    for fill in fills:
        why: str | None = None
        if fill.side == "correction":
            if holding is None:
                if fill.quantity > AGREEMENT_TOLERANCE_UNITS:
                    holding = fill
                else:
                    why = "a correction took units from the strategy while it held none"
            elif holding.quantity + fill.quantity <= AGREEMENT_TOLERANCE_UNITS:
                left_out += 1
                holding = None
            else:
                holding = replace(
                    holding,
                    quantity=holding.quantity + fill.quantity,
                    price=None if fill.quantity > 0 else holding.price,
                )
        elif fill.side == "buy":
            if holding is not None and holding.price is None:
                # Units a correction gave it are still held: the trade goes on,
                # with a price missing for some of its units, and is left out.
                holding = replace(fill, quantity=holding.quantity + fill.quantity, price=None)
            else:
                if holding is not None:
                    why = "two purchases follow each other with no sale between them"
                holding = fill
        elif holding is None:
            why = (
                "a sale comes before any purchase since deploying, so what it sold was "
                "bought before"
            )
        elif abs(fill.quantity - holding.quantity) > AGREEMENT_TOLERANCE_UNITS:
            why = (
                f"a sale of {fill.quantity:g} units closed a purchase of "
                f"{holding.quantity:g}, so the two cannot be paired as one trade"
            )
        elif holding.price is None or fill.price is None:
            left_out += 1
            holding = None
        else:
            ratio = fill.price / holding.price
            growth *= ratio
            closed += 1
            wins += ratio > 1
            holding = None
        if why is not None:
            return OwnTrades(None, closed, wins, None, charges, why, left_out)
    if closed == 0:
        why = "no trade has closed yet" + ("; one is open" if holding is not None else "")
        if left_out:
            why = f"no trade has closed without a correction; {left_out} left out"
        return OwnTrades(None, 0, 0, holding, charges, why, left_out)
    return OwnTrades((growth - 1) * 100, closed, wins, holding, charges, None, left_out)

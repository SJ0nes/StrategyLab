"""The venue interface: the boundary between this system and a broker.

Every adapter implements the same structural interface, and each adapter ships
with a test asserting that a simulated venue and a real one behave identically
at this boundary. Where they cannot, the difference is the finding. No adapter
is implemented yet, so no such test exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import NewType, Protocol

from strategylab.core.types import Side

OrderId = NewType("OrderId", str)


class OrderType(Enum):
    """How an order is to be executed.

    Only ``MARKET`` is supported. A limit order that does not fill changes the
    strategy's realised behaviour in a way the backtester does not model, so
    supporting one would mean the simulation and the live worker had quietly
    stopped describing the same thing.
    """

    MARKET = "market"


@dataclass(frozen=True)
class Order:
    """An instruction to transact, after risk has approved it.

    Attributes:
        ticker: Venue symbol to trade, which is the protocol's EXECUTION
            instrument, not its research instrument.
        side: Direction to transact.
        quantity: Units to transact. Positive; direction is carried by ``side``.
        order_type: How to execute.
        client_ref: Caller-generated reference, unique per intended order. The
            T212 order endpoint is not idempotent, so this is what lets a
            timed-out request be resolved by query rather than by resend.
    """

    ticker: str
    side: Side
    quantity: float
    order_type: OrderType
    client_ref: str


@dataclass(frozen=True)
class Position:
    """A holding as the venue reports it.

    Attributes:
        ticker: Venue symbol held.
        quantity: Units held. Negative if short.
        average_price: Average entry price in the instrument's currency.
        market_value: Current value in account currency units.
    """

    ticker: str
    quantity: float
    average_price: float
    market_value: float


@dataclass(frozen=True)
class Balance:
    """Account cash and equity as the venue reports them.

    Attributes:
        currency: ISO-4217 code of the account currency, such as ``GBP``.
        cash: Uninvested cash in account currency units.
        equity: Total account value including positions, in account currency
            units.
    """

    currency: str
    cash: float
    equity: float


@dataclass(frozen=True)
class ReconcileReport:
    """The difference between what this system believed and what the venue holds.

    Attributes:
        checked_at: UTC timestamp of the reconciliation.
        venue_positions: What the venue reports holding.
        local_positions: What this system believed was held.
        discrepancies: Plain-English description of each mismatch, empty when
            local state already matched.
        repaired: Whether local state was rewritten to match the venue. The
            venue is always authoritative; this system's belief is a cache of
            it, and a cache that disagrees with its source is simply wrong.
    """

    checked_at: datetime
    venue_positions: tuple[Position, ...]
    local_positions: tuple[Position, ...]
    discrepancies: tuple[str, ...]
    repaired: bool


class Venue(Protocol):
    """A broker this system can transact through.

    Implementations differ in transport and symbology, never in semantics. A
    method that means something different on one venue than another belongs
    outside this interface.
    """

    def place_order(self, order: Order) -> OrderId:
        """Submit an order and return the venue's identifier for it.

        Args:
            order: The order to submit, already approved by the risk layer.

        Returns:
            The venue's identifier for the submitted order.
        """
        ...

    def cancel(self, order_id: OrderId) -> None:
        """Cancel a working order.

        Args:
            order_id: Venue identifier of the order to cancel.

        Returns:
            None.
        """
        ...

    def get_positions(self) -> list[Position]:
        """Return every position the venue reports holding.

        Returns:
            The current positions, empty when flat.
        """
        ...

    def get_balance(self) -> Balance:
        """Return the account's cash and equity as the venue reports them.

        Returns:
            The current balance.
        """
        ...

    def reconcile(self) -> ReconcileReport:
        """Ask the venue what it ACTUALLY holds and repair local state to match.

        Runs on every startup. Local state is a cache of the venue's truth, and
        a process that restarts after a crash cannot assume its cache survived
        the event that killed it.

        Returns:
            What differed and whether local state was repaired.
        """
        ...

"""The venue interface: the boundary between this system and a broker.

Every adapter implements the same structural interface. The intent was that
each adapter ship with a test asserting a simulated venue and a real one behave
identically at this boundary; one adapter exists (``t212.py``), no simulated
venue does, and so no such test exists. What ``tests/test_t212.py`` asserts is
the adapter's side of the contract against a mock transport.

SYMBOLS AT THIS BOUNDARY ARE THE PROTOCOL'S. ``Order.ticker`` and
``Position.ticker`` carry the symbol as ``protocol.yaml`` names it; translating
to and from a venue's own spelling is the adapter's job and stays inside it.
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
        ticker: The protocol's EXECUTION instrument, as the protocol spells it
            - not its research instrument, and not the venue's own spelling,
            which the adapter applies on the way out.
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
        ticker: The symbol held, translated back to the protocol's spelling
            by the adapter where it knows the mapping.
        quantity: Units held. Negative if short.
        average_price: Average entry price in the instrument's currency.
        market_value: Current value, in the INSTRUMENT's currency as the T212
            adapter derives it - equal to account currency units only when the
            two coincide, which for this project's instrument on a GBP account
            they do, unverified. The risk layer reads it as account currency;
            ``LIMITATIONS.md`` records the gap.
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
class Charge:
    """One fee or tax the venue reports against a fill, as it reports it.

    Attributes:
        name: The venue's name for it, such as ``STAMP_DUTY_RESERVE_TAX``.
        amount: The amount, in ``currency`` units, with the venue's sign. The
            sign convention is not documented and has not been seen in a real
            reply, so nothing here adds charges up or nets them off a price.
        currency: ISO-4217 code of ``amount``.
    """

    name: str
    amount: float
    currency: str


@dataclass(frozen=True)
class Fill:
    """An order the venue reports as filled, wholly or in part.

    Attributes:
        order_id: The venue's identifier of the order, as ``place_order``
            returned it.
        ticker: The symbol, translated back to the protocol's spelling by the
            adapter where it knows the mapping.
        side: Direction of the order.
        quantity: Units filled. Positive; direction is carried by ``side``.
        price: Price per unit filled at, in ``currency`` units.
        currency: ISO-4217 code of the instrument's currency, which is
            ``price``'s.
        filled_at: When the fill happened, timezone-aware.
        charges: Fees and taxes as the venue reports them, uninterpreted.
    """

    order_id: OrderId
    ticker: str
    side: Side
    quantity: float
    price: float
    currency: str
    filled_at: datetime
    charges: tuple[Charge, ...]


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

    def get_fills(self, ticker: str) -> list[Fill]:
        """Return the newest fills the venue reports for one instrument.

        Args:
            ticker: The symbol as the protocol names it.

        Returns:
            The fills the venue's most recent page of order history holds for
            ``ticker``, newest first; an order that has not filled has none.
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

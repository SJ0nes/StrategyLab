"""What the account looked like at each daily check, and what became of its order.

The decision log (:mod:`strategylab.logging.decisions`) records what the
strategy decided and what the risk layer answered. It records nothing about
the account: no equity, no position, and not whether the order the decision
asked for was sent. A strategy's return on the practice account cannot be
worked out from it. This log is the other half.

ONE LINE PER CHECK, appended to ``state/account-live.jsonl`` by
``strategylab run`` once it knows how the check ended. A check that fails
before it reaches the venue writes nothing here; one that reaches the venue
writes the account as the venue reported it, and what happened to its order:

- ``none`` - no order was asked for, or the risk layer allowed nothing;
- ``sent`` - the venue accepted it, with the id it gave;
- ``failed`` - the venue refused it or could not be reached.

``sent`` is what the venue answered, not a fill: the order is a market order,
and this log does not learn the price it filled at.

Every field is in the account's currency, as :class:`Balance` reports it, except
``position_quantity``, which is units of the instrument.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from strategylab.execution.venue import Balance, Order, Position

#: Where ``run`` appends, relative to the working directory.
DEFAULT_ACCOUNT_LOG_PATH = Path("state/account-live.jsonl")

#: Schema version of one line.
ACCOUNT_SCHEMA_VERSION = 1

#: Every way a check's order can end.
ORDER_OUTCOMES = frozenset({"none", "sent", "failed"})


@dataclass(frozen=True)
class AccountRecord:
    """The account at one daily check.

    Attributes:
        schema_version: :data:`ACCOUNT_SCHEMA_VERSION`.
        recorded_at: When the check ended, ISO-8601 with an offset.
        bar_date: The trading day the check decided on, ISO-8601.
        strategy: The strategy's name.
        params: The version traded, parameter name to bars.
        currency: The account currency, such as ``GBP``.
        equity: Account value including positions, in account currency.
        cash: Uninvested cash, in account currency.
        ticker: The instrument traded.
        position_quantity: Units of ``ticker`` held when the check began.
        order_side: ``buy`` or ``sell``, or ``None`` when no order was asked for.
        order_quantity: Units the order asked for, or ``None``.
        order_outcome: One of :data:`ORDER_OUTCOMES`.
        venue_order_id: The venue's id for a sent order, else ``None``.
    """

    schema_version: int
    recorded_at: str
    bar_date: str
    strategy: str
    params: dict[str, int]
    currency: str
    equity: float
    cash: float
    ticker: str
    position_quantity: float
    order_side: str | None
    order_quantity: float | None
    order_outcome: str
    venue_order_id: str | None


def account_record(
    *,
    recorded_at: datetime,
    bar_date: date,
    strategy: str,
    params: dict[str, int],
    balance: Balance,
    positions: list[Position],
    ticker: str,
    order: Order | None,
    outcome: str,
    venue_order_id: str | None,
) -> AccountRecord:
    """Build the line for one check.

    Args:
        recorded_at: When the check ended. Must carry an offset.
        bar_date: The trading day decided on.
        strategy: The strategy's name.
        params: The version traded, in bars.
        balance: The account as the venue reported it at the start of the check.
        positions: The holdings as the venue reported them.
        ticker: The instrument traded.
        order: The order the decision asked for, or ``None``.
        outcome: How it ended; one of :data:`ORDER_OUTCOMES`.
        venue_order_id: The venue's id when ``outcome`` is ``sent``.

    Returns:
        The record.

    Raises:
        ValueError: If ``recorded_at`` has no offset; ``outcome`` is not in
            :data:`ORDER_OUTCOMES`; ``outcome`` is ``none`` while an order is
            given, or not ``none`` while none is; an id is given for an order
            that was not sent, or missing for one that was; or a money figure
            is not finite.
    """
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at has no offset; a log compared across machines needs one")
    if outcome not in ORDER_OUTCOMES:
        raise ValueError(f"order outcome {outcome!r} is not one of {sorted(ORDER_OUTCOMES)}")
    if (order is None) != (outcome == "none"):
        raise ValueError(
            f"outcome {outcome!r} does not fit {'no' if order is None else 'an'} order"
        )
    if (venue_order_id is not None) != (outcome == "sent"):
        raise ValueError("a venue order id belongs to a sent order and to nothing else")
    for name, value in (("equity", balance.equity), ("cash", balance.cash)):
        if not math.isfinite(value):
            raise ValueError(f"{name} is {value}; the venue reported a figure that is not a number")
    held = sum(position.quantity for position in positions if position.ticker == ticker)
    return AccountRecord(
        schema_version=ACCOUNT_SCHEMA_VERSION,
        recorded_at=recorded_at.isoformat(),
        bar_date=bar_date.isoformat(),
        strategy=strategy,
        params=dict(params),
        currency=balance.currency,
        equity=balance.equity,
        cash=balance.cash,
        ticker=ticker,
        position_quantity=held,
        order_side=None if order is None else order.side.value,
        order_quantity=None if order is None else order.quantity,
        order_outcome=outcome,
        venue_order_id=venue_order_id,
    )


def write_account(entry: AccountRecord, path: Path = DEFAULT_ACCOUNT_LOG_PATH) -> None:
    """Append one record as a line of JSON.

    Args:
        entry: The record.
        path: The log. Created, with its directory, if absent.

    Raises:
        OSError: If the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(entry), sort_keys=True, allow_nan=False)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")

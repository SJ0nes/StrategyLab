"""What each order the daily check sent was filled at.

The account log (:mod:`strategylab.live.account`) records an order as ``sent``
when the venue accepted it. That is not a fill. The check runs after London's
close, so a market order it sends is filled at the next session at the
earliest, and the price is known only to a later check.

SO EACH CHECK LOOKS BACK. Before it decides, ``strategylab run`` asks the venue
for its recent fills of the traded instrument, and appends one line to
``state/fills-live.jsonl`` for every order the account log says was sent and
this log does not yet hold. An order with no fill yet is looked for again at
the next check. Nothing here feeds the decision: a fill price is a record of
what happened, and live data never selects anything.

APPEND-ONLY, ONE LINE PER ORDER. No earlier line of either log is rewritten.
An order the venue reports in more than one fill is recorded by one only,
the one the venue lists first, which is its newest; the daily check's orders
are small market orders, and whether the venue ever splits one has not been
seen.

``price`` is per unit in the instrument's currency, as the venue reports it.
``charges`` are the fees and taxes the venue reports against the fill, with the
venue's sign, which its documentation does not give; nothing here adds them up.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from strategylab.execution.venue import Fill, Venue

#: Where ``run`` appends, relative to the working directory.
DEFAULT_FILLS_LOG_PATH = Path("state/fills-live.jsonl")

#: Schema version of one line.
FILLS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FillRecord:
    """One sent order's fill, as the venue reported it.

    Attributes:
        schema_version: :data:`FILLS_SCHEMA_VERSION`.
        recorded_at: When this check learned of the fill, ISO-8601 with an
            offset.
        venue_order_id: The id the venue gave the order when it was sent, as
            the account log holds it.
        ticker: The instrument, as the protocol names it.
        side: ``buy`` or ``sell``.
        quantity: Units filled.
        price: Price per unit, in ``currency``.
        currency: The instrument's currency, ISO-4217.
        filled_at: When the venue filled it, ISO-8601 with an offset.
        charges: Each fee or tax as ``{"name", "amount", "currency"}``, with
            the venue's sign.
    """

    schema_version: int
    recorded_at: str
    venue_order_id: str
    ticker: str
    side: str
    quantity: float
    price: float
    currency: str
    filled_at: str
    charges: list[dict[str, Any]]


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Read a JSON Lines log, every line an object.

    Args:
        path: The log. Absent means empty.

    Returns:
        Its lines, oldest first.

    Raises:
        ValueError: If a line is not a JSON object.
        OSError: If the file exists and cannot be read.
    """
    if not path.exists():
        return []
    entries = []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        try:
            entry = json.loads(text)
        except ValueError as exc:
            raise ValueError(f"{path.as_posix()} line {number} is not JSON: {exc}") from None
        if not isinstance(entry, dict):
            raise ValueError(f"{path.as_posix()} line {number} is not a JSON object")
        entries.append(entry)
    return entries


def unfilled_orders(account_log: Path, fills_log: Path) -> list[str]:
    """List the sent orders whose fill is not yet recorded.

    Args:
        account_log: The account log, ``state/account-live.jsonl``.
        fills_log: This module's log, ``state/fills-live.jsonl``.

    Returns:
        The venue order ids of every ``sent`` line in the account log with no
        line in the fills log, oldest first, each once.

    Raises:
        ValueError: If either log holds a line that is not a JSON object.
        OSError: If either exists and cannot be read.
    """
    recorded = {str(entry.get("venue_order_id")) for entry in read_lines(fills_log)}
    pending: list[str] = []
    for entry in read_lines(account_log):
        order_id = entry.get("venue_order_id")
        if entry.get("order_outcome") != "sent" or order_id is None:
            continue
        if str(order_id) not in recorded and str(order_id) not in pending:
            pending.append(str(order_id))
    return pending


def fill_record(fill: Fill, recorded_at: datetime) -> FillRecord:
    """Build the line for one fill.

    Args:
        fill: The fill, as the venue reported it.
        recorded_at: When this check learned of it. Must carry an offset.

    Returns:
        The record.

    Raises:
        ValueError: If ``recorded_at`` or the fill's time has no offset.
    """
    if recorded_at.tzinfo is None or fill.filled_at.tzinfo is None:
        raise ValueError("a fill's times need an offset; a log compared across machines needs one")
    return FillRecord(
        schema_version=FILLS_SCHEMA_VERSION,
        recorded_at=recorded_at.isoformat(),
        venue_order_id=str(fill.order_id),
        ticker=fill.ticker,
        side=fill.side.value,
        quantity=fill.quantity,
        price=fill.price,
        currency=fill.currency,
        filled_at=fill.filled_at.isoformat(),
        charges=[asdict(charge) for charge in fill.charges],
    )


def write_fill(entry: FillRecord, path: Path = DEFAULT_FILLS_LOG_PATH) -> None:
    """Append one record as a line of JSON.

    Args:
        entry: The record.
        path: The log. Created, with its directory, if absent.

    Raises:
        ValueError: If a figure is not finite, which JSON cannot hold.
        OSError: If the file cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(entry), sort_keys=True, allow_nan=False)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def record_fills(
    venue: Venue,
    ticker: str,
    now: datetime,
    account_log: Path,
    fills_log: Path = DEFAULT_FILLS_LOG_PATH,
) -> list[FillRecord]:
    """Record the fills of sent orders that the fills log does not yet hold.

    Asks the venue nothing when no sent order is waiting for its fill.

    Args:
        venue: The venue the orders were sent to.
        ticker: The instrument traded, as the protocol names it.
        now: The present moment, timezone-aware; each line's ``recorded_at``.
        account_log: The account log.
        fills_log: Where the fills are appended.

    Returns:
        The records appended, in the order the orders were sent.

    Raises:
        ValueError: If a log holds a line that is not a JSON object, or a fill
            carries a figure that is not finite or a time with no offset.
        OSError: If a log cannot be read or written.
        Exception: Whatever ``venue.get_fills`` raises; for Trading 212,
            ``T212Error``.
    """
    pending = unfilled_orders(account_log, fills_log)
    if not pending:
        return []
    first: dict[str, Fill] = {}
    for fill in venue.get_fills(ticker):
        first.setdefault(str(fill.order_id), fill)
    written = []
    for order_id in pending:
        if order_id in first:
            entry = fill_record(first[order_id], now)
            write_fill(entry, fills_log)
            written.append(entry)
    return written

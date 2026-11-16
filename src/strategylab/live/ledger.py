"""How many units of the traded instrument each deployed strategy holds.

Every strategy trades the one execution instrument, and the venue reports one
net position for it (``docs/decisions/0022``). Which units are whose is
therefore this project's to keep. It is not kept in a file of its own: it is
worked out, each time, from two append-only logs that already say it.

- ``state/account-live.jsonl`` says which strategy sent each order: a ``sent``
  line carries the venue's order id and the strategy's ``strategy_id``.
- ``state/fills-live.jsonl`` says what each sent order filled at, and how many
  units.

A strategy holds what its filled buys add up to, less its filled sells. An order
whose fill is not yet recorded counts for nothing, as it has not changed the
venue's position either; the strategy is reported as WAITING, and the daily
check sends it no new order until the fill is known. A line with no strategy id
- written by ``run`` typed by hand, or before strategies had ids - is counted
under ``None``, so the ledgers still add up to what the venue holds.

Units are units of the instrument, never money.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from strategylab.execution.venue import Position
from strategylab.live.fills import read_lines

#: The largest difference, in units, between the ledgers' total and the venue's
#: position that still counts as agreement. Venue quantities are fractional; a
#: millionth of a unit of a fund priced near a hundred pounds is a hundredth of
#: a penny.
AGREEMENT_TOLERANCE_UNITS = 1e-6


@dataclass(frozen=True)
class Ledger:
    """Units held by each strategy, from its own filled orders.

    Attributes:
        units: Strategy id to units held, ``None`` for checks run without one.
            A strategy with no filled order is absent.
        waiting: The strategies with a sent order whose fill is not yet
            recorded.
    """

    units: dict[str | None, float] = field(default_factory=dict)
    waiting: frozenset[str | None] = frozenset()

    def held(self, strategy_id: str | None) -> float:
        """Units a strategy holds.

        Args:
            strategy_id: The strategy's id, or ``None``.

        Returns:
            Its units; zero when it has none.
        """
        return self.units.get(strategy_id, 0.0)

    def total(self) -> float:
        """Units all the strategies hold together.

        Returns:
            The sum, in units.
        """
        return math.fsum(self.units.values())


def _quantity(entry: dict[str, object], path: Path) -> float:
    """Read a fill line's units, refusing anything that is not a positive number."""
    quantity = entry.get("quantity")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, (int, float))
        or not math.isfinite(quantity)
        or quantity <= 0
    ):
        raise ValueError(f"{path.as_posix()} holds a fill whose quantity is not a positive number")
    return float(quantity)


def read_ledger(account_log: Path, fills_log: Path) -> Ledger:
    """Work out each strategy's units from the account and fills logs.

    Args:
        account_log: ``state/account-live.jsonl``.
        fills_log: ``state/fills-live.jsonl``.

    Returns:
        The ledger. Both logs absent means an empty one.

    Raises:
        ValueError: If a line of either log is not a JSON object, or a fill of
            a sent order has a side other than ``buy`` or ``sell`` or a
            quantity that is not a positive number.
        OSError: If a log exists and cannot be read.
    """
    owner: dict[str, str | None] = {}
    for entry in read_lines(account_log):
        order_id = entry.get("venue_order_id")
        if entry.get("order_outcome") != "sent" or order_id is None:
            continue
        strategy_id = entry.get("strategy_id")
        owner.setdefault(str(order_id), None if strategy_id is None else str(strategy_id))
    units: dict[str | None, float] = {}
    filled: set[str] = set()
    for entry in read_lines(fills_log):
        order_id = str(entry.get("venue_order_id"))
        if order_id not in owner or order_id in filled:
            continue
        side = entry.get("side")
        if side not in ("buy", "sell"):
            raise ValueError(f"{fills_log.as_posix()} holds a fill whose side is {side!r}")
        quantity = _quantity(entry, fills_log)
        strategy_id = owner[order_id]
        units[strategy_id] = units.get(strategy_id, 0.0) + (
            quantity if side == "buy" else -quantity
        )
        filled.add(order_id)
    waiting = frozenset(owner[order_id] for order_id in owner if order_id not in filled)
    return Ledger(units=units, waiting=waiting)


def deployed_holdings(ledger: Ledger) -> str | None:
    """Say whether deployed strategies hold or await units a check run by hand must not trade.

    Args:
        ledger: The ledger.

    Returns:
        ``None`` when no strategy with an id holds units or waits for a fill;
        otherwise a sentence naming them.
    """
    named = sorted(
        {
            str(key)
            for key, units in ledger.units.items()
            if key is not None and abs(units) > AGREEMENT_TOLERANCE_UNITS
        }
        | {str(key) for key in ledger.waiting if key is not None}
    )
    if not named:
        return None
    return (
        f"{', '.join(named)} hold or await units of the instrument, and a check run "
        "without --strategy-id would trade them; run it for one strategy instead"
    )


def disagreement(ledger: Ledger, positions: list[Position], ticker: str) -> str | None:
    """Say how the ledgers and the venue disagree about the instrument, if they do.

    Args:
        ledger: The ledger.
        positions: The venue's positions.
        ticker: The instrument, as the protocol names it.

    Returns:
        ``None`` when no strategy holds fewer than zero units and the ledgers'
        total and the venue's position in ``ticker`` differ by no more than
        :data:`AGREEMENT_TOLERANCE_UNITS`; otherwise a sentence saying which.
        A negative holding is checked on its own because it would cancel
        another strategy's units in the total: a check run by hand that sold
        every strategy's units leaves the total agreeing with an empty venue.
    """
    short = sorted(
        str(key) for key, units in ledger.units.items() if units < -AGREEMENT_TOLERANCE_UNITS
    )
    if short:
        return (
            f"the recorded fills leave {', '.join(short)} holding fewer than zero units of "
            f"{ticker}, which only a sale of another strategy's units explains"
        )
    venue = math.fsum(p.quantity for p in positions if p.ticker == ticker)
    recorded = ledger.total()
    if abs(venue - recorded) <= AGREEMENT_TOLERANCE_UNITS:
        return None
    return (
        f"the venue holds {venue:g} units of {ticker} and the strategies' recorded fills "
        f"add up to {recorded:g}; units bought or sold outside the daily check, or an "
        "order whose fill the venue has not reported, would explain it"
    )

"""How many units of the traded instrument each deployed strategy holds.

Every strategy trades the one execution instrument, and the venue reports one
net position for it (``docs/decisions/0022``). Which units are whose is
therefore this project's to keep. It is not kept in a file of its own: it is
worked out, each time, from append-only logs that already say it.

- ``state/account-live.jsonl`` says which strategy sent each order: a ``sent``
  line carries the venue's order id and the strategy's ``strategy_id``.
- ``state/fills-live.jsonl`` says what each sent order filled at, and how many
  units.
- ``state/corrections-live.jsonl`` holds the operator's corrections: when the
  ledgers and the venue disagree, the operator names the strategy the
  difference belongs to, and a line adds it to that strategy's units
  (``docs/decisions/0024``). No order is sent, and no earlier line changes.

A strategy holds what its filled buys add up to, less its filled sells, plus its
corrections. An order
whose fill is not yet recorded counts for nothing, as it has not changed the
venue's position either; the strategy is reported as WAITING, and the daily
check sends it no new order until the fill is known. A line with no strategy id
- written by ``run`` typed by hand, or before strategies had ids - is counted
under ``None``, so the ledgers still add up to what the venue holds.

Units are units of the instrument, never money.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from strategylab.execution.venue import Position
from strategylab.live.fills import read_lines

#: Where corrections are appended, relative to the working directory.
DEFAULT_CORRECTIONS_LOG_PATH = Path("state/corrections-live.jsonl")

#: Schema version of one correction line.
CORRECTIONS_SCHEMA_VERSION = 1

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


@dataclass(frozen=True)
class SentOrder:
    """Who sent an order, and when, as the account log's ``sent`` line says.

    Attributes:
        strategy_id: The strategy that sent it, ``None`` for a check run
            without one.
        recorded_at: The line's ``recorded_at``, ISO-8601 text as written,
            or ``None`` when the line has none.
    """

    strategy_id: str | None
    recorded_at: str | None


def sent_orders(account_log: Path) -> dict[str, SentOrder]:
    """Read which strategy sent each order, in the order the orders were sent.

    The one place an order is given to a strategy: the ledger and each
    strategy's own trades (:mod:`strategylab.live.trades`) both read it here.

    Args:
        account_log: ``state/account-live.jsonl``.

    Returns:
        Venue order id to who sent it. Only ``sent`` lines with an id count;
        an id sent twice belongs to its first line.

    Raises:
        ValueError: If a line is not a JSON object.
        OSError: If the log exists and cannot be read.
    """
    owner: dict[str, SentOrder] = {}
    for entry in read_lines(account_log):
        order_id = entry.get("venue_order_id")
        if entry.get("order_outcome") != "sent" or order_id is None:
            continue
        strategy_id = entry.get("strategy_id")
        recorded_at = entry.get("recorded_at")
        owner.setdefault(
            str(order_id),
            SentOrder(
                strategy_id=None if strategy_id is None else str(strategy_id),
                recorded_at=recorded_at if isinstance(recorded_at, str) else None,
            ),
        )
    return owner


def fill_quantity(entry: dict[str, object], path: Path) -> float:
    """Read a fill line's units, refusing anything that is not a positive number.

    Args:
        entry: One line of the fills log.
        path: The log, named in the error.

    Returns:
        The units filled.

    Raises:
        ValueError: If ``quantity`` is not a finite number above zero.
    """
    quantity = entry.get("quantity")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, (int, float))
        or not math.isfinite(quantity)
        or quantity <= 0
    ):
        raise ValueError(f"{path.as_posix()} holds a fill whose quantity is not a positive number")
    return float(quantity)


@dataclass(frozen=True)
class Correction:
    """One correction: units the operator gave a strategy to settle a disagreement.

    Attributes:
        schema_version: :data:`CORRECTIONS_SCHEMA_VERSION`.
        recorded_at: When it was made, ISO-8601 with an offset.
        strategy_id: The strategy the difference was given to.
        units: Units added to it; below zero takes units away.
        venue_units: The venue's position in the instrument at the check it
            settles, in units.
        recorded_units: What the ledgers added up to then, in units.
        snapshot_at: When that check recorded the venue's position, ISO-8601.
    """

    schema_version: int
    recorded_at: str
    strategy_id: str
    units: float
    venue_units: float
    recorded_units: float
    snapshot_at: str


def _finite(value: object) -> float | None:
    """Read a finite number from a file, refusing booleans.

    Args:
        value: A field as read.

    Returns:
        The number, or ``None`` when it is not a finite number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def read_corrections(path: Path) -> list[Correction]:
    """Read the corrections log.

    Args:
        path: ``state/corrections-live.jsonl``. Absent means none.

    Returns:
        The corrections, oldest first.

    Raises:
        ValueError: If a line is not a JSON object, or is not a correction:
            another schema version, no strategy id, a time without an offset,
            units that are not a finite number other than zero, or totals that
            are not finite numbers.
        OSError: If the log exists and cannot be read.
    """
    corrections = []
    for entry in read_lines(path):
        strategy_id = entry.get("strategy_id")
        units = _finite(entry.get("units"))
        venue_units = _finite(entry.get("venue_units"))
        recorded_units = _finite(entry.get("recorded_units"))
        try:
            moment: datetime | None = datetime.fromisoformat(str(entry.get("recorded_at")))
        except ValueError:
            moment = None
        if (
            entry.get("schema_version") != CORRECTIONS_SCHEMA_VERSION
            or not isinstance(strategy_id, str)
            or moment is None
            or moment.tzinfo is None
            or units is None
            or units == 0
            or venue_units is None
            or recorded_units is None
        ):
            raise ValueError(f"{path.as_posix()} holds a line that is not a correction")
        corrections.append(
            Correction(
                schema_version=CORRECTIONS_SCHEMA_VERSION,
                recorded_at=str(entry["recorded_at"]),
                strategy_id=strategy_id,
                units=units,
                venue_units=venue_units,
                recorded_units=recorded_units,
                snapshot_at=str(entry.get("snapshot_at")),
            )
        )
    return corrections


def write_correction(entry: Correction, path: Path = DEFAULT_CORRECTIONS_LOG_PATH) -> None:
    """Append one correction as a line of JSON.

    Args:
        entry: The correction.
        path: The log; its folder is made if absent.

    Raises:
        OSError: If the line cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(entry)) + "\n")


def read_ledger(account_log: Path, fills_log: Path, corrections_log: Path) -> Ledger:
    """Work out each strategy's units from the account, fills and corrections logs.

    Args:
        account_log: ``state/account-live.jsonl``.
        fills_log: ``state/fills-live.jsonl``.
        corrections_log: ``state/corrections-live.jsonl``. Required, so no
            caller can count units without the operator's corrections.

    Returns:
        The ledger. All logs absent means an empty one.

    Raises:
        ValueError: If a line of any log is not a JSON object; a fill of a
            sent order has a side other than ``buy`` or ``sell`` or a quantity
            that is not a positive number; or a correction line is damaged
            (:func:`read_corrections`).
        OSError: If a log exists and cannot be read.
    """
    owner = {order_id: sent.strategy_id for order_id, sent in sent_orders(account_log).items()}
    units: dict[str | None, float] = {}
    filled: set[str] = set()
    for entry in read_lines(fills_log):
        order_id = str(entry.get("venue_order_id"))
        if order_id not in owner or order_id in filled:
            continue
        side = entry.get("side")
        if side not in ("buy", "sell"):
            raise ValueError(f"{fills_log.as_posix()} holds a fill whose side is {side!r}")
        quantity = fill_quantity(entry, fills_log)
        strategy_id = owner[order_id]
        units[strategy_id] = units.get(strategy_id, 0.0) + (
            quantity if side == "buy" else -quantity
        )
        filled.add(order_id)
    for correction in read_corrections(corrections_log):
        units[correction.strategy_id] = units.get(correction.strategy_id, 0.0) + correction.units
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
    """Say how the ledgers and the venue's positions disagree about the instrument.

    Args:
        ledger: The ledger.
        positions: The venue's positions.
        ticker: The instrument, as the protocol names it.

    Returns:
        As :func:`disagreement_with`, given the venue's units of ``ticker``.
    """
    venue = math.fsum(p.quantity for p in positions if p.ticker == ticker)
    return disagreement_with(ledger, venue, ticker)


def disagreement_with(ledger: Ledger, venue: float, ticker: str) -> str | None:
    """Say how the ledgers and the venue disagree about the instrument, if they do.

    The one statement of the rule: the daily check and the app's settlement
    both read it here.

    Args:
        ledger: The ledger.
        venue: The venue's position in ``ticker``, in units.
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
    recorded = ledger.total()
    if abs(venue - recorded) <= AGREEMENT_TOLERANCE_UNITS:
        return None
    return (
        f"the venue holds {venue:g} units of {ticker} and the strategies' recorded fills "
        f"add up to {recorded:g}; units bought or sold outside the daily check, or an "
        "order whose fill the venue has not reported, would explain it"
    )

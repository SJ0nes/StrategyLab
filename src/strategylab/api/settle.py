"""Settling a disagreement between the strategies' units and the venue's position.

When the units the strategies' fills add up to differ from what the venue holds,
every daily check sends nothing (``docs/decisions/0022``). The operator settles
it by naming the strategy the difference belongs to, and typing its name; a
correction line then gives it the difference (``docs/decisions/0024``). No order
is sent, and no earlier line of any log changes.

THE VENUE'S POSITION IS THE ONE THE LATEST CHECK RECORDED. Every check writes the
venue's units of the instrument to the account log; this module reads the
newest such line and makes no call to the venue. A settlement is offered only
when that line is newer than every fill and correction recorded, no order is
waiting for its fill, and no job is running. Otherwise the ledger may already
count something that the recorded position does not, and the difference shown
would not be the real one; the report says to run a check first.

THE DIFFERENCE IS WORKED OUT AGAIN WHEN IT IS SETTLED. The request carries the
difference the window showed, and is refused if it is not the one found now, so
a second press finds nothing to settle rather than settling twice. The whole
difference goes to one strategy. One that would be left holding fewer than zero
units is refused, and no split between strategies is built.
"""

from __future__ import annotations

import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from strategylab.api.deployment import read_deployment
from strategylab.api.jobs import list_jobs
from strategylab.api.reading import without_root
from strategylab.api.registry import StrategyFile
from strategylab.live.account import DEFAULT_ACCOUNT_LOG_PATH
from strategylab.live.fills import DEFAULT_FILLS_LOG_PATH, read_lines
from strategylab.live.ledger import (
    AGREEMENT_TOLERANCE_UNITS,
    CORRECTIONS_SCHEMA_VERSION,
    DEFAULT_CORRECTIONS_LOG_PATH,
    Correction,
    disagreement_with,
    read_ledger,
    write_correction,
)

#: Held while a correction is worked out and written, so two presses cannot
#: both settle the same difference.
_SETTLING = threading.Lock()


class SettleRefused(Exception):
    """A settlement the project will not make; the message says why."""


def _instant(value: Any) -> datetime | None:
    """Read an ISO-8601 instant with an offset, or ``None``."""
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _snapshot(root: Path) -> tuple[float, datetime, str] | None:
    """Read the venue's position the newest check recorded.

    Args:
        root: The project root.

    Returns:
        The venue's units of the instrument, when that check recorded them,
        and the instrument; ``None`` when no check has recorded one.

    Raises:
        ValueError: If a line of the account log is not a JSON object.
        OSError: If the log exists and cannot be read.
    """
    found = None
    for line in read_lines(root / DEFAULT_ACCOUNT_LOG_PATH):
        units = line.get("position_quantity")
        moment = _instant(line.get("recorded_at"))
        ticker = line.get("ticker")
        if (
            line.get("schema_version") != 1
            or isinstance(units, bool)
            or not isinstance(units, (int, float))
            or not math.isfinite(units)
            or moment is None
            or not isinstance(ticker, str)
        ):
            continue
        if found is None or moment >= found[1]:
            found = (float(units), moment, ticker)
    return found


def _newest(path: Path) -> datetime | None:
    """The newest ``recorded_at`` in a log, or ``None`` when it holds none."""
    times = [moment for line in read_lines(path) if (moment := _instant(line.get("recorded_at")))]
    return max(times) if times else None


def settlement(root: Path, now: datetime) -> dict[str, Any]:
    """Say whether the strategies' units and the venue's position disagree, and how to settle.

    Args:
        root: The project root.
        now: The present moment, timezone-aware; asks whether a job is running.

    Returns:
        ``state`` is ``agree`` when they agree or no check has recorded the
        venue's position; ``unreadable`` when a log cannot be read; ``wait``
        when they disagree but cannot be settled yet; ``settle`` when they can.
        ``why`` says, in words, for every state but ``agree``. With ``wait``
        and ``settle``: ``venue_units``, ``recorded_units`` and
        ``difference`` (the venue's less the recorded, the units a correction
        would give), all in units; ``snapshot_at``, when the check recorded
        the venue's position; and ``ticker``.
    """
    try:
        snapshot = _snapshot(root)
        if snapshot is None:
            return {"state": "agree"}
        venue, snapshot_at, ticker = snapshot
        ledger = read_ledger(
            root / DEFAULT_ACCOUNT_LOG_PATH,
            root / DEFAULT_FILLS_LOG_PATH,
            root / DEFAULT_CORRECTIONS_LOG_PATH,
        )
        newest_fill = _newest(root / DEFAULT_FILLS_LOG_PATH)
        newest_correction = _newest(root / DEFAULT_CORRECTIONS_LOG_PATH)
    except (ValueError, OSError) as exc:
        detail = without_root(str(exc), root)
        return {"state": "unreadable", "why": f"the logs could not be read: {detail}"}
    said = disagreement_with(ledger, venue, ticker)
    if said is None:
        return {"state": "agree"}
    recorded = ledger.total()
    report: dict[str, Any] = {
        "venue_units": venue,
        "recorded_units": recorded,
        "difference": venue - recorded,
        "snapshot_at": snapshot_at.isoformat(),
        "ticker": ticker,
    }
    if any(units < -AGREEMENT_TOLERANCE_UNITS for units in ledger.units.values()):
        wait = f"{said}; settle it by trading in the venue's own app"
    elif ledger.waiting:
        wait = "an order has no recorded fill yet; settle after a check has recorded it"
    elif (newest_fill is not None and newest_fill > snapshot_at) or (
        newest_correction is not None and newest_correction > snapshot_at
    ):
        wait = "something was recorded after the newest check; run a check first"
    elif list_jobs(root, now)["current"] is not None:
        wait = "a job is running; settle once it has ended"
    else:
        return {"state": "settle", "why": said, **report}
    return {"state": "wait", "why": wait, **report}


def settle(
    root: Path, strategy: StrategyFile, typed: str, shown: float, now: datetime
) -> dict[str, Any]:
    """Give the whole difference to one deployed strategy, as a correction.

    Args:
        root: The project root.
        strategy: The strategy the operator named.
        typed: Its name, as the operator typed it.
        shown: The difference the window showed, in units.
        now: The present moment, timezone-aware.

    Returns:
        :func:`settlement` once the correction is written.

    Raises:
        SettleRefused: If the name typed is not the strategy's; it is not
            deployed; there is nothing to settle, or it cannot be settled yet;
            the difference is not the one shown; or the strategy would be left
            holding fewer than zero units.
        OSError: If the correction cannot be written.
    """
    if typed.strip() != strategy.name:
        raise SettleRefused(f"type the strategy's name, {strategy.name}, to settle")
    with _SETTLING:
        deployed = read_deployment(root, strategy.id)
        if deployed is None or not deployed["available"]:
            raise SettleRefused("only a deployed strategy can be given the difference")
        report = settlement(root, now)
        if report["state"] != "settle":
            raise SettleRefused(report.get("why", "there is nothing to settle"))
        difference = float(report["difference"])
        if not math.isfinite(shown) or abs(shown - difference) > AGREEMENT_TOLERANCE_UNITS:
            raise SettleRefused("the difference has changed since it was shown; read it again")
        ledger = read_ledger(
            root / DEFAULT_ACCOUNT_LOG_PATH,
            root / DEFAULT_FILLS_LOG_PATH,
            root / DEFAULT_CORRECTIONS_LOG_PATH,
        )
        held = ledger.held(strategy.id)
        if held + difference < -AGREEMENT_TOLERANCE_UNITS:
            raise SettleRefused(
                f"{strategy.name} holds {held:g} units, and giving it {difference:g} would leave "
                "it holding fewer than zero"
            )
        write_correction(
            Correction(
                schema_version=CORRECTIONS_SCHEMA_VERSION,
                recorded_at=now.isoformat(),
                strategy_id=strategy.id,
                units=difference,
                venue_units=float(report["venue_units"]),
                recorded_units=float(report["recorded_units"]),
                snapshot_at=str(report["snapshot_at"]),
            ),
            root / DEFAULT_CORRECTIONS_LOG_PATH,
        )
        return settlement(root, now)

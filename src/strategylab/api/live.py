"""How the deployed strategy is doing on the practice account, read from its checks' logs.

Four files, all written by ``strategylab run``:

- ``state/account-live.jsonl`` - the account at each check
  (:mod:`strategylab.live.account`);
- ``state/fills-live.jsonl`` - what each sent order filled at, learned at a
  later check (:mod:`strategylab.live.fills`);
- ``state/decisions-live.jsonl`` - each decision and the risk layer's answer;
- ``state/session.json`` - the day's opening equity and the loss lock.

Only lines recorded at or after the deployment are counted, and only those
naming the deployed strategy, so an earlier deployment's checks are not taken
for this one's.

EVERY FIGURE IS ONE THE PROJECT ALREADY COMPUTES. Sharpe and the largest fall
are :func:`~strategylab.backtest.metrics.sharpe` and
:func:`~strategylab.backtest.metrics.max_drawdown`, the backtest's own, applied
to the equity the venue reported at each check. The return is the last
reported equity against the first. No figure is shown on too little: a Sharpe
needs :data:`MIN_CHECKS_FOR_SHARPE` checks, and below that the figure is
missing with the count, never a number from a handful of days.

THESE FIGURES ARE NOT EVIDENCE THE STRATEGY WORKS. A few weeks of one account
on one instrument cannot say that; the luck test is what speaks to it. They
show the plumbing working, and how far live runs from the backtest.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from strategylab.api.reading import (
    Unreadable,
    figure_or_none,
    missing,
    parse_json,
    read_json_object,
    text_or_none,
)
from strategylab.backtest.metrics import max_drawdown, sharpe

#: The four files, relative to the project root.
ACCOUNT_LOG = Path("state") / "account-live.jsonl"
FILLS_LOG = Path("state") / "fills-live.jsonl"
DECISION_LOG = Path("state") / "decisions-live.jsonl"
SESSION_FILE = Path("state") / "session.json"

#: Fewest checks a live Sharpe is shown for. Twenty trading days is about a
#: month; fewer and the figure is mostly the noise in two or three days.
MIN_CHECKS_FOR_SHARPE = 20

#: Largest log read, in bytes.
MAX_LOG_BYTES = 32 * 1024 * 1024

#: How many decisions the page is given, newest first.
RECENT_DECISIONS = 30


def _instant(text: Any) -> datetime | None:
    """Read an ISO-8601 instant that carries an offset.

    Args:
        text: A field read from a file.

    Returns:
        The instant, or ``None`` when it is not text, not ISO-8601, or has no
        offset - a time without one cannot be put before or after another.
    """
    value = text_or_none(text)
    if value is None:
        return None
    try:
        moment = datetime.fromisoformat(value)
        # Converted here, once: a time at the edge of the calendar whose offset
        # takes it past either end cannot be compared, and is not a time.
        return moment.astimezone(UTC) if moment.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _lines(path: Path) -> list[dict[str, Any]] | None:
    """Read a JSON Lines log, keeping the lines that are objects.

    Args:
        path: The log.

    Returns:
        Each line that is a JSON object, in file order. ``None`` when the file
        cannot be opened or is larger than :data:`MAX_LOG_BYTES`; an empty
        list when it does not exist.
    """
    if not path.exists():
        return []
    try:
        if path.stat().st_size > MAX_LOG_BYTES:
            return None
        raw = path.read_bytes().splitlines()
    except OSError:
        return None
    kept: list[dict[str, Any]] = []
    for line in raw:
        try:
            value = parse_json(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            kept.append(value)
    return kept


def _checks(root: Path, strategy: str, since: datetime) -> list[dict[str, Any]] | None:
    """Read the account at each check since a deployment.

    Args:
        root: The project root.
        strategy: The deployed strategy's name.
        since: When it was deployed.

    Returns:
        One entry per check, in the order recorded, each with ``bar_date``,
        ``recorded_at``, ``equity`` and ``cash`` in account currency,
        ``currency``, ``position_quantity`` in units, ``order_side``,
        ``order_outcome``, and ``venue_order_id``. A line is kept only if it is of schema version 1,
        names ``strategy``, was recorded at or after ``since``, and has a
        finite equity. ``None`` when the log cannot be read.
    """
    lines = _lines(root / ACCOUNT_LOG)
    if lines is None:
        return None
    kept: list[dict[str, Any]] = []
    for line in lines:
        recorded = _instant(line.get("recorded_at"))
        equity = figure_or_none(line.get("equity"))
        if (
            line.get("schema_version") != 1
            or line.get("strategy") != strategy
            or recorded is None
            or recorded < since
            or equity is None
        ):
            continue
        kept.append(
            {
                "bar_date": text_or_none(line.get("bar_date")),
                "recorded_at": recorded.astimezone(UTC).isoformat(),
                "equity": float(equity),
                "cash": figure_or_none(line.get("cash")),
                "currency": text_or_none(line.get("currency")),
                "position_quantity": figure_or_none(line.get("position_quantity")),
                "order_side": text_or_none(line.get("order_side")),
                "order_outcome": text_or_none(line.get("order_outcome")),
                "venue_order_id": text_or_none(line.get("venue_order_id")),
            }
        )
    return kept


def _fills(root: Path) -> dict[str, dict[str, Any]] | None:
    """Read what each sent order filled at.

    Args:
        root: The project root.

    Returns:
        Venue order id to ``price`` per unit and ``quantity`` in units, both in
        the instrument's ``currency``, and ``filled_at``. The first line for an
        id wins, as the log is written once per order. A line is kept only if
        it is of schema version 1 with an id, a finite positive price and
        quantity, and a fill time with an offset. ``None`` when the log cannot
        be read.
    """
    lines = _lines(root / FILLS_LOG)
    if lines is None:
        return None
    fills: dict[str, dict[str, Any]] = {}
    for line in lines:
        order_id = text_or_none(line.get("venue_order_id"))
        price = figure_or_none(line.get("price"))
        quantity = figure_or_none(line.get("quantity"))
        filled = _instant(line.get("filled_at"))
        if (
            line.get("schema_version") != 1
            or order_id is None
            or order_id in fills
            or price is None
            or price <= 0
            or quantity is None
            or quantity <= 0
            or filled is None
        ):
            continue
        fills[order_id] = {
            "price": float(price),
            "quantity": float(quantity),
            "currency": text_or_none(line.get("currency")),
            "filled_at": filled.isoformat(),
        }
    return fills


def _decisions(root: Path, since: datetime) -> list[dict[str, Any]] | None:
    """Read the decisions recorded since a deployment, newest first.

    Args:
        root: The project root.
        since: When the strategy was deployed.

    Returns:
        Up to :data:`RECENT_DECISIONS` entries, each with ``bar_date``,
        ``recorded_at``, ``action``, ``risk_verdict``, ``risk_reason``,
        ``conditions_met`` and ``conditions_failed``. ``None`` when the log
        cannot be read.
    """
    lines = _lines(root / DECISION_LOG)
    if lines is None:
        return None
    kept: list[dict[str, Any]] = []
    for line in lines:
        recorded = _instant(line.get("recorded_at"))
        if recorded is None or recorded < since or line.get("source") != "live":
            continue

        def texts(value: Any) -> list[str]:
            return (
                [item for item in value if isinstance(item, str)] if isinstance(value, list) else []
            )

        kept.append(
            {
                "bar_date": (text_or_none(line.get("bar_timestamp")) or "")[:10] or None,
                "recorded_at": recorded.astimezone(UTC).isoformat(),
                "action": text_or_none(line.get("action")),
                "risk_verdict": text_or_none(line.get("risk_verdict")),
                "risk_reason": text_or_none(line.get("risk_reason")),
                "conditions_met": texts(line.get("conditions_met")),
                "conditions_failed": texts(line.get("conditions_failed")),
            }
        )
    return list(reversed(kept))[:RECENT_DECISIONS]


def _figures(checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Work out the live figures from the account at each check.

    Args:
        checks: As :func:`_checks` returns them, oldest first.

    Returns:
        ``return_pct``, the last equity against the first as a PERCENTAGE;
        ``sharpe``, annualised; ``max_drawdown_pct``, a PERCENTAGE; ``sent``
        and ``failed``, counts of orders. Each figure is ``None`` when there
        are too few checks for it, and ``why_missing`` says, per figure, why.
    """
    equity = pd.Series([check["equity"] for check in checks], dtype=float)
    why: dict[str, str] = {}
    result: dict[str, Any] = {
        "return_pct": None,
        "sharpe": None,
        "max_drawdown_pct": None,
        "sent": sum(1 for check in checks if check["order_outcome"] == "sent"),
        "failed": sum(1 for check in checks if check["order_outcome"] == "failed"),
        "checks": len(checks),
    }
    if len(checks) < 2:
        why["return_pct"] = why["max_drawdown_pct"] = "needs two checks"
    else:
        first = float(equity.iloc[0])
        if first > 0:
            result["return_pct"] = (float(equity.iloc[-1]) / first - 1) * 100
        else:
            why["return_pct"] = "the first check reported no equity"
        try:
            result["max_drawdown_pct"] = max_drawdown(equity)
        except ValueError as exc:
            why["max_drawdown_pct"] = str(exc)
    if len(checks) < MIN_CHECKS_FOR_SHARPE:
        why["sharpe"] = f"needs {MIN_CHECKS_FOR_SHARPE} checks; {len(checks)} so far"
    else:
        try:
            result["sharpe"] = sharpe(equity.pct_change().dropna())
        except ValueError as exc:
            why["sharpe"] = str(exc)
    result["why_missing"] = why
    return result


def _loss_lock(root: Path, today: date) -> bool | None:
    """Read whether the daily loss limit has locked buying today.

    Args:
        root: The project root.
        today: The UTC date now.

    Returns:
        True or False as the session file says for ``today``; False when the
        file is for an earlier day, because the lock lasts one day; ``None``
        when there is no file or it cannot be read.
    """
    try:
        payload = read_json_object(root / SESSION_FILE, 64 * 1024)
    except Unreadable:
        return None
    if payload.get("day") != today.isoformat():
        return False
    return payload.get("tripped") is True


def read_live(root: Path, deployed: dict[str, Any], strategy: str, now: datetime) -> dict[str, Any]:
    """Report how the deployed strategy is doing.

    Args:
        root: The project root.
        deployed: The deployment, as
            :func:`~strategylab.api.deployment.read_deployment` reports it.
        strategy: The deployed strategy's name, as its config declares it.
        now: The present moment, timezone-aware.

    Returns:
        The missing shape when the deployment has no readable time or a log
        cannot be read. Otherwise ``available`` true; ``checks``, the account
        at each check since deploying, oldest first, each with ``fill``, what
        its order filled at (:func:`_fills`) or ``None`` while none is
        recorded; ``fills_readable``, false when the fills log could not be
        read, in which case every ``fill`` is ``None``; ``figures``
        (:func:`_figures`); ``decisions``, newest first; and ``loss_lock``.
    """
    since = _instant(deployed.get("deployed_at"))
    if since is None:
        return missing("The deployment records no time it was made.")
    checks = _checks(root, strategy, since)
    if checks is None:
        return missing("The account log could not be read.")
    decisions = _decisions(root, since)
    if decisions is None:
        return missing("The decision log could not be read.")
    fills = _fills(root)
    for check in checks:
        order_id = check["venue_order_id"]
        check["fill"] = None if fills is None or order_id is None else fills.get(order_id)
    return {
        "available": True,
        "checks": checks,
        "fills_readable": fills is not None,
        "figures": _figures(checks),
        "decisions": decisions,
        "loss_lock": _loss_lock(root, now.astimezone(UTC).date()),
    }

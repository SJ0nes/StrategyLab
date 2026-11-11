"""When the deployed strategy's daily check is due, and which weekdays were missed.

``docs/decisions/0019`` sets the rule, and records that it is the assistant's:
one check a weekday, after :data:`CHECK_AFTER` UTC - after the London close in
both winter and summer time - while the app is open. A day is a UTC day.

:func:`due` decides; :func:`start_if_due` acts on it; the server calls the
second once a minute from a thread of its own (:mod:`strategylab.api.server`).
Everything here takes the time as an argument, so a test can say what time it
is.

A WEEKDAY HAS HAD ITS CHECK if a daily check job was started on it, whatever
became of the job. A failed check is not retried on the same day: a failure
that repeats every minute would fill the log, and the next day's check trades
the next bar anyway. The app shows a failed check as failed.

A MISSED DAY is a weekday since the deployment, after its check time, on which
no check was started. Bank holidays are counted as weekdays: the project has no
exchange calendar, and on such a day the check decides on the last bar it has,
which the one-position rule keeps from ordering twice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from strategylab.api.deployment import CHECK_AFTER, check_refusal, read_deployment
from strategylab.api.jobs import DAILY_CHECK, JobRefused, list_job_records, start_job
from strategylab.api.registry import find_strategy

#: How many weekdays back a missed check is looked for.
MISSED_LOOKBACK_DAYS = 31


def _is_weekday(day: date) -> bool:
    return day.weekday() < 5


def checked_days(root: Path) -> set[date]:
    """List the UTC days on which a daily check was started.

    Args:
        root: The project root.

    Returns:
        The day of every daily check job on record, read from its id.
    """
    days: set[date] = set()
    for record in list_job_records(root):
        if record["kind"] != DAILY_CHECK:
            continue
        try:
            days.add(datetime.strptime(record["id"][:8], "%Y%m%d").date())
        except ValueError:
            # An id shaped like a date that is not one names no day.
            continue
    return days


def next_check(after: datetime) -> datetime:
    """Say when the next check falls due.

    Args:
        after: The present moment, timezone-aware.

    Returns:
        The next weekday's :data:`CHECK_AFTER`, UTC - today's if it has not yet
        passed and today is a weekday.
    """
    moment = after.astimezone(UTC)
    day = moment.date()
    if moment.time() >= CHECK_AFTER:
        day += timedelta(days=1)
    while not _is_weekday(day):
        day += timedelta(days=1)
    return datetime.combine(day, CHECK_AFTER, tzinfo=UTC)


def missed_days(deployed_at: datetime, now: datetime, checked: set[date]) -> list[date]:
    """List the weekdays since deploying whose check was due and never started.

    Args:
        deployed_at: When the strategy was deployed, timezone-aware.
        now: The present moment, timezone-aware.
        checked: The days a check was started, as :func:`checked_days` gives.

    Returns:
        Each such day, oldest first, looking back at most
        :data:`MISSED_LOOKBACK_DAYS` days. A day counts only if its check time
        fell after the deployment and before ``now``.
    """
    start = deployed_at.astimezone(UTC)
    end = now.astimezone(UTC)
    day = max(start.date(), end.date() - timedelta(days=MISSED_LOOKBACK_DAYS))
    missed: list[date] = []
    while day <= end.date():
        due_at = datetime.combine(day, CHECK_AFTER, tzinfo=UTC)
        if _is_weekday(day) and start <= due_at <= end and day not in checked:
            missed.append(day)
        day += timedelta(days=1)
    return missed


def due(root: Path, now: datetime, environ: Mapping[str, str]) -> tuple[str | None, str]:
    """Decide whether the deployed strategy's check should start now.

    Args:
        root: The project root.
        now: The present moment, timezone-aware.
        environ: The server's environment, for whether the practice account
            is set.

    Returns:
        ``(strategy_id, why)``: the strategy to check and ``"due"`` when a
        check should start; otherwise ``None`` and the reason it should not.
    """
    moment = now.astimezone(UTC)
    deployed = read_deployment(root)
    if deployed is None or not deployed["available"]:
        return None, "nothing is deployed"
    strategy_id = str(deployed["strategy_id"])
    refusal = check_refusal(root, strategy_id, environ)
    if refusal is not None:
        return None, refusal
    if not _is_weekday(moment.date()):
        return None, "no check on a weekend"
    if moment.time() < CHECK_AFTER:
        return None, "not yet time for today's check"
    if moment.date() in checked_days(root):
        return None, "today's check has already been started"
    return strategy_id, "due"


def start_if_due(
    root: Path,
    now: datetime,
    environ: Mapping[str, str],
    spawn: Callable[[list[str], Path, Path], None] | None = None,
) -> dict[str, Any] | None:
    """Start the daily check if it is due.

    Args:
        root: The project root.
        now: The present moment, timezone-aware.
        environ: The server's environment.
        spawn: As :func:`~strategylab.api.jobs.start_job` takes it.

    Returns:
        The job started, or ``None`` when none was due or one could not be
        started - another job running, say. A refusal is not raised: the
        scheduler asks again a minute later.
    """
    strategy_id, _ = due(root, now, environ)
    if strategy_id is None:
        return None
    strategy = find_strategy(root, strategy_id)
    if strategy is None:
        return None
    try:
        return start_job(root, strategy, DAILY_CHECK, now, spawn=spawn, environ=environ)
    except JobRefused:
        return None


def schedule_report(root: Path, now: datetime, environ: Mapping[str, str]) -> dict[str, Any]:
    """Describe the schedule for the page.

    Args:
        root: The project root.
        now: The present moment, timezone-aware.
        environ: The server's environment.

    Returns:
        ``next_check`` as an ISO-8601 instant, or ``None`` when nothing would
        run; ``why_not``, the reason nothing would run, or ``None``;
        ``missed``, the ISO dates of missed checks since the deployment; and
        ``checked_today``.
    """
    deployed = read_deployment(root)
    checked = checked_days(root)
    moment = now.astimezone(UTC)
    report: dict[str, Any] = {
        "next_check": None,
        "why_not": None,
        "missed": [],
        "checked_today": moment.date() in checked,
    }
    if deployed is None or not deployed["available"]:
        report["why_not"] = "nothing is deployed"
        return report
    refusal = check_refusal(root, str(deployed["strategy_id"]), environ)
    if refusal is not None:
        report["why_not"] = refusal
    else:
        report["next_check"] = next_check(moment).isoformat()
    deployed_at = deployed["deployed_at"]
    if isinstance(deployed_at, str):
        try:
            since: datetime | None = datetime.fromisoformat(deployed_at).astimezone(UTC)
        except (ValueError, OverflowError):
            since = None
        if since is not None:
            report["missed"] = [day.isoformat() for day in missed_days(since, moment, checked)]
    return report

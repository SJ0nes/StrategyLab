"""Which strategy is deployed to the practice account, paused or not, and whether all is stopped.

``docs/decisions/0019`` records the rules, and that they are the assistant's:

- several strategies may be deployed; each has an equal share of the exposure
  ceiling, a ledger of its own units, and a decision log of its own
  (``docs/decisions/0022``, which replaces "one at a time"; the split is the
  operator's choice, the rest the assistant's);
- a strategy that failed the luck test may be deployed, and is labelled
  unproven;
- deploying needs the rules confirmed for the file as it is now, a search on
  that file that put a version forward, the practice account set, and the
  strategy's name typed by the operator;
- "stop all" is the app's own switch. It stops the daily check and nothing
  else, and it never touches ``configs/risk_limits.yaml``.

TWO FILES, both under ``state/`` and so not committed:
``state/deployment.json``, the deployment; ``state/stop-all.json``, the switch.
Both are read as files this server may not have written. A deployment that
cannot be read is reported as damaged, never as "nothing deployed", because the
second reading would let a strategy deploy beside others it cannot see.

THE DEPLOYMENT NAMES WHAT WAS DEPLOYED, EXACTLY: the config's content hash, the
search artifact whose candidate trades, and its parameters. A later edit to the
config does not change what trades. The daily check refuses to run a file that
has changed since it was deployed, and the deployment says so until it is
deployed again.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from strategylab.api.drafts import confirmation
from strategylab.api.history import latest_search
from strategylab.api.reading import (
    Unreadable,
    params_or_none,
    read_json_object,
    text_or_none,
)
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.live.account import DEFAULT_ACCOUNT_LOG_PATH
from strategylab.live.fills import DEFAULT_FILLS_LOG_PATH
from strategylab.live.ledger import AGREEMENT_TOLERANCE_UNITS, read_ledger
from strategylab.live.worker import STRATEGY_ID

#: The deployment and the switch, relative to the project root.
DEPLOYMENT_FILE = Path("state") / "deployment.json"
STOP_ALL_FILE = Path("state") / "stop-all.json"

#: Schema version of the deployment file. Version 1 held one deployment; it is
#: still read, as a list of one.
DEPLOYMENT_SCHEMA_VERSION = 2

#: Schema version of the stop-all switch's file.
STOP_ALL_SCHEMA_VERSION = 1

#: Largest file read, in bytes.
MAX_DEPLOYMENT_BYTES = 64 * 1024

#: A search artifact's file name, as ``write_search_result`` makes it.
ARTIFACT_NAME = re.compile(r"search-\d{8}T\d{6}Z\.json")

#: The time of day, UTC, after which a weekday's check may run: after the London
#: close in both winter and summer time. Earlier, the day's bar is not final, and
#: ``strategylab run`` would cache its interim close for good.
CHECK_AFTER = time(17, 30)

#: Held while a deployment file is read, changed and written.
_WRITING = threading.Lock()


class DeployRefused(Exception):
    """A deployment change could not be made, for a reason the operator can act on."""


def venue_ready(environ: Mapping[str, str]) -> str | None:
    """Say why the practice account is not set, or that it is.

    Args:
        environ: The server's environment. Only whether a key is set is looked
            at; the key itself is never read into anything returned.

    Returns:
        ``None`` when ``T212_ENVIRONMENT`` is ``demo`` and ``T212_API_KEY`` is
        set; otherwise the reason, in words.
    """
    if environ.get("T212_ENVIRONMENT") != "demo":
        return "the practice account is not set: T212_ENVIRONMENT must be demo"
    if not environ.get("T212_API_KEY", "").strip():
        return "the practice account is not set: T212_API_KEY is missing"
    return None


def _write(path: Path, payload: dict[str, Any]) -> None:
    """Write a file whole, through a temporary file of its own renamed over it.

    Args:
        path: The file.
        payload: Its content.

    Raises:
        OSError: If it cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.stem}-", suffix=".partial")
    partial = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _raw_deployments(root: Path) -> list[dict[str, Any]] | None:
    """Read the deployment file.

    Args:
        root: The project root.

    Returns:
        ``None`` when there is no file. Otherwise one entry per deployed
        strategy, in the order the file holds them. A file of schema version 1,
        written when one strategy was deployed at a time, is read as a list of
        its one entry.

    Raises:
        DeployRefused: If the file is there but cannot be read, is not a
            deployment this code knows, or names a strategy twice or not at all.
    """
    path = root / DEPLOYMENT_FILE
    if not path.exists():
        return None
    try:
        payload = read_json_object(path, MAX_DEPLOYMENT_BYTES)
    except Unreadable as exc:
        raise DeployRefused(f"the deployment record cannot be read: {exc}") from exc
    version = payload.get("schema_version")
    if version == 1:
        entries: Any = [{key: value for key, value in payload.items() if key != "schema_version"}]
    elif version == DEPLOYMENT_SCHEMA_VERSION:
        entries = payload.get("deployments")
    else:
        raise DeployRefused("the deployment record is in a format this app does not read")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise DeployRefused("the deployment record does not hold a list of deployments")
    ids = [text_or_none(entry.get("strategy_id")) for entry in entries]
    if None in ids or len(set(ids)) != len(ids):
        raise DeployRefused("the deployment record names a strategy twice, or not at all")
    return entries


def _write_deployments(root: Path, entries: list[dict[str, Any]]) -> None:
    """Write the deployments, or remove the file when there are none.

    Args:
        root: The project root.
        entries: One per deployed strategy. Written in order of strategy id.

    Raises:
        OSError: If the file cannot be written or removed.
    """
    if not entries:
        (root / DEPLOYMENT_FILE).unlink(missing_ok=True)
        return
    _write(
        root / DEPLOYMENT_FILE,
        {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "deployments": sorted(entries, key=lambda entry: str(entry["strategy_id"])),
        },
    )


def _report(root: Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Describe one deployment for the page.

    Args:
        root: The project root.
        raw: Its entry, as the file holds it.

    Returns:
        As :func:`read_deployment` describes it.
    """
    strategy_id = text_or_none(raw.get("strategy_id"))
    strategy = None if strategy_id is None else find_strategy(root, strategy_id)
    problem = None
    if strategy is None:
        problem = "the deployed strategy's file is no longer in configs/"
    elif raw.get("config_hash") != strategy.config_hash:
        problem = "the deployed strategy's file has changed since it was deployed"
    return {
        "available": True,
        "strategy_id": strategy_id,
        "artifact": text_or_none(raw.get("artifact")),
        "params": params_or_none(raw.get("params")),
        "proven": raw.get("proven") is True,
        "deployed_at": text_or_none(raw.get("deployed_at")),
        # Anything but a plain false is paused: a damaged value must not read as "go".
        "paused": raw.get("paused") is not False,
        "paused_at": text_or_none(raw.get("paused_at")),
        "current": problem is None,
        "problem": problem,
    }


def _damaged(exc: DeployRefused) -> dict[str, Any]:
    """The missing shape for a deployment file that cannot be read."""
    return {"available": False, "reason": f"{str(exc)[0].upper()}{str(exc)[1:]}."}


def read_deployments(root: Path) -> dict[str, Any]:
    """Report every deployment.

    Args:
        root: The project root.

    Returns:
        The missing shape when the record is damaged - which blocks every
        deployment change but undeploying, which clears it. Otherwise
        ``available`` true and ``deployments``, each as :func:`read_deployment`
        reports it, in order of strategy id: the order the daily checks run in.
    """
    try:
        entries = _raw_deployments(root) or []
    except DeployRefused as exc:
        return _damaged(exc)
    reports = [_report(root, entry) for entry in entries]
    return {
        "available": True,
        "deployments": sorted(reports, key=lambda report: str(report["strategy_id"])),
    }


def read_deployment(root: Path, strategy_id: str) -> dict[str, Any] | None:
    """Report one strategy's deployment.

    Args:
        root: The project root.
        strategy_id: The strategy.

    Returns:
        ``None`` when it is not deployed. The missing shape when the record is
        damaged. Otherwise ``available`` true; ``strategy_id``; ``artifact``,
        the search whose candidate trades; ``params``, that candidate in BARS;
        ``proven``, whether the search's luck test passed; ``deployed_at``;
        ``paused`` and ``paused_at``; and ``current``, false when the strategy
        is no longer in ``configs/`` or its file has changed since it was
        deployed - in which case ``problem`` says which.
    """
    everything = read_deployments(root)
    if not everything["available"]:
        return everything
    for report in everything["deployments"]:
        if report["strategy_id"] == strategy_id:
            return dict(report)
    return None


def deployed_count(root: Path) -> int:
    """Count the deployed strategies, paused ones included.

    Args:
        root: The project root.

    Returns:
        How many the record holds; each has an equal share of the exposure
        ceiling (``docs/decisions/0022``).

    Raises:
        DeployRefused: If the record is damaged.
    """
    return len(_raw_deployments(root) or [])


def deploy(
    root: Path,
    strategy: StrategyFile,
    typed: str,
    environ: Mapping[str, str],
    now: datetime,
) -> dict[str, Any]:
    """Deploy a strategy's newest searched candidate to the practice account.

    Strategies already deployed stay deployed; each then has an equal share of
    the exposure ceiling (``docs/decisions/0022``).

    Args:
        root: The project root.
        strategy: The strategy, as the registry found it now.
        typed: What the operator typed to confirm; must be the strategy's name.
        environ: The server's environment, for :func:`venue_ready`.
        now: The present moment, timezone-aware.

    Returns:
        The deployment, as :func:`read_deployment` reports it.

    Raises:
        DeployRefused: If the name typed is not the strategy's; its file name
            is not one a decision log can be named after; the practice
            account is not set; the strategy is already deployed, or the
            record is damaged; the rules are not confirmed for the file as it
            is now; or no search on the file as it is now put a version
            forward.
        OSError: If the record cannot be written.
    """
    if typed.strip() != strategy.name:
        raise DeployRefused(f"type the strategy's name, {strategy.name}, to deploy it")
    if STRATEGY_ID.fullmatch(strategy.id) is None:
        raise DeployRefused(
            "its file name must be letters, digits, _ and - only, as its own decision log "
            "is named after it"
        )
    unready = venue_ready(environ)
    if unready is not None:
        raise DeployRefused(unready)
    with _WRITING:
        entries = _raw_deployments(root) or []
        if any(entry.get("strategy_id") == strategy.id for entry in entries):
            raise DeployRefused("it is already deployed")
        if not confirmation(root, strategy)["confirmed"]:
            raise DeployRefused("its rules have not been confirmed for the file as it is now")
        search = latest_search(root, strategy)
        if search is None or search["config_changed_since"] or search["candidate"] is None:
            raise DeployRefused(
                "no search on the file as it is now has put a version forward; run a backtest"
            )
        if ARTIFACT_NAME.fullmatch(str(search["artifact"])) is None:
            raise DeployRefused("the search's file name is not one this app reads")
        entries.append(
            {
                "strategy_id": strategy.id,
                "strategy": strategy.name,
                "config": strategy.config,
                "config_hash": strategy.config_hash,
                "artifact": search["artifact"],
                "params": search["candidate"],
                "proven": search["gate"]["status"] == "passed",
                "deployed_at": now.astimezone(UTC).isoformat(),
                "paused": False,
                "paused_at": None,
            }
        )
        _write_deployments(root, entries)
    deployed = read_deployment(root, strategy.id)
    assert deployed is not None
    return deployed


def set_paused(root: Path, strategy_id: str, paused: bool, now: datetime) -> dict[str, Any]:
    """Pause or resume one deployed strategy.

    Args:
        root: The project root.
        strategy_id: The strategy.
        paused: True to pause, False to resume.
        now: The present moment, timezone-aware.

    Returns:
        The deployment, as :func:`read_deployment` reports it.

    Raises:
        DeployRefused: If the strategy is not deployed, or the record is
            damaged.
        OSError: If the record cannot be written.
    """
    with _WRITING:
        entries = _raw_deployments(root) or []
        entry = next((e for e in entries if e.get("strategy_id") == strategy_id), None)
        if entry is None:
            raise DeployRefused("that strategy is not deployed")
        entry["paused"] = paused
        entry["paused_at"] = now.astimezone(UTC).isoformat() if paused else None
        _write_deployments(root, entries)
    deployed = read_deployment(root, strategy_id)
    assert deployed is not None
    return deployed


def undeploy(root: Path, strategy_id: str) -> None:
    """Remove one strategy's deployment, or a damaged record whole.

    REFUSED WHILE THE STRATEGY HOLDS UNITS (``docs/decisions/0022``). Its units
    would belong to no deployed strategy, and every later check would find the
    strategies' units and the venue's position disagreeing. The app does not
    sell on undeploying; a strategy is undeployed once its own exit has filled.

    Args:
        root: The project root.
        strategy_id: The strategy. Ignored when the record is damaged: the
            damaged record is removed whole, as it names nothing reliably.

    Raises:
        DeployRefused: If the strategy is not deployed; it holds units, or has
            an order whose fill is not yet recorded; or the logs that say so
            cannot be read.
        OSError: If the record cannot be written or removed.
    """
    with _WRITING:
        try:
            entries = _raw_deployments(root)
        except DeployRefused:
            (root / DEPLOYMENT_FILE).unlink()
            return
        entries = entries or []
        kept = [entry for entry in entries if entry.get("strategy_id") != strategy_id]
        if len(kept) == len(entries):
            raise DeployRefused("that strategy is not deployed")
        try:
            ledger = read_ledger(root / DEFAULT_ACCOUNT_LOG_PATH, root / DEFAULT_FILLS_LOG_PATH)
        except (ValueError, OSError) as exc:
            raise DeployRefused(f"what it holds cannot be read: {exc}") from exc
        if strategy_id in ledger.waiting:
            raise DeployRefused("it has an order whose fill is not yet recorded")
        held = ledger.held(strategy_id)
        if abs(held) > AGREEMENT_TOLERANCE_UNITS:
            raise DeployRefused(
                f"it holds {held:g} units; undeploy it once its own exit has filled"
            )
        _write_deployments(root, kept)


def stop_all(root: Path) -> dict[str, Any]:
    """Report the app's stop-all switch.

    Args:
        root: The project root.

    Returns:
        ``stopped``, true when the switch is on - and also when its file is
        there but cannot be read, because a damaged "stop" must not read as
        "go"; ``stopped_at``; and ``damaged``, true in that second case.
    """
    path = root / STOP_ALL_FILE
    if not path.exists():
        return {"stopped": False, "stopped_at": None, "damaged": False}
    try:
        payload = read_json_object(path, MAX_DEPLOYMENT_BYTES)
    except Unreadable:
        return {"stopped": True, "stopped_at": None, "damaged": True}
    if payload.get("schema_version") != STOP_ALL_SCHEMA_VERSION:
        return {"stopped": True, "stopped_at": None, "damaged": True}
    return {
        "stopped": payload.get("stopped") is not False,
        "stopped_at": text_or_none(payload.get("stopped_at")),
        "damaged": False,
    }


def set_stop_all(root: Path, stopped: bool, now: datetime) -> dict[str, Any]:
    """Turn the app's stop-all switch on or off.

    Args:
        root: The project root.
        stopped: True to stop all trading from the app, False to allow it.
        now: The present moment, timezone-aware.

    Returns:
        The switch, as :func:`stop_all` reports it.

    Raises:
        OSError: If the file cannot be written.
    """
    _write(
        root / STOP_ALL_FILE,
        {
            "schema_version": STOP_ALL_SCHEMA_VERSION,
            "stopped": stopped,
            "stopped_at": now.astimezone(UTC).isoformat() if stopped else None,
        },
    )
    return stop_all(root)


def window_refusal(now: datetime) -> str | None:
    """Say why no check may run at this moment, or that one may.

    Args:
        now: The present moment, timezone-aware.

    Returns:
        ``None`` on a weekday at or after :data:`CHECK_AFTER` UTC; otherwise
        the reason.
    """
    moment = now.astimezone(UTC)
    if moment.weekday() >= 5:
        return "no check runs on a weekend"
    if moment.time() < CHECK_AFTER:
        return "a check runs only after 17:30 UTC, once the day's prices are final"
    return None


def check_refusal(root: Path, strategy_id: str, environ: Mapping[str, str]) -> str | None:
    """Say why a daily check for a strategy may not run now, or that it may.

    Args:
        root: The project root.
        strategy_id: The strategy the check is for.
        environ: The server's environment.

    Returns:
        ``None`` when the strategy is deployed, its record is current, it is
        not paused, stop-all is off and the practice account is set.
        Otherwise the first reason it may not.
    """
    deployed = read_deployment(root, strategy_id)
    if deployed is None:
        return "that strategy is not deployed"
    if not deployed["available"]:
        return str(deployed["reason"]).rstrip(".")
    if not deployed["current"]:
        return str(deployed["problem"])
    if deployed["paused"]:
        return "the strategy is paused"
    if stop_all(root)["stopped"]:
        return "all trading from the app is stopped"
    return venue_ready(environ)

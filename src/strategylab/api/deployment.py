"""Which strategy is deployed to the practice account, paused or not, and whether all is stopped.

``docs/decisions/0019`` records the rules, and that they are the assistant's:

- one strategy is deployed at a time;
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
second reading would let a second strategy deploy beside a first.

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

#: The deployment and the switch, relative to the project root.
DEPLOYMENT_FILE = Path("state") / "deployment.json"
STOP_ALL_FILE = Path("state") / "stop-all.json"

#: Schema version of both files.
DEPLOYMENT_SCHEMA_VERSION = 1

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


def _raw_deployment(root: Path) -> dict[str, Any] | None:
    """Read the deployment file.

    Args:
        root: The project root.

    Returns:
        ``None`` when there is no file. Otherwise its content.

    Raises:
        DeployRefused: If the file is there but cannot be read, or is not a
            deployment this code knows.
    """
    path = root / DEPLOYMENT_FILE
    if not path.exists():
        return None
    try:
        payload = read_json_object(path, MAX_DEPLOYMENT_BYTES)
    except Unreadable as exc:
        raise DeployRefused(f"the deployment record cannot be read: {exc}") from exc
    if payload.get("schema_version") != DEPLOYMENT_SCHEMA_VERSION:
        raise DeployRefused("the deployment record is in a format this app does not read")
    return payload


def read_deployment(root: Path) -> dict[str, Any] | None:
    """Report the deployment.

    Args:
        root: The project root.

    Returns:
        ``None`` when nothing is deployed. The missing shape when the record is
        damaged - which blocks a new deployment until it is removed by
        undeploying. Otherwise ``available`` true; ``strategy_id``; ``artifact``,
        the search whose candidate trades; ``params``, that candidate in BARS;
        ``proven``, whether the search's luck test passed; ``deployed_at``;
        ``paused`` and ``paused_at``; and ``current``, false when the strategy
        is no longer in ``configs/`` or its file has changed since it was
        deployed - in which case ``problem`` says which.
    """
    try:
        raw = _raw_deployment(root)
    except DeployRefused as exc:
        return {"available": False, "reason": f"{str(exc)[0].upper()}{str(exc)[1:]}."}
    if raw is None:
        return None
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


def deploy(
    root: Path,
    strategy: StrategyFile,
    typed: str,
    environ: Mapping[str, str],
    now: datetime,
) -> dict[str, Any]:
    """Deploy a strategy's newest searched candidate to the practice account.

    Args:
        root: The project root.
        strategy: The strategy, as the registry found it now.
        typed: What the operator typed to confirm; must be the strategy's name.
        environ: The server's environment, for :func:`venue_ready`.
        now: The present moment, timezone-aware.

    Returns:
        The deployment, as :func:`read_deployment` reports it.

    Raises:
        DeployRefused: If the name typed is not the strategy's; the practice
            account is not set; a strategy is already deployed, or the record
            of one is damaged; the rules are not confirmed for the file as it
            is now; or no search on the file as it is now put a version
            forward.
        OSError: If the record cannot be written.
    """
    if typed.strip() != strategy.name:
        raise DeployRefused(f"type the strategy's name, {strategy.name}, to deploy it")
    unready = venue_ready(environ)
    if unready is not None:
        raise DeployRefused(unready)
    with _WRITING:
        existing = _raw_deployment(root)
        if existing is not None:
            raise DeployRefused(
                f"{existing.get('strategy_id')} is already deployed; only one strategy is "
                "deployed at a time"
            )
        if not confirmation(root, strategy)["confirmed"]:
            raise DeployRefused("its rules have not been confirmed for the file as it is now")
        search = latest_search(root, strategy)
        if search is None or search["config_changed_since"] or search["candidate"] is None:
            raise DeployRefused(
                "no search on the file as it is now has put a version forward; run a backtest"
            )
        if ARTIFACT_NAME.fullmatch(str(search["artifact"])) is None:
            raise DeployRefused("the search's file name is not one this app reads")
        _write(
            root / DEPLOYMENT_FILE,
            {
                "schema_version": DEPLOYMENT_SCHEMA_VERSION,
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
            },
        )
    deployed = read_deployment(root)
    assert deployed is not None
    return deployed


def set_paused(root: Path, paused: bool, now: datetime) -> dict[str, Any]:
    """Pause or resume the deployed strategy.

    Args:
        root: The project root.
        paused: True to pause, False to resume.
        now: The present moment, timezone-aware.

    Returns:
        The deployment, as :func:`read_deployment` reports it.

    Raises:
        DeployRefused: If nothing is deployed, or the record is damaged.
        OSError: If the record cannot be written.
    """
    with _WRITING:
        raw = _raw_deployment(root)
        if raw is None:
            raise DeployRefused("nothing is deployed")
        raw["paused"] = paused
        raw["paused_at"] = now.astimezone(UTC).isoformat() if paused else None
        _write(root / DEPLOYMENT_FILE, raw)
    deployed = read_deployment(root)
    assert deployed is not None
    return deployed


def undeploy(root: Path) -> None:
    """Remove the deployment, damaged or not.

    The practice account's position, if there is one, is left as it is: the
    app does not sell on undeploying. The operator closes it from Trading 212.

    Args:
        root: The project root.

    Raises:
        DeployRefused: If nothing is deployed.
        OSError: If the record cannot be removed.
    """
    with _WRITING:
        path = root / DEPLOYMENT_FILE
        if not path.exists():
            raise DeployRefused("nothing is deployed")
        path.unlink()


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
    if payload.get("schema_version") != DEPLOYMENT_SCHEMA_VERSION:
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
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
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
    deployed = read_deployment(root)
    if deployed is None:
        return "nothing is deployed"
    if not deployed["available"]:
        return str(deployed["reason"]).rstrip(".")
    if deployed["strategy_id"] != strategy_id:
        return "that strategy is not the one deployed"
    if not deployed["current"]:
        return str(deployed["problem"])
    if deployed["paused"]:
        return "the deployed strategy is paused"
    if stop_all(root)["stopped"]:
        return "all trading from the app is stopped"
    return venue_ready(environ)

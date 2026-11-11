"""What the app's first screen needs to know, read from the project as it stands.

One function, :func:`read_overview`, and it only reads. It computes no figure:
every number it returns was written by a command - a search artifact, the
protocol's access counter, a cache's provenance - and is passed on as recorded.

WHAT IS MISSING IS RETURNED AS MISSING. A figure that cannot be read comes back
as ``{"available": False, "reason": ...}`` with the reason in words, never as a
zero or a default. The app shows the reason; a number that looks like a result
and is not one is the thing this project exists to avoid.

IT READS NO CREDENTIAL. The venue block reports whether ``T212_API_KEY`` is set
in the environment the server was started with, as a boolean, and the value
never leaves this function. ``.env`` is not opened.

IT NAMES NO DIRECTORY ABOVE THE PROJECT. The loaders it calls put the path they
were given into their error messages, and that path starts with wherever the
project happens to be kept - on most machines, a directory named after the
person. Every reason passed on to the app goes through :func:`plain_reason`
first, which writes the project root as ``.``.

A FILE IN ``configs/`` OR ``state/`` IS NOT TRUSTED TO BE WELL-FORMED. One too
large to be what it claims is not opened, and an artifact holding ``NaN`` or
``Infinity`` - which Python's JSON reader accepts and JSON does not - is passed
over, because a response containing either cannot be sent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from strategylab.core.config import StrategyConfigError, config_content_hash, load_strategy
from strategylab.data.source import DataQualityError, cache_read
from strategylab.protocol import Protocol, load_protocol
from strategylab.risk.limits import load_limits
from strategylab.search.loop import SEARCH_SCHEMA_VERSION

#: Where each thing lives, relative to the project root. The same places the
#: commands use, which they name relative to the working directory.
PROTOCOL_FILE = Path("protocol.yaml")
CONFIGS_DIR = Path("configs")
LIMITS_FILE = CONFIGS_DIR / "risk_limits.yaml"
DATA_DIR = Path("data")
STATE_DIR = Path("state")

#: Largest strategy config read, in bytes. The committed one is a few
#: kilobytes; a megabyte of YAML in ``configs/`` is not a strategy.
MAX_CONFIG_BYTES = 1024 * 1024

#: Largest search artifact read, in bytes. A ninety-cell artifact with every
#: equity curve is under a megabyte.
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def plain_reason(root: Path, error: Exception) -> str:
    """Render an error for the app without the directory the project is kept in.

    Args:
        root: The project root.
        error: The error a loader raised.

    Returns:
        The error's message with every spelling of ``root`` - as given, resolved,
        and with either kind of slash - replaced by ``.``.
    """
    message = str(error)
    for spelling in {str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()}:
        message = message.replace(spelling, ".")
    return message


def _not_a_json_number(constant: str) -> float:
    """Refuse ``NaN``, ``Infinity`` and ``-Infinity`` while reading an artifact.

    Args:
        constant: The token the JSON reader met.

    Raises:
        ValueError: Always.
    """
    raise ValueError(f"{constant} is not a JSON number")


def _missing(reason: str) -> dict[str, Any]:
    """Return the shape that stands for a figure which could not be read.

    Args:
        reason: Why, in words the person using the app can act on.

    Returns:
        ``{"available": False, "reason": reason}``.
    """
    return {"available": False, "reason": reason}


def _prices(root: Path, protocol: Protocol) -> dict[str, Any]:
    """Describe the cached price data for the research instrument.

    Args:
        root: The project root.
        protocol: The parsed protocol, which names the instrument.

    Returns:
        The cache's provenance - instrument, number of bars, first and last bar
        as ISO dates, content hash - or the missing shape when there is no
        cache or it does not match its recorded hash.
    """
    ticker = protocol.instruments.research
    try:
        _, entry = cache_read(ticker, root / DATA_DIR)
    except FileNotFoundError:
        return _missing(f"No price data for {ticker} has been fetched.")
    except (DataQualityError, OSError, ValueError) as exc:
        reason = plain_reason(root, exc)
        return _missing(f"The stored price data for {ticker} cannot be used: {reason}")
    return {
        "available": True,
        "instrument": entry.ticker,
        "bars": entry.rows,
        "first_bar": entry.start.isoformat(),
        "last_bar": entry.end.isoformat(),
        "content_hash": entry.content_hash,
    }


def _kill_switch(root: Path) -> bool | dict[str, Any]:
    """Read the kill switch from the risk layer's own file, through its own loader.

    Args:
        root: The project root.

    Returns:
        The switch, or the missing shape when the limits file cannot be loaded.
        An unreadable limits file is NOT reported as "off".
    """
    try:
        return load_limits(root / LIMITS_FILE).kill_switch
    except (OSError, ValueError) as exc:
        return _missing(f"The risk limits file cannot be read: {plain_reason(root, exc)}")


def _latest_search(root: Path, name: str, config_hash: str) -> dict[str, Any] | None:
    """Find the newest search artifact for a strategy and report what it recorded.

    An artifact is considered only if it is no larger than
    :data:`MAX_ARTIFACT_BYTES`, is a JSON object holding no ``NaN`` or
    ``Infinity``, declares the schema version this code reads, and names this
    strategy. Others are passed over, not guessed at.

    Args:
        root: The project root.
        name: The strategy's name, as its config declares it.
        config_hash: Content hash of the strategy's config as it is now.

    Returns:
        The artifact's file name, when it was generated, how many cells it ran,
        its candidate's parameters in BARS, the candidate's annualised Sharpe
        and largest drawdown as a PERCENTAGE, the noise gate as recorded, and
        whether the config has changed since. ``None`` when no artifact
        qualifies. A search that put no candidate forward reports ``None`` for
        the candidate and its two figures.
    """
    for path in sorted((root / STATE_DIR).glob("search-*.json"), reverse=True):
        try:
            if path.stat().st_size > MAX_ARTIFACT_BYTES:
                continue
            payload = json.loads(
                path.read_text(encoding="utf-8"), parse_constant=_not_a_json_number
            )
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("schema_version") != SEARCH_SCHEMA_VERSION
            or payload.get("strategy") != name
        ):
            continue
        gate = payload.get("noise_gate")
        if not isinstance(gate, dict):
            continue
        raw_winner = payload.get("winner")
        winner: dict[str, Any] = raw_winner if isinstance(raw_winner, dict) else {}
        return {
            "artifact": path.name,
            "generated_at": payload.get("generated_at"),
            "cells": payload.get("grid_size"),
            "candidate": winner.get("params"),
            "sharpe": winner.get("sharpe"),
            "max_drawdown_pct": winner.get("max_drawdown_pct"),
            "gate": {
                "status": gate.get("status"),
                "beaten": gate.get("beaten"),
                "required": gate.get("required"),
                "scoreable_trials": gate.get("scoreable_trials"),
                "summary": gate.get("summary"),
            },
            "config_changed_since": payload.get("config_content_hash") != config_hash,
        }
    return None


def _strategies(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """List the strategy configs in ``configs/``, and the ones that do not load.

    Every ``.yaml`` file there except the risk limits file is taken for a
    strategy. One the loader refuses, or one larger than
    :data:`MAX_CONFIG_BYTES`, is reported with the reason, not dropped: a draft
    written from a video is exactly the file most likely to be wrong, and
    silence about it would read as "no such strategy".

    Args:
        root: The project root.

    Returns:
        The strategies that loaded, in file-name order, and the files that did
        not as ``{"config": ..., "reason": ...}``. Every strategy's state is
        ``draft``: nothing in this project can deploy one yet.
    """
    loaded: list[dict[str, Any]] = []
    refused: list[dict[str, str]] = []
    for path in sorted((root / CONFIGS_DIR).glob("*.yaml")):
        relative = path.relative_to(root)
        if relative == LIMITS_FILE:
            continue
        try:
            size = path.stat().st_size
            if size > MAX_CONFIG_BYTES:
                raise StrategyConfigError(
                    f"the file is {size} bytes, more than the {MAX_CONFIG_BYTES} a strategy "
                    "config is read up to"
                )
            definition = load_strategy(path)
            config_hash = config_content_hash(path)
        except (StrategyConfigError, OSError) as exc:
            refused.append({"config": relative.as_posix(), "reason": plain_reason(root, exc)})
            continue
        loaded.append(
            {
                "id": path.stem,
                "name": definition.name,
                "description": " ".join(definition.description.split()),
                "config": relative.as_posix(),
                "state": "draft",
                "latest_search": _latest_search(root, definition.name, config_hash),
            }
        )
    return loaded, refused


def read_overview(root: Path, environ: Mapping[str, str]) -> dict[str, Any]:
    """Read everything the app's first screen shows.

    Args:
        root: The project root - the directory holding ``protocol.yaml``.
        environ: The environment to look for venue settings in. Passed in
            rather than read from ``os.environ`` so a test can supply one.

    Returns:
        A JSON-ready mapping: the protocol version, how many times the
        held-back window has been read, the cached prices, the kill switch, what
        the environment says about the venue, the strategies, and the configs
        that would not load. The venue block holds the value of
        ``T212_ENVIRONMENT`` (or ``None``) and whether ``T212_API_KEY`` is set;
        it never holds the key.

    Raises:
        ProtocolError: If ``protocol.yaml`` is missing or invalid. Nothing else
            here means anything without it, so it is not turned into a missing
            figure.
    """
    protocol = load_protocol(root / PROTOCOL_FILE)
    strategies, refused = _strategies(root)
    return {
        "protocol_version": protocol.protocol_version,
        "held_back_reads": protocol.walk_forward_touches,
        "prices": _prices(root, protocol),
        "kill_switch": _kill_switch(root),
        "venue": {
            "environment": environ.get("T212_ENVIRONMENT") or None,
            "key_present": bool(environ.get("T212_API_KEY", "").strip()),
        },
        "strategies": strategies,
        "unreadable_configs": refused,
    }

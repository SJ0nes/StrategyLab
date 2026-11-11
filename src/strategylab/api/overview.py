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

HOW FILES ARE READ, and how a reason is worded, is in
:mod:`strategylab.api.reading`. Which strategies there are is in
:mod:`strategylab.api.registry`, and what has been run for each in
:mod:`strategylab.api.history`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from strategylab.api.drafts import confirmation, read_source
from strategylab.api.history import latest_search
from strategylab.api.reading import missing, plain_reason
from strategylab.api.registry import LIMITS_FILE, list_strategies
from strategylab.api.rules import describe
from strategylab.api.window_state import tour_seen
from strategylab.data.source import DataQualityError, cache_read
from strategylab.protocol import Protocol, load_protocol
from strategylab.risk.limits import load_limits

#: Where each thing lives, relative to the project root. The same places the
#: commands use, which they name relative to the working directory.
PROTOCOL_FILE = Path("protocol.yaml")
DATA_DIR = Path("data")


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
        return missing(f"No price data for {ticker} has been fetched.")
    except (DataQualityError, OSError, ValueError) as exc:
        reason = plain_reason(root, exc)
        return missing(f"The stored price data for {ticker} cannot be used: {reason}")
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
        return missing(f"The risk limits file cannot be read: {plain_reason(root, exc)}")


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
        that would not load. Every strategy's state is ``draft``: nothing in
        this project can deploy one yet. Each strategy carries its rules in
        words (:func:`~strategylab.api.rules.describe`), where it came from
        (:func:`~strategylab.api.drafts.read_source`) and whether its rules
        are confirmed (:func:`~strategylab.api.drafts.confirmation`).
        ``tour_seen`` says whether the window's tour has been seen
        (:mod:`strategylab.api.window_state`). The
        venue block holds the value of
        ``T212_ENVIRONMENT`` (or ``None``) and whether ``T212_API_KEY`` is set;
        it never holds the key.

    Raises:
        ProtocolError: If ``protocol.yaml`` is missing or invalid. Nothing else
            here means anything without it, so it is not turned into a missing
            figure.
    """
    protocol = load_protocol(root / PROTOCOL_FILE)
    loaded, refused = list_strategies(root)
    strategies = [
        {
            "id": strategy.id,
            "name": strategy.name,
            "description": strategy.description,
            "config": strategy.config,
            "config_hash": strategy.config_hash,
            "state": "draft",
            "latest_search": latest_search(root, strategy),
            "rules": describe(strategy.definition),
            "source": read_source(root, strategy),
            **confirmation(root, strategy),
        }
        for strategy in loaded
    ]
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
        "tour_seen": tour_seen(root),
    }

"""What has been run: the searches, the rejected candidates, the held-back reads.

Every function here reads files a command wrote under ``state/`` and passes on
what they record. None runs a backtest, and none computes a metric: a Sharpe or
a drawdown shown in the app is the one the search wrote down. The single
derived figure is a cell's total return, and :func:`_total_return_pct` says how.

THE BUY-AND-HOLD LINE IS NOT PASSED ON. A search artifact records the
instrument's own closes beside its equity curves, and a benchmark block. The
operator chose on 2026-11-11 to keep that comparison out of the app
(``docs/decisions/0014``), so neither leaves this module. The artifact still
holds both.

A FILE THAT CANNOT BE READ IS LISTED, NOT DROPPED. A history with a gap that
does not say it is a gap reads as a complete history. A search written in an
older format, or a file that is not an artifact at all, appears by name with
the reason it was not read. Only three fields of such a file are ever looked
at: its schema version, the strategy it names, and when it says it was written.

A NAME FROM A REQUEST IS LOOKED UP, NEVER OPENED. :func:`search_detail` compares
the name against the names of the files that are there and builds no path from
it.

THE GRAVEYARD BELONGS TO THE PROJECT, NOT TO A STRATEGY. Its records carry a
parameter combination and a reason and nothing that says which strategy the
combination was for, so :func:`read_graveyard` reports them for the project as
a whole and does not guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from strategylab.api.reading import (
    Unreadable,
    count_or_none,
    figure_or_none,
    missing,
    params_or_none,
    parse_json,
    read_json_object,
    text_or_none,
)
from strategylab.api.registry import StrategyFile
from strategylab.search.graveyard import GRAVEYARD_SCHEMA_VERSION, REJECTION_REASONS
from strategylab.search.loop import SEARCH_SCHEMA_VERSION
from strategylab.search.noise import GATE_STATUSES
from strategylab.validate.walkforward import VALIDATION_SCHEMA_VERSION, Outcome

#: Where commands write their results, relative to the project root.
STATE_DIR = Path("state")

#: The graveyard, relative to the project root.
GRAVEYARD_FILE = STATE_DIR / "graveyard.jsonl"

#: Largest search or validation artifact read, in bytes. A ninety-cell search
#: artifact with every equity curve is under a megabyte.
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

#: Largest graveyard read, in bytes. One record is about two hundred bytes.
MAX_GRAVEYARD_BYTES = 32 * 1024 * 1024

#: What a cell is marked as when it is the one the search put forward.
CANDIDATE = "candidate"

#: The outcomes a validation can record.
EXAM_OUTCOMES = frozenset(outcome.value for outcome in Outcome)


@dataclass(frozen=True)
class ResultFile:
    """One result file under ``state/``, read or not.

    Attributes:
        name: The file's name.
        payload: The parsed artifact, or ``None`` when it was not read.
        reason: Why it was not read, or ``None`` when it was.
        strategy: The strategy name the file records, when that much could be
            told. ``None`` otherwise.
        generated_at: When the file says it was written, when that much could
            be told. ``None`` otherwise.
    """

    name: str
    payload: dict[str, Any] | None
    reason: str | None
    strategy: str | None
    generated_at: str | None


def _result_files(root: Path, pattern: str, version: int) -> list[ResultFile]:
    """Read every result file matching a pattern, newest name first.

    Args:
        root: The project root.
        pattern: A glob under ``state/``, such as ``search-*.json``. The files'
            names carry a UTC timestamp, so name order is time order.
        version: The schema version this code reads.

    Returns:
        One :class:`ResultFile` per file. A file is read only if it is a JSON
        object no larger than :data:`MAX_ARTIFACT_BYTES` that declares
        ``version``.
    """
    files: list[ResultFile] = []
    for path in sorted((root / STATE_DIR).glob(pattern), reverse=True):
        try:
            payload = read_json_object(path, MAX_ARTIFACT_BYTES)
        except Unreadable as exc:
            files.append(ResultFile(path.name, None, str(exc), None, None))
            continue
        strategy = text_or_none(payload.get("strategy"))
        generated_at = text_or_none(payload.get("generated_at"))
        declared = payload.get("schema_version")
        if declared != version:
            reason = (
                f"written in another format (version {declared!r}); this app reads "
                f"version {version}"
            )
            files.append(ResultFile(path.name, None, reason, strategy, generated_at))
            continue
        files.append(ResultFile(path.name, payload, None, strategy, generated_at))
    return files


def _belongs(file: ResultFile, strategy: StrategyFile) -> bool:
    """Say whether a result file is listed under a strategy.

    Args:
        file: The result file.
        strategy: The strategy.

    Returns:
        True when the file names this strategy, and also when it names none -
        a file that cannot say whose it is is shown everywhere, not nowhere.
    """
    return file.strategy is None or file.strategy == strategy.name


def _unread(file: ResultFile) -> dict[str, Any]:
    """Render a file that was not read, for a history list.

    Args:
        file: A result file whose ``payload`` is ``None``.

    Returns:
        Its name, when it says it was written if that could be told, and why
        it was not read.
    """
    return {
        "artifact": file.name,
        "readable": False,
        "generated_at": file.generated_at,
        "reason": file.reason,
    }


def _search_summary(file: ResultFile, config_hash: str) -> dict[str, Any] | None:
    """Report what one search recorded about its outcome.

    Args:
        file: A search artifact that was read.
        config_hash: Content hash of the strategy's config as it is now.

    Returns:
        The artifact's file name, when it was generated, how many cells it ran,
        its candidate's parameters in BARS, the candidate's annualised Sharpe
        and largest drawdown as a PERCENTAGE, the noise gate as recorded, and
        whether the config has changed since. A search that put no candidate
        forward reports ``None`` for the candidate and its two figures.
        ``None`` when the artifact carries no noise gate with a status from
        :data:`~strategylab.search.noise.GATE_STATUSES`, which no artifact
        of the current schema lacks. A field of the wrong kind - a list
        where the Sharpe belongs - is reported as ``None``, not passed on.
    """
    payload = file.payload if file.payload is not None else {}
    gate = payload.get("noise_gate")
    if not isinstance(gate, dict) or text_or_none(gate.get("status")) not in GATE_STATUSES:
        return None
    raw_winner = payload.get("winner")
    winner: dict[str, Any] = raw_winner if isinstance(raw_winner, dict) else {}
    return {
        "artifact": file.name,
        "readable": True,
        "generated_at": file.generated_at,
        "cells": count_or_none(payload.get("grid_size")),
        "candidate": params_or_none(winner.get("params")),
        "sharpe": figure_or_none(winner.get("sharpe")),
        "max_drawdown_pct": figure_or_none(winner.get("max_drawdown_pct")),
        "gate": {
            "status": gate["status"],
            "beaten": count_or_none(gate.get("beaten")),
            "required": count_or_none(gate.get("required")),
            "scoreable_trials": count_or_none(gate.get("scoreable_trials")),
            "summary": text_or_none(gate.get("summary")) or "",
        },
        "config_changed_since": payload.get("config_content_hash") != config_hash,
    }


def list_searches(root: Path, strategy: StrategyFile) -> list[dict[str, Any]]:
    """List every search on record for a strategy, newest first.

    Args:
        root: The project root.
        strategy: The strategy.

    Returns:
        One entry per ``state/search-*.json`` that names this strategy or names
        none. A file that was read is summarised as :func:`_search_summary`
        describes, with ``readable`` true. One that was not is listed with
        ``readable`` false and the reason.
    """
    listed: list[dict[str, Any]] = []
    for file in _result_files(root, "search-*.json", SEARCH_SCHEMA_VERSION):
        if not _belongs(file, strategy):
            continue
        summary = None if file.payload is None else _search_summary(file, strategy.config_hash)
        if summary is None:
            reason = file.reason or "the file records no noise gate this app knows"
            listed.append(_unread(ResultFile(file.name, None, reason, None, file.generated_at)))
        else:
            listed.append(summary)
    return listed


def latest_search(root: Path, strategy: StrategyFile) -> dict[str, Any] | None:
    """Find the newest search for a strategy that could be read.

    Args:
        root: The project root.
        strategy: The strategy.

    Returns:
        The first readable entry of :func:`list_searches` that names this
        strategy, or ``None`` when there is none. Files that could not be read
        are passed over, not guessed at.
    """
    for file in _result_files(root, "search-*.json", SEARCH_SCHEMA_VERSION):
        if file.payload is None or file.strategy != strategy.name:
            continue
        summary = _search_summary(file, strategy.config_hash)
        if summary is not None:
            return summary
    return None


def _total_return_pct(curve: list[Any] | None, starting_equity: float) -> float | None:
    """Work out a cell's total return from its recorded equity curve.

    THE ONE FIGURE THIS MODULE DERIVES. The artifact records each cell's equity
    through the window but not the return over it, and no function in
    ``backtest/metrics.py`` computes one. It is the curve's last recorded point
    against the equity the backtest started with. The artifact's curves are
    thinned for drawing but always keep the final bar, and are rounded to two
    decimal places of the account currency, so on a starting equity of ten
    thousand the result is exact to about a ten-thousandth of a percent.

    Args:
        curve: The cell's equity at each recorded date, ``None`` before the
            strategy's warm-up ends. ``None`` for a cell whose backtest failed.
        starting_equity: The equity every cell's backtest started with, in the
            account currency.

    Returns:
        ``(last / starting_equity - 1) * 100``, a PERCENTAGE, where ``last`` is
        the final entry of ``curve``. ``None`` when there is no curve, its final
        entry is not a number, or ``starting_equity`` is not positive.
    """
    if not curve or starting_equity <= 0:
        return None
    last = curve[-1]
    if isinstance(last, bool) or not isinstance(last, (int, float)):
        return None
    return (last / starting_equity - 1) * 100


def _figure(value: Any) -> float:
    """Read a field that has to be a number.

    Args:
        value: A field read from an artifact.

    Returns:
        The number, as a float.

    Raises:
        TypeError: If ``value`` is not an integer or a float. Text is refused
            even when it spells a number: ``float("inf")`` is a number Python
            will make from text, and not one a search wrote.
    """
    figure = figure_or_none(value)
    if figure is None:
        raise TypeError("not a number")
    return float(figure)


def _detail(file: ResultFile, config_hash: str) -> dict[str, Any]:
    """Lay one search artifact out for the app's grid.

    Args:
        file: A search artifact that was read.
        config_hash: Content hash of the strategy's config as it is now.

    Returns:
        See :func:`search_detail`.

    Raises:
        KeyError, TypeError, ValueError, IndexError: If the artifact lacks a
            field a search artifact always has, or holds one of the wrong kind.
    """
    payload = file.payload if file.payload is not None else {}
    summary = _search_summary(file, config_hash)
    if summary is None:
        raise KeyError("noise_gate")

    cells = payload["cells"]
    curves = payload["equity_curves"]
    starting_equity = _figure(payload["starting_equity"])
    parameters = [str(name) for name in cells[0]["params"]] if cells else []

    def key(params: dict[str, Any]) -> tuple[tuple[str, int], ...]:
        return tuple(sorted((str(name), int(value)) for name, value in params.items()))

    marks = {key(entry["params"]): str(entry["reason"]) for entry in payload["rejections"]}
    if summary["candidate"] is not None:
        marks[key(summary["candidate"])] = CANDIDATE

    laid_out = []
    # strict: an artifact whose curves do not pair one-to-one with its cells is
    # refused here, with a ValueError, not drawn with the wrong curve on a cell.
    for cell, curve in zip(cells, curves["cells"], strict=True):
        laid_out.append(
            {
                "params": {name: int(cell["params"][name]) for name in parameters},
                "sharpe": figure_or_none(cell["sharpe"]),
                "max_drawdown_pct": figure_or_none(cell["max_drawdown_pct"]),
                "trade_count": int(cell["trade_count"]),
                "plateau_score": figure_or_none(cell["plateau_score"]),
                "neighbour_count": int(cell["neighbour_count"]),
                "total_return_pct": _total_return_pct(curve, starting_equity),
                "mark": marks.get(key(cell["params"])),
                "curve": None if curve is None else [figure_or_none(point) for point in curve],
            }
        )

    ceiling = payload.get("noise_ceiling")
    window = payload["window"]
    return {
        **summary,
        "available": True,
        "instrument": str(payload["instrument"]),
        "window": {
            "start": str(window["start"]),
            "end": str(window["end"]),
            "bars": int(window["bars"]),
        },
        "starting_equity": starting_equity,
        "max_drawdown_ceiling_pct": _figure(payload["max_drawdown_ceiling_pct"]),
        "parameters": parameters,
        "axes": {
            name: sorted({int(cell["params"][name]) for cell in cells}) for name in parameters
        },
        "dates": [str(date) for date in curves["dates"]],
        "cells": laid_out,
        "rejections": [
            {
                "params": {str(name): int(value) for name, value in entry["params"].items()},
                "reason": str(entry["reason"]),
                "detail": str(entry["detail"]),
            }
            for entry in payload["rejections"]
        ],
        "ceiling": None
        if not isinstance(ceiling, dict)
        else {
            "trials": count_or_none(ceiling["trials"]),
            "scoreable_trials": count_or_none(ceiling["scoreable_trials"]),
            "percentiles": {
                str(name): figure_or_none(value) for name, value in ceiling["percentiles"].items()
            },
            "max": figure_or_none(ceiling["max"]),
        },
    }


def search_detail(root: Path, strategy: StrategyFile, artifact: str) -> dict[str, Any] | None:
    """Report one search in full: every cell, its curve, and what was rejected.

    Args:
        root: The project root.
        strategy: The strategy the search must be for.
        artifact: The artifact's file name, as :func:`list_searches` gave it.
            Compared against the files that are there; no path is built from
            it.

    Returns:
        ``None`` when no search artifact of that name records this strategy.
        The missing shape when the file is there but could not be read, or
        lacks a field a search artifact always has. Otherwise everything
        :func:`_search_summary` returns - except that ``cells`` is the list
        below, not the summary's count - plus:

        - ``instrument`` and ``window`` - what was searched, and over which
          dates and how many bars;
        - ``starting_equity`` - what each backtest started with, in the account
          currency;
        - ``max_drawdown_ceiling_pct`` - the protocol's ceiling, a PERCENTAGE;
        - ``parameters`` - the searched parameter names, in the order the
          config declares them - and ``axes``, the values each took, ascending,
          in BARS;
        - ``dates`` - the ISO dates the curves are recorded on;
        - ``cells`` - per combination: ``params``; ``sharpe`` (annualised, or
          ``None`` when its backtest failed); ``max_drawdown_pct``, a
          PERCENTAGE; ``trade_count``; ``plateau_score``; ``neighbour_count``;
          ``total_return_pct``, a PERCENTAGE, from :func:`_total_return_pct`;
          ``mark``, which is :data:`CANDIDATE`, a rejection reason, or
          ``None``; and ``curve``, the equity at each of ``dates``;
        - ``rejections`` - the combinations the search turned down, each with
          its reason and the search's own sentence about it;
        - ``ceiling`` - the noise ceiling's trial counts, percentiles and
          maximum, or ``None`` when the search ran without one.

        The instrument's closes and the buy-and-hold benchmark are not
        included.
    """
    for file in _result_files(root, "search-*.json", SEARCH_SCHEMA_VERSION):
        if file.name != artifact or not _belongs(file, strategy):
            continue
        if file.payload is None:
            return missing(f"This search could not be read: {file.reason}.")
        try:
            return _detail(file, strategy.config_hash)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            return missing(
                "This search could not be read: the file lacks something a search "
                "result always has."
            )
    return None


def list_exams(root: Path, strategy: StrategyFile) -> list[dict[str, Any]]:
    """List every read of the held-back window on record for a strategy.

    Each is one run of ``strategylab validate``. This function reads the files
    those runs left; it runs nothing and reads no price.

    Args:
        root: The project root.
        strategy: The strategy.

    Returns:
        One entry per ``state/validation-*.json`` that names this strategy or
        names none, newest first. A file that was read reports its name, when
        it was generated, the parameters validated in BARS, the outcome
        (``pass``, ``fail`` or ``insufficient_data``) and the run's own reason
        for it, the pooled annualised Sharpe, the pooled largest drawdown as a
        PERCENTAGE, the pooled trade count, the number of folds, and which
        read of the held-back window it was. One that was not, or that records
        an outcome outside those three, is listed with ``readable`` false and
        the reason.
    """
    listed: list[dict[str, Any]] = []
    for file in _result_files(root, "validation-*.json", VALIDATION_SCHEMA_VERSION):
        if not _belongs(file, strategy):
            continue
        if file.payload is None:
            listed.append(_unread(file))
            continue
        payload = file.payload
        raw_pooled = payload.get("pooled")
        pooled: dict[str, Any] = raw_pooled if isinstance(raw_pooled, dict) else {}
        folds = payload.get("folds")
        outcome = text_or_none(payload.get("outcome"))
        if outcome not in EXAM_OUTCOMES:
            unknown = "the file records no outcome this app knows"
            listed.append(_unread(ResultFile(file.name, None, unknown, None, file.generated_at)))
            continue
        listed.append(
            {
                "artifact": file.name,
                "readable": True,
                "generated_at": file.generated_at,
                "params": params_or_none(payload.get("params")),
                "outcome": outcome,
                "reason": text_or_none(payload.get("reason")) or "",
                "sharpe": figure_or_none(pooled.get("sharpe")),
                "max_drawdown_pct": figure_or_none(pooled.get("max_drawdown_pct")),
                "trades": count_or_none(pooled.get("trades")),
                "folds": len(folds) if isinstance(folds, list) else None,
                "read_number": count_or_none(payload.get("walk_forward_touches")),
            }
        )
    return listed


def read_graveyard(root: Path) -> dict[str, Any]:
    """Report the project's rejected candidates, each distinct one once.

    A search re-records its rejections every time it runs, so the file holds
    the same combination many times. This collapses them: one entry per
    distinct combination and reason, with how many times it was recorded and
    when it last was.

    Args:
        root: The project root.

    Returns:
        ``records``, the number of lines read as rejections; ``unread_lines``,
        the number of non-blank lines that were not - not JSON, not an object,
        of another schema version, or carrying a reason outside the closed
        vocabulary; and ``entries``, most recently recorded first, each with
        ``candidate`` (parameters in BARS), ``reason``, ``times``,
        ``last_recorded_at``, and the ``sharpe`` (annualised) and
        ``max_drawdown_pct`` (a PERCENTAGE) of its latest record, either of
        which may be ``None``. A project with no graveyard reports zero records
        and no entries. The missing shape when the file is there but cannot be
        opened or is larger than :data:`MAX_GRAVEYARD_BYTES`.
    """
    path = root / GRAVEYARD_FILE
    if not path.exists():
        return {"available": True, "records": 0, "unread_lines": 0, "entries": []}
    try:
        size = path.stat().st_size
        if size > MAX_GRAVEYARD_BYTES:
            return missing(
                f"The record of rejected versions is {size} bytes, more than the "
                f"{MAX_GRAVEYARD_BYTES} read."
            )
        # Read as bytes and decoded a line at a time, so that one line that is
        # not text costs that line and not the file.
        lines = path.read_bytes().splitlines()
    except OSError:
        return missing("The record of rejected versions could not be opened.")

    records = 0
    unread = 0
    entries: dict[tuple[tuple[tuple[str, int], ...], str], dict[str, Any]] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = parse_json(line.decode("utf-8"))
            if not isinstance(record, dict):
                raise ValueError("not an object")
            if record["schema_version"] != GRAVEYARD_SCHEMA_VERSION:
                raise ValueError("another schema version")
            reason = record["reason"]
            if reason not in REJECTION_REASONS:
                raise ValueError("a reason outside the vocabulary")
            candidate = params_or_none(record["candidate"])
            recorded_at = record["recorded_at"]
            if candidate is None or not isinstance(recorded_at, str):
                raise TypeError("a field of the wrong kind")
            metrics = record["metrics"]
            sharpe = figure_or_none(metrics.get("sharpe"))
            drawdown = figure_or_none(metrics.get("max_drawdown_pct"))
        except (KeyError, TypeError, ValueError, AttributeError):
            unread += 1
            continue
        records += 1
        identity = (tuple(sorted(candidate.items())), reason)
        entry = entries.get(identity)
        if entry is None:
            entries[identity] = {
                "candidate": candidate,
                "reason": reason,
                "times": 1,
                "last_recorded_at": recorded_at,
                "sharpe": sharpe,
                "max_drawdown_pct": drawdown,
            }
            continue
        entry["times"] += 1
        if recorded_at >= entry["last_recorded_at"]:
            entry["last_recorded_at"] = recorded_at
            entry["sharpe"] = sharpe
            entry["max_drawdown_pct"] = drawdown
    ordered = sorted(entries.values(), key=lambda entry: entry["last_recorded_at"], reverse=True)
    return {"available": True, "records": records, "unread_lines": unread, "entries": ordered}

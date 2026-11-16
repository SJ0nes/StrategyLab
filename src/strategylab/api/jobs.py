"""Work the app starts and does not wait for: a search, or a search with a luck test.

A job is a child process that outlives the server which started it. The server
goes when the window closes; a noise ceiling takes most of an hour, and nobody
keeps a window open that long on purpose. ``docs/decisions/0017`` records the
design, and that it is the assistant's.

WHAT A JOB MAY BE IS A CLOSED LIST. :data:`JOB_KINDS` names every kind and the
number of trials each runs. A request chooses a kind by name and a strategy by
id; the command is built here, from the registry's own record of the strategy.
No part of a command line comes from a request, and ``validate`` is not a kind.

ONE AT A TIME. The graveyard is not safe for two writers, and two results
started in the same second would take the same file name. :func:`start_job`
refuses while a job is running, and says which.

FIVE FILES PER JOB, under ``state/jobs/``, all named for the job's id:

- ``<id>.json`` - what was asked for, written once by the server before the
  child starts;
- ``<id>.log`` - everything the command printed;
- ``<id>.beat`` - touched by the runner every few seconds while it is alive;
- ``<id>.done`` - the command's exit code, written by the runner when it ends;
- ``<id>.stop`` - written by :func:`request_stop`; the search looks for it
  between trials.

WHETHER A JOB IS RUNNING IS READ FROM THE HEARTBEAT, NOT FROM A PROCESS ID.
Nothing here looks a process up or signals one. A job with no ``done`` file and
a stale heartbeat is reported as lost - the machine was restarted, or the
runner was killed - and does not block the next job.

THE CHILD RUNS THIS PROJECT'S CODE AND NOTHING FOUND IN THE DIRECTORY IT RUNS IN.
Python started with ``-m`` puts the working directory first on its import
path, so a ``json.py`` lying in a served directory would be imported in place
of the real one. Every command here is started with ``-P`` and with
``PYTHONSAFEPATH`` set, which the search's worker processes inherit. The
child is also started without the server's token and without any venue
setting: a search needs neither, and a process that runs for an hour should
not carry them.

PROGRESS IS READ FROM THE LOG. The search prints one line per finished trial,
written by :func:`trial_line` and read back by :func:`parse_trial_line`, which
are tested against each other so the wording cannot drift from its reader.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from strategylab.api.deployment import (
    ARTIFACT_NAME,
    DeployRefused,
    check_refusal,
    deployed_count,
    read_deployment,
    window_refusal,
)
from strategylab.api.drafts import confirmation
from strategylab.api.history import latest_search
from strategylab.api.reading import (
    Unreadable,
    count_or_none,
    plain_reason,
    read_json_object,
    text_or_none,
)
from strategylab.api.registry import StrategyFile, find_strategy
from strategylab.search.noise import RELIABLE_TRIALS

#: Where job files live, relative to the project root.
JOBS_DIR = Path("state") / "jobs"

#: Schema version of a job record.
JOB_SCHEMA_VERSION = 1

#: The daily check: one bar of ``strategylab run`` for the deployed strategy.
DAILY_CHECK = "daily_check"

#: The final exam: ``strategylab validate`` on a search whose luck test passed.
#: It spends one permanent read of the held-back years (``docs/decisions/0020``).
FINAL_EXAM = "final_exam"

#: What the operator types to take the final exam. Not the strategy's name,
#: which is what deploying asks for: the two must not be confused.
EXAM_PHRASE = "SPEND ONE LOOK"

#: Every kind of job, and the surrogate trials it runs. ``search`` runs none;
#: ``luck_test`` runs the number the noise gate needs to reach a verdict, and
#: no other number is on offer; the daily check is not a search and runs none.
JOB_KINDS: dict[str, int] = {
    "search": 0,
    "luck_test": RELIABLE_TRIALS,
    DAILY_CHECK: 0,
    FINAL_EXAM: 0,
}

#: Exit code the search command ends with when it stopped because it was asked.
STOPPED_EXIT_CODE = 3

#: Seconds between the runner's heartbeats.
BEAT_SECONDS = 5.0

#: Seconds without a heartbeat after which a job with no ``done`` file is taken
#: for lost. Also how long a job that has only just started is given to beat.
STALE_SECONDS = 60.0

#: Largest job record or ``done`` file read, in bytes.
MAX_RECORD_BYTES = 64 * 1024

#: How much of the end of a log is read to find progress and a last line, in
#: bytes. A trial line is under forty.
LOG_TAIL_BYTES = 16 * 1024

#: Longest last line of a log passed on to the app, in characters.
MAX_LAST_LINE = 300

#: How many past jobs :func:`list_jobs` reports.
RECENT_JOBS = 10

#: A job id: a UTC timestamp to the second, and the kind.
_JOB_ID = re.compile(r"\d{8}T\d{6}Z-[a-z_]+")

#: Counts are bounded so that a log line of thousands of digits is not a
#: trial line, and is never handed to ``int``.
_TRIAL_LINE = re.compile(r"surrogate (\d{1,6}) of (\d{1,6}) done")

#: Environment variables the server holds and a job is started without: the
#: launch token, by name, and every venue setting, by prefix.
WITHHELD_VARIABLE = "STRATEGYLAB_APP_TOKEN"
WITHHELD_PREFIX = "T212_"

#: Held while deciding whether a job may start, so two requests to one server
#: cannot both find nothing running.
_STARTING = threading.Lock()


class JobRefused(Exception):
    """A job could not be started or stopped, for a reason the operator can act on."""


def trial_line(finished: int, total: int) -> str:
    """Word the line the search prints as each ceiling trial finishes.

    Args:
        finished: How many trials have finished.
        total: How many there are.

    Returns:
        The line, without markup or a line ending.
    """
    return f"surrogate {finished} of {total} done"


def parse_trial_line(line: str) -> tuple[int, int] | None:
    """Read a line :func:`trial_line` wrote.

    Args:
        line: One line of a job's log.

    Returns:
        ``(finished, total)``, or ``None`` when the line is not a trial line.
    """
    match = _TRIAL_LINE.search(line)
    return None if match is None else (int(match.group(1)), int(match.group(2)))


def stop_file(job_id: str) -> Path:
    """Name the file whose presence asks a job to stop.

    Args:
        job_id: The job's id.

    Returns:
        The path, relative to the project root.
    """
    return JOBS_DIR / f"{job_id}.stop"


def command_for(record: dict[str, Any], root: Path) -> list[str]:
    """Build the command a job runs, from its record and the registry.

    Args:
        record: A job record, as :func:`start_job` wrote it.
        root: The project root.

    Returns:
        The argument list: this interpreter with ``-P``, so that nothing in
        the directory it runs in is importable; the ``search`` command, the
        strategy's config as the registry names it, the kind's trial count, and
        the job's stop file.

    Raises:
        JobRefused: If the record's kind is not in :data:`JOB_KINDS`, its id is
            not a job id, its strategy is not one the registry holds, or the
            strategy's file no longer has the content hash recorded when the
            job was started - the one whose rules were confirmed. The config
            path is taken from the registry, never from the record.
    """
    kind = text_or_none(record.get("kind"))
    job_id = text_or_none(record.get("id"))
    strategy_id = text_or_none(record.get("strategy_id"))
    if kind is None or kind not in JOB_KINDS:
        raise JobRefused("the job's kind is not one this app runs")
    if job_id is None or _JOB_ID.fullmatch(job_id) is None:
        raise JobRefused("the job's id is not a job id")
    strategy = None if strategy_id is None else find_strategy(root, strategy_id)
    if strategy is None:
        raise JobRefused("the job's strategy is not in configs/")
    if record.get("config_hash") != strategy.config_hash:
        raise JobRefused("the strategy file has changed since the job was started")
    if kind == DAILY_CHECK:
        return _daily_check_command(root, strategy)
    if kind == FINAL_EXAM:
        return _final_exam_command(root, record)
    return [
        sys.executable,
        "-P",
        "-m",
        "strategylab.cli",
        "search",
        strategy.config,
        "--trials",
        str(JOB_KINDS[kind]),
        "--stop-file",
        stop_file(job_id).as_posix(),
    ]


def _daily_check_command(root: Path, strategy: StrategyFile) -> list[str]:
    """Build the daily check's command from the deployment record.

    Args:
        root: The project root.
        strategy: The strategy the job is for, as the registry found it.

    Returns:
        This interpreter with ``-P``, ``strategylab run``, the deployed search
        artifact's path under ``state/``, the strategy's id, and how many
        strategies are deployed, so the check trades the strategy's own units
        within its equal share (``docs/decisions/0022``).

    Raises:
        JobRefused: If a check may not run for the strategy now
            (:func:`~strategylab.api.deployment.check_refusal`, asked with this
            process's environment) - it is not the one deployed, is paused,
            stop all is on, its file has changed, or the practice account is
            not set - or the deployment's artifact is not a search file that is
            there, or the deployment record cannot be counted.
    """
    # Asked again here, when the runner is about to start the command and not
    # only when the job was started: stop all, or pause, pressed in between must
    # stop it. The runner's environment is the one the check would trade with.
    refusal = check_refusal(root, strategy.id, os.environ)
    if refusal is not None:
        raise JobRefused(refusal)
    deployed = read_deployment(root, strategy.id)
    if deployed is None or not deployed["available"]:
        raise JobRefused("the job's strategy is not deployed")
    try:
        strategies = deployed_count(root)
    except DeployRefused as exc:
        raise JobRefused(str(exc)) from exc
    artifact = deployed["artifact"]
    if not isinstance(artifact, str) or ARTIFACT_NAME.fullmatch(artifact) is None:
        raise JobRefused("the deployed search's file name is not one this app reads")
    path = Path("state") / artifact
    if not (root / path).is_file():
        raise JobRefused("the deployed search's file is not in state/")
    return [
        sys.executable,
        "-P",
        "-m",
        "strategylab.cli",
        "run",
        path.as_posix(),
        "--strategy-id",
        strategy.id,
        "--strategies",
        str(strategies),
    ]


def exam_refusal(root: Path, strategy: StrategyFile) -> tuple[str | None, str | None]:
    """Say why a strategy may not take the final exam, or which search it would take it on.

    Args:
        root: The project root.
        strategy: The strategy, as the registry found it now.

    Returns:
        ``(None, artifact)`` when the newest readable search on the file as it
        is now passed the luck test and this is not a git worktree; otherwise
        ``(reason, None)``. ``strategylab validate`` checks the gate itself as
        well; this is asked first so the window can say so before anything
        starts.
    """
    if (root / ".git").is_file():
        return (
            "this is a git worktree, whose protocol keeps its own count of looks; "
            "take the final exam from the main checkout",
            None,
        )
    search = latest_search(root, strategy)
    if search is None or search["config_changed_since"]:
        return "no search on the strategy file as it is now", None
    if search["gate"]["status"] != "passed":
        return "its newest search did not pass the luck test", None
    artifact = str(search["artifact"])
    if ARTIFACT_NAME.fullmatch(artifact) is None:
        return "the search's file name is not one this app reads", None
    return None, artifact


def _final_exam_command(root: Path, record: dict[str, Any]) -> list[str]:
    """Build the final exam's command from its record.

    Args:
        root: The project root.
        record: The job record, naming the search it was started on.

    Returns:
        This interpreter with ``-P``, ``strategylab validate``, and the search's
        path under ``state/``.

    Raises:
        JobRefused: If the record names no search file, or one that is not
            there.
    """
    artifact = record.get("artifact")
    if not isinstance(artifact, str) or ARTIFACT_NAME.fullmatch(artifact) is None:
        raise JobRefused("the exam's search file name is not one this app reads")
    path = Path("state") / artifact
    if not (root / path).is_file():
        raise JobRefused("the exam's search file is not in state/")
    return [sys.executable, "-P", "-m", "strategylab.cli", "validate", path.as_posix()]


def job_environment(environ: Mapping[str, str], venue: bool = False) -> dict[str, str]:
    """Make the environment a job is started with.

    Args:
        environ: The server's own environment.
        venue: Whether to keep the practice account's settings.

    Returns:
        A copy without :data:`WITHHELD_VARIABLE` and - unless ``venue`` is
        true - without any variable whose name starts with
        :data:`WITHHELD_PREFIX`, compared without regard to case as Windows
        compares them, and with ``PYTHONSAFEPATH`` set so that no Python
        process the job starts imports from its working directory. ``venue``
        is true for the daily check alone, which trades on the practice
        account and needs its settings; a search never does.
    """
    kept = {
        name: value
        for name, value in environ.items()
        if name.upper() != WITHHELD_VARIABLE
        and (venue or not name.upper().startswith(WITHHELD_PREFIX))
    }
    kept["PYTHONSAFEPATH"] = "1"
    return kept


def _spawn_detached(command: list[str], root: Path, log: Path, venue: bool = False) -> None:
    """Start a process that goes on running after this one ends.

    Its standard input is closed and its output goes to ``log``, so it holds no
    pipe to this process and is not ended by this process going away. Its
    environment is :func:`job_environment`'s, with the practice account's
    settings only when ``venue`` is true.

    Args:
        command: The argument list. Never run through a shell.
        root: The directory to run it in.
        log: The file its output is appended to.
        venue: Whether the process may see the practice account's settings.

    Raises:
        OSError: If the process cannot be started.
    """
    with log.open("ab") as output:
        options: dict[str, Any] = {
            "cwd": root,
            "env": job_environment(os.environ, venue=venue),
            "stdin": subprocess.DEVNULL,
            "stdout": output,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            # No console window, and its own process group so that a Ctrl+C
            # meant for the server does not reach it.
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                # The launcher that started the server may hold it in a Windows
                # job that ends every member when the launcher exits. Ask to be
                # started outside it; where that is not allowed, start inside.
                subprocess.Popen(
                    command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options
                )
            except PermissionError:
                subprocess.Popen(command, creationflags=flags, **options)
        else:
            subprocess.Popen(command, start_new_session=True, **options)


def _tail(path: Path) -> list[str]:
    """Read the last lines of a log.

    Args:
        path: The log.

    Returns:
        The non-blank lines within the last :data:`LOG_TAIL_BYTES` of the file,
        oldest first, with bytes that are not text replaced. Empty when the
        file cannot be read.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - LOG_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _without_places(root: Path, line: str) -> str:
    """Take the directories a traceback names out of a line of a log.

    Args:
        root: The project root.
        line: One line of a job's log.

    Returns:
        The line with the project root, the directory this package is
        installed from and the home directory each written as ``.``. A search
        that fails prints the file that raised; the three are where a path
        naming a person comes from. An interpreter installed outside all
        three is still named.
    """
    places = [root, Path(__file__).parents[3], Path.home()]
    for place in places:
        line = plain_reason(place, Exception(line))
    return line


def _age(path: Path, now: datetime) -> float | None:
    """Say how long ago a file was last written.

    Args:
        path: The file.
        now: The present moment, timezone-aware.

    Returns:
        Seconds since the file's modification time, or ``None`` when there is
        no such file or the file is dated more than :data:`STALE_SECONDS`
        after ``now``. A file dated a moment after ``now`` is zero seconds old.
    """
    try:
        age = now.timestamp() - path.stat().st_mtime
    except OSError:
        return None
    # A file dated well into the future says nothing about now: the clock was
    # set back, or the file was written by hand. It is not counted as fresh.
    # One dated a moment ahead is the ordinary case of a file written just
    # after ``now`` was read, and is as fresh as a file can be.
    if age < -STALE_SECONDS:
        return None
    return max(age, 0.0)


def read_job(root: Path, job_id: str, now: datetime) -> dict[str, Any] | None:
    """Report one job: what was asked for, and where it has got to.

    Args:
        root: The project root.
        job_id: The job's id. Checked to have the shape of one before any path
            is built from it.
        now: The present moment, timezone-aware. Passed in so a test can say
            what time it is.

    Returns:
        ``None`` when ``job_id`` is not a job id, no readable record of that
        name exists, or the record inside names a different job. Otherwise
        the job's ``id``, ``kind``, ``strategy_id``,
        ``trials`` and ``started_at`` as recorded; its ``status``; ``progress``
        as ``{"finished": n, "total": m}`` trials, or ``None`` when the log
        holds no trial line; and ``last_line``, the log's last line with the
        directories :func:`_without_places` lists removed, cut to :data:`MAX_LAST_LINE` characters,
        or ``None`` for an empty log.

        ``status`` is ``finished`` (exit code 0), ``stopped`` (it ended
        because it was asked), ``failed`` (any other exit code, or a ``done``
        file that cannot be read), ``running``, ``stopping`` (running, with a
        stop asked for), or ``lost`` (no ``done`` file and no heartbeat for
        :data:`STALE_SECONDS`).
    """
    if _JOB_ID.fullmatch(job_id) is None:
        return None
    directory = root / JOBS_DIR
    try:
        record = read_json_object(directory / f"{job_id}.json", MAX_RECORD_BYTES)
    except Unreadable:
        return None
    kind = text_or_none(record.get("kind"))
    if record.get("schema_version") != JOB_SCHEMA_VERSION or kind not in JOB_KINDS:
        return None
    if record.get("id") != job_id:
        return None

    done = directory / f"{job_id}.done"
    if done.exists():
        try:
            code = count_or_none(read_json_object(done, MAX_RECORD_BYTES).get("exit_code"))
        except Unreadable:
            code = None
        status = "finished" if code == 0 else "stopped" if code == STOPPED_EXIT_CODE else "failed"
    else:
        beat = _age(directory / f"{job_id}.beat", now)
        asked = _age(directory / f"{job_id}.json", now)
        ages = [age for age in (beat, asked) if age is not None]
        if not ages or min(ages) > STALE_SECONDS:
            status = "lost"
        elif (root / stop_file(job_id)).exists():
            status = "stopping"
        else:
            status = "running"

    lines = _tail(directory / f"{job_id}.log")
    trials = [parsed for parsed in map(parse_trial_line, lines) if parsed is not None]
    last = _without_places(root, lines[-1])[:MAX_LAST_LINE] if lines else None
    return {
        "id": job_id,
        "kind": kind,
        "strategy_id": text_or_none(record.get("strategy_id")),
        "trials": count_or_none(record.get("trials")),
        "started_at": text_or_none(record.get("started_at")),
        "status": status,
        "progress": None if not trials else {"finished": trials[-1][0], "total": trials[-1][1]},
        "last_line": last,
    }


def list_job_records(root: Path) -> list[dict[str, str | None]]:
    """List every job on record by id, kind and strategy, without reading its state.

    Args:
        root: The project root.

    Returns:
        ``{"id", "kind", "strategy_id"}`` for each record whose file name is a job id, whose
        content names that same id, is of this schema version, and has a kind
        in :data:`JOB_KINDS`; ``strategy_id`` is ``None`` when the record names
        none. Newest first.
    """
    kept: list[dict[str, str | None]] = []
    for path in sorted((root / JOBS_DIR).glob("*.json"), reverse=True):
        if _JOB_ID.fullmatch(path.stem) is None:
            continue
        try:
            record = read_json_object(path, MAX_RECORD_BYTES)
        except Unreadable:
            continue
        kind = text_or_none(record.get("kind"))
        if (
            record.get("schema_version") == JOB_SCHEMA_VERSION
            and record.get("id") == path.stem
            and kind in JOB_KINDS
        ):
            kept.append(
                {
                    "id": path.stem,
                    "kind": str(kind),
                    "strategy_id": text_or_none(record.get("strategy_id")),
                }
            )
    return kept


def list_jobs(root: Path, now: datetime) -> dict[str, Any]:
    """Report the job that is running, if any, and the most recent ones.

    Args:
        root: The project root.
        now: The present moment, timezone-aware.

    Returns:
        ``current``, the newest running or stopping job among ALL records,
        or ``None``; and ``recent``, up to :data:`RECENT_JOBS` jobs, newest
        first.
    """
    directory = root / JOBS_DIR
    names = sorted((path.stem for path in directory.glob("*.json")), reverse=True)
    recent: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    # Every record is read, not only the newest few: a running job must be
    # found however many later-dated records there are, or a second could start.
    for name in names:
        job = read_job(root, name, now)
        if job is None:
            continue
        if current is None and job["status"] in ("running", "stopping"):
            current = job
        if len(recent) < RECENT_JOBS:
            recent.append(job)
    return {"current": current, "recent": recent}


def start_job(
    root: Path,
    strategy: StrategyFile,
    kind: str,
    now: datetime,
    spawn: Callable[[list[str], Path, Path], None] | None = None,
    environ: Mapping[str, str] | None = None,
    phrase: str | None = None,
) -> dict[str, Any]:
    """Start a job, unless one is running.

    Writes the job's record, then starts the runner
    (:mod:`strategylab.api.job_runner`) as a process of its own. The runner
    starts the command, beats while it runs, and records how it ended.

    Args:
        root: The project root.
        strategy: The strategy to search, as the registry found it.
        kind: A key of :data:`JOB_KINDS`.
        now: The present moment, timezone-aware. Names the job.
        spawn: Starts the runner. ``None`` starts a real, detached process;
            tests pass a stand-in and start none.
        environ: The server's environment, for whether the practice account
            is set. ``None`` reads this process's own.
        phrase: What the operator typed, for the final exam alone, which needs
            :data:`EXAM_PHRASE`.

    Returns:
        The job, as :func:`read_job` reports it.

    Raises:
        JobRefused: If ``kind`` is not in :data:`JOB_KINDS`, the strategy's
            rules are not confirmed for its file as it is now
            (``docs/decisions/0018``), a daily check may not run for it now
            (:func:`~strategylab.api.deployment.check_refusal`), a job is
            already running - the message names it - or the runner cannot be
            started.
    """
    if kind not in JOB_KINDS:
        raise JobRefused("that is not a kind of job this app runs")
    if not confirmation(root, strategy)["confirmed"]:
        raise JobRefused(
            "the strategy's rules have not been confirmed for the file as it is now; "
            "read them on its Dashboard first"
        )
    artifact: str | None = None
    if kind == FINAL_EXAM:
        if (phrase or "").strip() != EXAM_PHRASE:
            raise JobRefused(f"type {EXAM_PHRASE} to take the final exam")
        reason, artifact = exam_refusal(root, strategy)
        if reason is not None:
            raise JobRefused(reason)
    if kind == DAILY_CHECK:
        refusal = check_refusal(
            root, strategy.id, os.environ if environ is None else environ
        ) or window_refusal(now)
        if refusal is not None:
            raise JobRefused(refusal)
        today = f"{now.astimezone(UTC):%Y%m%d}"
        if any(
            record["kind"] == DAILY_CHECK
            and str(record["id"]).startswith(today)
            and record.get("strategy_id") == strategy.id
            for record in list_job_records(root)
        ):
            raise JobRefused("today's check for this strategy has already been started")
    stamp = now.astimezone(UTC)
    job_id = f"{stamp:%Y%m%dT%H%M%SZ}-{kind}"
    directory = root / JOBS_DIR
    with _STARTING:
        current = list_jobs(root, now)["current"]
        if current is not None:
            raise JobRefused(f"a job is already running: {current['id']}")
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise JobRefused("the folder for job records could not be made") from exc
        record = {
            "schema_version": JOB_SCHEMA_VERSION,
            "id": job_id,
            "kind": kind,
            "strategy": strategy.name,
            "strategy_id": strategy.id,
            "config": strategy.config,
            "config_hash": strategy.config_hash,
            "trials": JOB_KINDS[kind],
            "started_at": stamp.isoformat(),
        }
        if artifact is not None:
            record["artifact"] = artifact
        try:
            with (directory / f"{job_id}.json").open("x", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2)
        except FileExistsError as exc:
            raise JobRefused(f"a job was started this second: {job_id}") from exc
        try:
            (spawn or functools.partial(_spawn_detached, venue=kind == DAILY_CHECK))(
                [sys.executable, "-P", "-m", "strategylab.api.job_runner", job_id],
                root,
                directory / f"{job_id}.log",
            )
        except OSError as exc:
            (directory / f"{job_id}.done").write_text(
                '{"exit_code": null, "finished_at": null}', encoding="utf-8"
            )
            raise JobRefused("the job could not be started") from exc
    started = read_job(root, job_id, now)
    if started is None:
        raise JobRefused("the job's record could not be read back")
    return started


def request_stop(root: Path, job_id: str, now: datetime) -> dict[str, Any]:
    """Ask a running job to stop at its next trial.

    Writes the job's stop file and does nothing else: no process is signalled.
    The search looks for the file each time a trial finishes.

    Args:
        root: The project root.
        job_id: The job's id.
        now: The present moment, timezone-aware.

    Returns:
        The job, as :func:`read_job` reports it, now ``stopping``.

    Raises:
        JobRefused: If there is no such job, it is not running, it runs no
            trials - a plain search has no point at which it looks - or the
            stop file cannot be written or is a link to somewhere else.
    """
    job = read_job(root, job_id, now)
    if job is None:
        raise JobRefused("there is no such job")
    if job["status"] not in ("running", "stopping"):
        raise JobRefused("that job is not running")
    if JOB_KINDS[job["kind"]] == 0:
        raise JobRefused("a search without a luck test takes minutes and cannot be stopped")
    target = root / stop_file(job_id)
    try:
        if target.is_symlink():
            raise JobRefused("the stop request could not be written")
        target.write_text("stop\n", encoding="utf-8")
    except OSError as exc:
        raise JobRefused("the stop request could not be written") from exc
    stopping = read_job(root, job_id, now)
    if stopping is None:
        raise JobRefused("there is no such job")
    return stopping

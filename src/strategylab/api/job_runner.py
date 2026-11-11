"""The process that carries one job: starts its command, beats, records how it ended.

Run as ``python -m strategylab.api.job_runner <job id>`` from the project root,
by :func:`strategylab.api.jobs.start_job` and by nothing else. It is a process
of its own so that the job goes on when the server that started it has gone.

It decides nothing. The command comes from
:func:`~strategylab.api.jobs.command_for`, which builds it from the job's kind
and the registry; this module runs it, touches the job's heartbeat file while it
runs, and writes the exit code when it ends.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from strategylab.api.jobs import (
    BEAT_SECONDS,
    JOBS_DIR,
    MAX_RECORD_BYTES,
    JobRefused,
    command_for,
)
from strategylab.api.reading import Unreadable, read_json_object

#: Exit code of the runner itself when it could not run the job at all.
COULD_NOT_RUN = 2


def _beat(path: Path, stop: threading.Event, interval: float) -> None:
    """Touch a file until told to stop.

    Args:
        path: The heartbeat file.
        stop: Set when the job has ended.
        interval: Seconds between touches.
    """
    while True:
        # One touch refused - a scanner holding the file, a full disk - must
        # not end the heartbeat of a job that is still running.
        with contextlib.suppress(OSError):
            path.touch()
        if stop.wait(interval):
            return


def run_job(
    root: Path,
    job_id: str,
    run: Callable[[list[str], Path], int],
    interval: float = BEAT_SECONDS,
) -> int:
    """Carry one job from its record to its ``done`` file.

    Args:
        root: The project root.
        job_id: The job's id.
        run: Runs the command in ``root`` and returns its exit code. Replaced
            in tests, which start no process.
        interval: Seconds between heartbeats.

    Returns:
        The command's exit code, or :data:`COULD_NOT_RUN` when the record
        could not be read, names something that is not a job this app runs, or
        the command could not be started.
        Either way the code is written to the job's ``done`` file, with the UTC
        time it ended, unless there was no record to find the job by.
    """
    directory = root / JOBS_DIR
    try:
        record = read_json_object(directory / f"{job_id}.json", MAX_RECORD_BYTES)
        if record.get("id") != job_id:
            raise JobRefused("the record names a different job")
        command = command_for(record, root)
    except Unreadable:
        return COULD_NOT_RUN
    except JobRefused as exc:
        print(f"job not run: {exc}", flush=True)
        code = COULD_NOT_RUN
    else:
        ended = threading.Event()
        heartbeat = directory / f"{job_id}.beat"
        # Once here, before the command starts, so the file exists for as long
        # as the command runs and not from whenever the thread first wakes.
        heartbeat.touch()
        beating = threading.Thread(target=_beat, args=(heartbeat, ended, interval), daemon=True)
        beating.start()
        try:
            code = run(command, root)
        except OSError:
            print("job not run: the command could not be started", flush=True)
            code = COULD_NOT_RUN
        finally:
            ended.set()
            beating.join()
    done = {"exit_code": code, "finished_at": datetime.now(UTC).isoformat()}
    (directory / f"{job_id}.done").write_text(json.dumps(done), encoding="utf-8")
    return code


def _run_command(command: list[str], root: Path) -> int:
    """Run a job's command, with its output joining this process's.

    Args:
        command: The argument list. Never run through a shell.
        root: The directory to run it in.

    Returns:
        The command's exit code.
    """
    return subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL, check=False).returncode


if __name__ == "__main__":
    if len(sys.argv) != 2:  # noqa: PLR2004
        sys.exit(COULD_NOT_RUN)
    sys.exit(run_job(Path.cwd(), sys.argv[1], _run_command))

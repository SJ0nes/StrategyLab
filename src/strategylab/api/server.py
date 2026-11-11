"""Running the app's server: one loopback socket, one line saying where, one way to stop.

THE SERVER BINDS ``127.0.0.1`` AND NOTHING ELSE. Not ``0.0.0.0``, not a name
that could resolve elsewhere. Another machine on the network cannot reach it.

IT SAYS WHICH PORT IT GOT. Asked for port 0, the operating system picks a free
one. The process that started the server learns it from the first line of
standard output: ``{"port": N}``. The socket is already listening when that
line is printed, so a reader that connects at once is not refused.

IT STARTS THE DAILY CHECK. While it runs, a thread asks once a minute whether
the deployed strategy's check is due (:mod:`strategylab.api.schedule`) and
starts it if so. That is the only thing the server does without being asked,
and it stops with the server: nothing runs while the app is closed.

IT STOPS WHEN ITS STANDARD INPUT CLOSES. The window holds the other end of that
pipe. When the window exits - cleanly, or by crashing, or by being killed - the
pipe closes and the server shuts down, so no server is left running with nobody
attached. Started by hand in a terminal, it runs until the terminal is closed
or interrupted.

THE LIFELINE FAILS TOWARDS STOPPING. A server with no standard input at all
has nothing to watch and does not start. One whose input cannot be read stops,
exactly as if it had closed. The failure this avoids is a server that outlives
its window, still holding a valid token.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import uvicorn

from strategylab.api.app import create_app
from strategylab.api.schedule import start_if_due

#: Seconds between the scheduler's questions.
SCHEDULE_EVERY_SECONDS = 60.0


def _check_each_minute(root: Path, environ: Mapping[str, str], stopping: threading.Event) -> None:
    """Start the deployed strategy's daily check when it falls due, until told to stop.

    Args:
        root: The project root.
        environ: The server's environment.
        stopping: Set when the server stops.
    """
    while not stopping.wait(SCHEDULE_EVERY_SECONDS):
        try:
            start_if_due(root, datetime.now(UTC), environ)
        except Exception:  # noqa: BLE001
            # Whatever went wrong this minute - a file held open, a damaged
            # record - is tried again the next. An exception that ended this
            # thread would end the daily check for the rest of the session with
            # nothing on screen to say so.
            continue


#: The only address the server listens on.
LOOPBACK = "127.0.0.1"

#: Environment variable the launcher passes the token in. The environment and
#: not the command line, which any process on the machine can read.
TOKEN_VARIABLE = "STRATEGYLAB_APP_TOKEN"

#: Where the built window is, relative to the project root.
WINDOW_FILES = Path("desktop") / "dist"


def _stop_when_closed(lifeline: IO[str], server: uvicorn.Server) -> None:
    """Block until ``lifeline`` reaches end of file, then ask the server to exit.

    Args:
        lifeline: A text stream whose closing means the launcher has gone.
        server: The running server.
    """
    try:
        while lifeline.read(1024):
            pass
    except (OSError, ValueError):
        # Unreadable is treated as closed: see the module docstring.
        pass
    finally:
        server.should_exit = True


def serve(
    root: Path,
    environ: Mapping[str, str],
    port: int = 0,
    *,
    lifeline: IO[str] | None = None,
    announce: IO[str] | None = None,
) -> None:
    """Serve the app's API and window files until the lifeline closes.

    Args:
        root: The project root.
        environ: The environment. Supplies the token under
            :data:`TOKEN_VARIABLE` and the venue settings the overview reports.
        port: Port to listen on. Zero lets the operating system choose.
        lifeline: Stream whose end of file stops the server. Defaults to
            standard input.
        announce: Stream the ``{"port": N}`` line is written to. Defaults to
            standard output.

    Raises:
        ValueError: If the environment holds no token, or one too short, or
            there is no lifeline to watch. Raised before any socket is opened.
        OSError: If the port cannot be bound.
    """
    supplied = environ.get(TOKEN_VARIABLE, "")
    application = create_app(root, supplied, environ, root / WINDOW_FILES)
    watched = sys.stdin if lifeline is None else lifeline
    if watched is None:
        raise ValueError(
            "there is no standard input to watch, so nothing would ever stop this server"
        )

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((LOOPBACK, port))
    server = uvicorn.Server(uvicorn.Config(application, log_level="warning"))

    threading.Thread(
        target=_stop_when_closed,
        args=(watched, server),
        daemon=True,
    ).start()

    stopping = threading.Event()
    threading.Thread(target=_check_each_minute, args=(root, environ, stopping), daemon=True).start()

    out = sys.stdout if announce is None else announce
    listener.listen()
    out.write(json.dumps({"port": listener.getsockname()[1]}) + "\n")
    out.flush()
    try:
        server.run(sockets=[listener])
    finally:
        stopping.set()

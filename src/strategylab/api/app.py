"""The HTTP application the desktop window talks to.

A thin layer. Each route calls a function that a command also calls, or that
only reads, and returns what it returned; none holds strategy, cost, risk or
validation logic. ``docs/decisions/0014`` records why.

THREE CHECKS STAND IN FRONT OF EVERY ROUTE, in :class:`LocalOnly`.

*The Host header must name this machine.* A page on any website can ask a
browser to send a request to ``127.0.0.1``, and by re-pointing its own domain
name at that address can make the browser treat the answer as its own. The
request then arrives with the website's name in its Host header, and is refused.

*An Origin header, when there is one, must name this machine too.* A browser
attaches the page's origin to a request that could change something, and to
every WebSocket. A request carrying some other site's origin did not come from
the window.

*Every* ``/api/`` *route needs the token for this launch.* The process that
started the server made the token and adds it to the window's requests itself;
the page never holds it. The comparison takes the same time whether the token is
nearly right or entirely wrong.

The checks are ASGI middleware and not a decorator on HTTP routes, so that they
cover a WebSocket as well. A WebSocket added later for progress reports would
otherwise have been reachable from any web page, with none of the three applied.

The window's own files are served without the token: they are the same for
everyone and say nothing about the project.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from strategylab.api.deployment import (
    DeployRefused,
    deploy,
    read_deployment,
    read_deployments,
    set_paused,
    set_stop_all,
    stop_all,
    undeploy,
    venue_ready,
)
from strategylab.api.drafts import StaleConfirmation, confirm
from strategylab.api.history import list_exams, list_searches, read_graveyard, search_detail
from strategylab.api.jobs import (
    FINAL_EXAM,
    JOB_KINDS,
    JobRefused,
    exam_refusal,
    list_jobs,
    request_stop,
    start_job,
)
from strategylab.api.live import read_live
from strategylab.api.overview import read_overview
from strategylab.api.reading import plain_reason
from strategylab.api.registry import find_strategy
from strategylab.api.schedule import schedule_report
from strategylab.api.settle import SettleRefused, settle, settlement
from strategylab.api.window_state import mark_tour_seen
from strategylab.protocol import ProtocolError

#: Shortest token accepted, in characters. The window's launcher sends 64
#: hexadecimal characters; anything much shorter is a guessable token or a
#: mistake, and either way is refused before the server starts.
MIN_TOKEN_CHARS = 32

#: Host names that mean "this machine". A request naming any other is refused.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: Scheme a request's token must be presented under.
BEARER = "Bearer "

#: WebSocket close code for "policy violation", sent to a refused socket.
POLICY_VIOLATION = 1008

#: Sent with every response. The page may load only its own files and talk only
#: to this server; it may not be framed, re-based, or made to post a form
#: elsewhere. Sent as headers because a ``<meta>`` policy cannot forbid framing.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
}


def _host_name(header: str) -> str:
    """Return the host part of a Host header, without any port.

    Args:
        header: The header's value, such as ``127.0.0.1:5173``.

    Returns:
        The text before the last colon, lower-cased, or the whole value when it
        carries no port.
    """
    return header.rsplit(":", 1)[0].lower() if ":" in header else header.lower()


class LocalOnly:
    """ASGI middleware: refuse what is not from this machine's window.

    Applies to HTTP requests and WebSockets alike; see the module docstring for
    the three checks. Adds :data:`SECURITY_HEADERS` to every HTTP response,
    refusals included.
    """

    def __init__(self, app: ASGIApp, token: str) -> None:
        """Wrap an application.

        Args:
            app: The application to guard.
            token: The secret every ``/api/`` request must carry.
        """
        self.app = app
        self.expected = token.encode()

    def refusal(self, path: str, headers: Headers) -> tuple[int, str] | None:
        """Decide whether a request is refused.

        Args:
            path: The request path, already percent-decoded.
            headers: The request headers.

        Returns:
            ``None`` to let the request through, or the HTTP status and the
            reason to refuse it with: 403 for a Host or Origin that is not this
            machine, 401 for an ``/api/`` path without the token.
        """
        if _host_name(headers.get("host", "")) not in LOCAL_HOSTS:
            return 403, "this server answers only as 127.0.0.1"
        origin = headers.get("origin")
        if origin is not None and urlsplit(origin).hostname not in LOCAL_HOSTS:
            return 403, "this server answers only its own window"
        if path.startswith("/api/"):
            presented = headers.get("authorization", "")
            given = presented[len(BEARER) :] if presented.startswith(BEARER) else ""
            if not secrets.compare_digest(given.encode(), self.expected):
                return 401, "missing or wrong app token"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one connection. See the class docstring."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        refused = self.refusal(scope["path"], Headers(scope=scope))
        if refused is not None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": POLICY_VIOLATION})
                return
            status, detail = refused
            response = JSONResponse({"detail": detail}, status, headers=SECURITY_HEADERS)
            await response(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(SECURITY_HEADERS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class Typed(BaseModel):
    """What a deploy request carries: the strategy's name, as the operator typed it."""

    typed: str = Field(max_length=200)


class Settle(BaseModel):
    """What a settlement carries: the strategy's name as typed, and the difference shown."""

    typed: str = Field(max_length=200)
    units: float


class Switch(BaseModel):
    """What a stop-all request carries."""

    stopped: bool


class Shown(BaseModel):
    """What a confirmation request carries: the config hash the rules were shown for."""

    config_hash: str = Field(min_length=64, max_length=64)


def create_app(
    root: Path,
    token: str,
    environ: Mapping[str, str],
    window_files: Path | None = None,
) -> FastAPI:
    """Build the application for one launch of the window.

    Args:
        root: The project root - the directory holding ``protocol.yaml``.
        token: The secret every ``/api/`` request must carry. At least
            :data:`MIN_TOKEN_CHARS` characters.
        environ: The environment venue settings are looked for in.
        window_files: Directory holding the built window (``desktop/dist``).
            ``None``, or a directory that does not exist, serves a plain
            message at ``/`` saying the window has not been built.

    Returns:
        The application, wrapped in :class:`LocalOnly`. It has no interactive
        documentation pages and no schema route: both would describe the API to
        anyone who could reach the port.

    Raises:
        ValueError: If ``token`` is shorter than :data:`MIN_TOKEN_CHARS`.
    """
    if len(token) < MIN_TOKEN_CHARS:
        raise ValueError(
            f"the app token is {len(token)} characters; at least {MIN_TOKEN_CHARS} are required"
        )

    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(LocalOnly, token=token)

    @application.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @application.get("/api/overview")
    def overview() -> Any:
        try:
            return read_overview(root, environ)
        except ProtocolError as exc:
            detail = f"protocol.yaml cannot be used: {plain_reason(root, exc)}"
            return JSONResponse({"detail": detail}, status_code=500)

    no_strategy = JSONResponse({"detail": "no such strategy"}, status_code=404)

    @application.get("/api/strategies/{strategy_id}/searches")
    def searches(strategy_id: str) -> Any:
        strategy = find_strategy(root, strategy_id)
        return no_strategy if strategy is None else list_searches(root, strategy)

    @application.get("/api/strategies/{strategy_id}/searches/{artifact}")
    def search(strategy_id: str, artifact: str) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        detail = search_detail(root, strategy, artifact)
        if detail is None:
            return JSONResponse({"detail": "no such search"}, status_code=404)
        return detail

    @application.get("/api/strategies/{strategy_id}/exams")
    def exams(strategy_id: str) -> Any:
        strategy = find_strategy(root, strategy_id)
        return no_strategy if strategy is None else list_exams(root, strategy)

    @application.get("/api/graveyard")
    def graveyard() -> Any:
        return read_graveyard(root)

    @application.get("/api/jobs")
    def jobs() -> Any:
        return list_jobs(root, datetime.now(UTC))

    @application.post("/api/strategies/{strategy_id}/jobs/{kind}")
    def start(strategy_id: str, kind: str) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        if kind not in JOB_KINDS:
            return JSONResponse({"detail": "no such kind of job"}, status_code=404)
        try:
            job = start_job(root, strategy, kind, datetime.now(UTC), environ=environ)
        except JobRefused as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return JSONResponse(job, status_code=201)

    @application.get("/api/strategies/{strategy_id}/final-exam")
    def exam_status(strategy_id: str) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        reason, artifact = exam_refusal(root, strategy)
        return {"refusal": reason, "artifact": artifact}

    @application.post("/api/strategies/{strategy_id}/final-exam")
    def final_exam(strategy_id: str, typed: Typed) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        try:
            job = start_job(
                root, strategy, FINAL_EXAM, datetime.now(UTC), environ=environ, phrase=typed.typed
            )
        except JobRefused as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return JSONResponse(job, status_code=201)

    @application.post("/api/strategies/{strategy_id}/confirm")
    def confirm_rules(strategy_id: str, shown: Shown) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        try:
            return confirm(root, strategy, shown.config_hash, datetime.now(UTC))
        except StaleConfirmation as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except OSError:
            return JSONResponse(
                {"detail": "the confirmation could not be recorded"}, status_code=500
            )

    def refused(exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @application.get("/api/deployment")
    def deployment(strategy_id: str | None = None) -> Any:
        now = datetime.now(UTC)
        everything = read_deployments(root)
        deployed = None if strategy_id is None else read_deployment(root, strategy_id)
        live = None
        count = len(everything["deployments"]) if everything["available"] else 0
        if deployed is not None and deployed["available"]:
            strategy = find_strategy(root, str(deployed["strategy_id"]))
            name = strategy.name if strategy is not None else str(deployed["strategy_id"])
            live = read_live(root, deployed, name, now, strategies=count)
        return {
            "deployment": deployed if everything["available"] else everything,
            "deployed": [
                {
                    "strategy_id": item["strategy_id"],
                    "paused": item["paused"],
                    "proven": item["proven"],
                }
                for item in (everything["deployments"] if everything["available"] else [])
            ],
            "stop_all": stop_all(root),
            "venue_unready": venue_ready(environ),
            "schedule": None
            if strategy_id is None
            else schedule_report(root, strategy_id, now, environ),
            "live": live,
            "settlement": settlement(root, now),
        }

    @application.post("/api/strategies/{strategy_id}/deploy")
    def deploy_strategy(strategy_id: str, typed: Typed) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        try:
            return deploy(root, strategy, typed.typed, environ, datetime.now(UTC))
        except DeployRefused as exc:
            return refused(exc)
        except OSError:
            return JSONResponse({"detail": "the deployment could not be recorded"}, status_code=500)

    @application.post("/api/strategies/{strategy_id}/deployment/{action}")
    def change_deployment(strategy_id: str, action: str) -> Any:
        try:
            if action == "pause":
                return set_paused(root, strategy_id, True, datetime.now(UTC))
            if action == "resume":
                return set_paused(root, strategy_id, False, datetime.now(UTC))
            if action == "undeploy":
                undeploy(root, strategy_id)
                return {"deployment": None}
        except DeployRefused as exc:
            return refused(exc)
        except OSError:
            return JSONResponse({"detail": "the deployment could not be changed"}, status_code=500)
        return JSONResponse({"detail": "no such change"}, status_code=404)

    @application.post("/api/strategies/{strategy_id}/settle")
    def settle_difference(strategy_id: str, body: Settle) -> Any:
        strategy = find_strategy(root, strategy_id)
        if strategy is None:
            return no_strategy
        try:
            return settle(root, strategy, body.typed, body.units, datetime.now(UTC))
        except SettleRefused as exc:
            return refused(exc)
        except OSError:
            return JSONResponse({"detail": "the correction could not be recorded"}, status_code=500)

    @application.post("/api/stop-all")
    def change_stop_all(switch: Switch) -> Any:
        try:
            return set_stop_all(root, switch.stopped, datetime.now(UTC))
        except OSError:
            return JSONResponse({"detail": "the switch could not be recorded"}, status_code=500)

    @application.post("/api/tour/seen")
    def tour_seen() -> Any:
        try:
            return {"tour_seen": mark_tour_seen(root, datetime.now(UTC))}
        except OSError:
            return JSONResponse(
                {"detail": "the window's memory could not be written"}, status_code=500
            )

    @application.post("/api/jobs/{job_id}/stop")
    def stop(job_id: str) -> Any:
        try:
            return request_stop(root, job_id, datetime.now(UTC))
        except JobRefused as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)

    if window_files is not None and window_files.is_dir():
        application.mount("/", StaticFiles(directory=window_files, html=True), name="window")
    else:

        @application.get("/")
        def not_built() -> PlainTextResponse:
            return PlainTextResponse(
                "The StrategyLab window has not been built. "
                "Run `npm install` and `npm run build` in desktop/.",
                status_code=503,
            )

    return application

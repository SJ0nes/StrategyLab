"""Tests for the app's server: who it answers, and how it starts and stops.

Two kinds of test. The first builds the application and calls it in-process
through a test client, which opens no socket. The second starts the real
`strategylab serve` command as a child process and talks to it over the
loopback address - 127.0.0.1, this machine and nothing else - because whether
the server stops when its launcher goes away can only be seen from outside it.
Neither reaches the network.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from strategylab.api.app import MIN_TOKEN_CHARS, POLICY_VIOLATION, create_app
from strategylab.api.server import TOKEN_VARIABLE, _stop_when_closed, serve
from strategylab.protocol import ProtocolError, load_protocol

REPOSITORY = Path(__file__).resolve().parents[1]

#: A token of exactly the shortest accepted length.
TOKEN = "t" * MIN_TOKEN_CHARS
AUTH = {"Authorization": f"Bearer {TOKEN}"}

#: The host name a test client must present to be taken for this machine.
LOCAL = "http://127.0.0.1"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project root holding the committed protocol, strategy config and risk limits."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", tmp_path / "protocol.yaml")
    (tmp_path / "configs").mkdir()
    for name in ("ma_crossover.yaml", "risk_limits.yaml"):
        shutil.copyfile(REPOSITORY / "configs" / name, tmp_path / "configs" / name)
    return tmp_path


@pytest.fixture
def client(project: Path) -> TestClient:
    """A client for an application serving `project`, presenting itself as local."""
    return TestClient(create_app(project, TOKEN, {}), base_url=LOCAL)


# --------------------------------------------------------------------------
# The token.
# --------------------------------------------------------------------------


def test_a_request_with_the_token_is_answered(client: TestClient) -> None:
    assert client.get("/api/health", headers=AUTH).json() == {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": TOKEN},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": f"Bearer {TOKEN[:-1]}"},
        {"Authorization": "Bearer "},
        {"Authorization": f"Basic {TOKEN}"},
    ],
    ids=["none", "no-scheme", "longer", "shorter", "empty", "wrong-scheme"],
)
@pytest.mark.parametrize("route", ["/api/health", "/api/overview", "/api/no-such-route"])
def test_a_request_without_the_exact_token_is_refused_on_every_api_route(
    client: TestClient, headers: dict[str, str], route: str
) -> None:
    response = client.get(route, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "missing or wrong app token"}


@pytest.mark.parametrize("length", [0, 1, MIN_TOKEN_CHARS - 1])
def test_an_application_cannot_be_built_with_a_short_token(project: Path, length: int) -> None:
    with pytest.raises(ValueError, match="at least"):
        create_app(project, "t" * length, {})


# --------------------------------------------------------------------------
# The Host header.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8123", "localhost", "LOCALHOST:8123"])
def test_a_request_naming_this_machine_is_answered(project: Path, host: str) -> None:
    client = TestClient(create_app(project, TOKEN, {}), base_url=f"http://{host}")

    assert client.get("/api/health", headers=AUTH).status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:8123", "127.0.0.1.evil.example", "192.168.1.20:8123"]
)
@pytest.mark.parametrize("route", ["/api/health", "/"])
def test_a_request_naming_any_other_host_is_refused_even_with_the_token(
    project: Path, host: str, route: str
) -> None:
    # What a web page does when it re-points its own name at 127.0.0.1: the
    # request reaches this server carrying the page's name.
    client = TestClient(create_app(project, TOKEN, {}), base_url=f"http://{host}")

    assert client.get(route, headers=AUTH).status_code == 403


# --------------------------------------------------------------------------
# The Origin header.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8123", "http://localhost:8123"])
def test_a_request_from_this_machines_own_page_is_answered(client: TestClient, origin: str) -> None:
    assert client.get("/api/health", headers={**AUTH, "Origin": origin}).status_code == 200


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://127.0.0.1.evil.example", "null", "file://"]
)
def test_a_request_from_any_other_page_is_refused_even_with_the_token(
    client: TestClient, origin: str
) -> None:
    response = client.get("/api/health", headers={**AUTH, "Origin": origin})

    assert response.status_code == 403
    assert response.json() == {"detail": "this server answers only its own window"}


# --------------------------------------------------------------------------
# A WebSocket gets the same three checks.
# --------------------------------------------------------------------------


@pytest.fixture
def socket_client(project: Path) -> TestClient:
    """A client for an application that also has one WebSocket route under /api/."""
    application = create_app(project, TOKEN, {})

    @application.websocket("/api/echo")
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("connected")
        await websocket.close()

    return TestClient(application, base_url=LOCAL)


#: The test client's WebSocket handshake names a host of its own whatever the
#: client's base address is, so a socket that is to pass as local says so itself.
LOCAL_SOCKET = {**AUTH, "Host": "127.0.0.1"}


def test_a_socket_from_the_window_connects(socket_client: TestClient) -> None:
    with socket_client.websocket_connect("/api/echo", headers=LOCAL_SOCKET) as socket:
        assert socket.receive_text() == "connected"


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "127.0.0.1"},
        {**LOCAL_SOCKET, "Origin": "https://evil.example"},
        {**AUTH, "Host": "evil.example"},
    ],
    ids=["no-token", "another-pages-origin", "another-host"],
)
def test_a_socket_from_anywhere_else_is_closed_before_it_opens(
    socket_client: TestClient, headers: dict[str, str]
) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as closed,
        socket_client.websocket_connect("/api/echo", headers=headers),
    ):
        pass

    assert closed.value.code == POLICY_VIOLATION


# --------------------------------------------------------------------------
# What every answer carries.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "headers", "status"),
    [
        ("/api/health", AUTH, 200),
        ("/api/health", {}, 401),
        ("/api/health", {**AUTH, "Origin": "https://evil.example"}, 403),
        ("/", {}, 503),
    ],
    ids=["answered", "no-token", "refused-origin", "window-not-built"],
)
def test_every_answer_forbids_framing_and_sniffing_refusals_included(
    client: TestClient, route: str, headers: dict[str, str], status: int
) -> None:
    response = client.get(route, headers=headers)

    assert response.status_code == status
    policy = response.headers["content-security-policy"]
    for directive in ("default-src 'self'", "frame-ancestors 'none'", "base-uri 'none'"):
        assert directive in policy
    assert "form-action 'none'" in policy
    assert response.headers["x-content-type-options"] == "nosniff"


# --------------------------------------------------------------------------
# What it serves.
# --------------------------------------------------------------------------


def test_the_overview_route_returns_what_the_reader_returns(client: TestClient) -> None:
    overview = client.get("/api/overview", headers=AUTH).json()

    assert overview["protocol_version"] == 1
    assert [s["id"] for s in overview["strategies"]] == ["ma_crossover"]


def test_the_venue_key_does_not_come_back_through_the_route(project: Path) -> None:
    planted = "k3y-that-must-never-appear"
    client = TestClient(
        create_app(project, TOKEN, {"T212_API_KEY": planted, "T212_ENVIRONMENT": "demo"}),
        base_url=LOCAL,
    )

    response = client.get("/api/overview", headers=AUTH)

    assert response.json()["venue"] == {"environment": "demo", "key_present": True}
    assert planted not in response.text


def test_a_project_with_no_protocol_is_a_stated_error(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, TOKEN, {}), base_url=LOCAL)

    response = client.get("/api/overview", headers=AUTH)

    detail = response.json()["detail"]
    assert response.status_code == 500
    assert "protocol.yaml cannot be used" in detail
    # Read from the decoded value: in the raw JSON a Windows path has its
    # backslashes doubled, and a search for the plain path would always miss.
    assert str(tmp_path) not in detail
    assert tmp_path.as_posix() not in detail


def test_the_protocol_loader_does_put_the_path_in_its_message(tmp_path: Path) -> None:
    # The control for the test above: there is a path to remove.
    with pytest.raises(ProtocolError) as caught:
        load_protocol(tmp_path / "protocol.yaml")

    assert str(tmp_path) in str(caught.value) or tmp_path.as_posix() in str(caught.value)


@pytest.mark.parametrize("route", ["/docs", "/redoc", "/openapi.json"])
def test_the_api_does_not_describe_itself(client: TestClient, route: str) -> None:
    assert client.get(route).status_code == 404


def test_without_a_built_window_the_root_says_how_to_build_one(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 503
    assert "npm run build" in response.text


def test_a_built_window_is_served_without_the_token(project: Path) -> None:
    window = project / "window"
    window.mkdir()
    (window / "index.html").write_text("<p>the window</p>", encoding="utf-8")
    client = TestClient(create_app(project, TOKEN, {}, window), base_url=LOCAL)

    assert client.get("/").text == "<p>the window</p>"
    assert client.get("/api/health").status_code == 401, "the token still guards the API"


# --------------------------------------------------------------------------
# The lifeline fails towards stopping.
# --------------------------------------------------------------------------


class _Unreadable(io.StringIO):
    """A stream that cannot be read, as a closed or invalid handle cannot."""

    def read(self, size: int | None = -1) -> str:
        raise OSError("the handle is invalid")


@pytest.mark.parametrize("lifeline", [io.StringIO(""), io.StringIO("ignored input"), _Unreadable()])
def test_a_lifeline_that_ends_or_cannot_be_read_stops_the_server(lifeline: io.StringIO) -> None:
    server = uvicorn.Server(uvicorn.Config(lambda *_: None))
    assert server.should_exit is False

    _stop_when_closed(lifeline, server)

    assert server.should_exit is True


def test_a_server_with_nothing_to_watch_does_not_start(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("strategylab.api.server.sys.stdin", None)
    announced = io.StringIO()

    with pytest.raises(ValueError, match="nothing would ever stop this server"):
        serve(project, {TOKEN_VARIABLE: TOKEN}, announce=announced)

    assert announced.getvalue() == "", "no port was announced, so no socket was opened"


# --------------------------------------------------------------------------
# The command, as a real process.
# --------------------------------------------------------------------------


def _serve(project: Path, token: str | None) -> subprocess.Popen[str]:
    """Start `strategylab serve` in `project`, with its input and output piped."""
    environ = {k: v for k, v in os.environ.items() if k != TOKEN_VARIABLE}
    if token is not None:
        environ[TOKEN_VARIABLE] = token
    return subprocess.Popen(
        [sys.executable, "-m", "strategylab.cli", "serve"],
        cwd=project,
        env=environ,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture
def served(project: Path) -> Iterator[tuple[subprocess.Popen[str], int]]:
    """A running server and the port it announced. Killed afterwards if still alive."""
    process = _serve(project, TOKEN)
    assert process.stdout is not None
    try:
        port = json.loads(process.stdout.readline())["port"]
        yield process, port
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=30)


def _status(url: str, headers: dict[str, str]) -> int:
    """Fetch a loopback URL and return the HTTP status, error statuses included."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            return int(r.status)
    except urllib.error.HTTPError as error:
        return error.code


def test_the_command_announces_a_port_and_answers_on_it(
    served: tuple[subprocess.Popen[str], int],
) -> None:
    _, port = served

    assert _status(f"http://127.0.0.1:{port}/api/health", AUTH) == 200
    assert _status(f"http://127.0.0.1:{port}/api/health", {}) == 401


def test_the_server_stops_when_its_launcher_lets_go(
    served: tuple[subprocess.Popen[str], int],
) -> None:
    process, port = served
    assert _status(f"http://127.0.0.1:{port}/api/health", AUTH) == 200
    assert process.stdin is not None

    process.stdin.close()

    assert process.wait(timeout=30) == 0


def test_the_command_refuses_to_start_without_a_token(project: Path) -> None:
    process = _serve(project, None)

    out, err = process.communicate(timeout=60)

    assert process.returncode == 1
    assert out == "", "no port was announced"
    assert "refusing to serve" in " ".join(err.split())

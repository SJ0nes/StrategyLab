"""Tests for the command-line wiring: what each command reads, writes and refuses.

THE DECISIONS ARE TESTED ELSEWHERE. A search's arithmetic is in
`tests/test_search_loop.py`, a verdict's in `tests/test_walkforward.py`, the
worker's in `tests/test_worker.py`. What this file covers is the layer between
those functions and a person typing a command: which file a command opens, what
it leaves on disk, what it prints as the next step, and above all what it
REFUSES - because a refusal that lives in a library function and is never
reached from the command is not a refusal anyone gets.

EVERY TEST RUNS IN A THROWAWAY WORKSPACE. The commands resolve `protocol.yaml`,
`configs/`, `data/` and `state/` against the working directory, so each test
changes into a temporary directory holding copies. Nothing here reads or writes
this repository's own `protocol.yaml`, and in particular nothing can move its
`walk_forward_touches`. The price data is the recorded fixture, written into the
workspace's cache; no test makes a network call.

WHAT IS NOT COVERED, and it is most of two commands. `fetch` is tested only on
the path that finds a cache already there, because the other path downloads.
`run` and `diff` are tested only up to their refusals, because past those `run`
talks to the venue. And `validate` is tested only where it refuses: a run that
proceeds would need held-back bars the fixture does not have.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from typer.testing import CliRunner

from strategylab.api.jobs import STOPPED_EXIT_CODE
from strategylab.cli import app
from strategylab.core.config import config_content_hash
from strategylab.data.source import cache_write
from strategylab.search.loop import SEARCH_SCHEMA_VERSION
from strategylab.search.noise import default_workers

REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE_BARS = REPOSITORY / "tests" / "fixtures" / "SPY.parquet"

#: A grid of twenty-seven cells, small enough to search in a few seconds on the
#: fixture's 253 bars: three values of each parameter. Three, because a cell on
#: the edge of the grid is refused and an axis of one value is all edge.
#:
#: The longest lookback is 30 bars, on purpose. A noise ceiling resamples in
#: blocks as long as that and needs at least eight of them, and 253 bars holds
#: eight blocks of 30 and not of anything much longer.
SMALL_SEARCH = """search:
  fast:  {min: 5,  max: 15, step: 5}
  slow:  {min: 20, max: 30, step: 5}
  trend: {min: 20, max: 30, step: 5}
"""

#: The same grid with a 100-bar lookback: too long to resample 253 bars with.
LONG_SEARCH = SMALL_SEARCH.replace(
    "trend: {min: 20, max: 30, step: 5}", "trend: {min: 60, max: 100, step: 20}"
)

CONFIG = "configs/small.yaml"

runner = CliRunner()


def invoke(*args: str) -> tuple[int, str]:
    """Run one command and return its exit code and everything it printed.

    Output is collapsed to single spaces: the commands print through a console
    that wraps at the terminal's width, so a phrase can be split across lines
    anywhere, and a test that depended on where would be testing the terminal.
    """
    result = runner.invoke(app, list(args))
    return result.exit_code, " ".join(result.output.split())


def _lay_out(root: Path) -> None:
    """Put a copy of the protocol, a small strategy config and the risk limits under `root`."""
    shutil.copyfile(REPOSITORY / "protocol.yaml", root / "protocol.yaml")
    committed = (REPOSITORY / "configs" / "ma_crossover.yaml").read_text(encoding="utf-8")
    head, _, tail = committed.partition("search:")
    fixed = tail[tail.index("# Fixed") :]
    (root / "configs").mkdir()
    (root / CONFIG).write_text(head + SMALL_SEARCH + "\n" + fixed, encoding="utf-8")
    shutil.copyfile(
        REPOSITORY / "configs" / "risk_limits.yaml", root / "configs" / "risk_limits.yaml"
    )


def _write_cache() -> None:
    """Write the recorded fixture into the working directory's price cache."""
    cache_write(
        pd.read_parquet(FIXTURE_BARS),
        "SPY",
        Path("data"),
        source="fixture",
        fetched_at=datetime(2026, 8, 21, 13, 13, tzinfo=UTC),
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory holding a copy of the protocol and a small config, and nothing else."""
    _lay_out(tmp_path)
    monkeypatch.chdir(tmp_path)
    for name in ("T212_ENVIRONMENT", "T212_API_KEY", "T212_API_SECRET", "T212_TICKER"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def cached(workspace: Path) -> Path:
    """The workspace with the recorded fixture written into its price cache."""
    _write_cache()
    return workspace


@pytest.fixture(scope="module")
def searched_once(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One plain search, run once for the whole file. Returns the state directory it left.

    The search is the slow part of these tests and its result does not depend on
    which test asks, so it is run a single time and each test is handed a copy.
    """
    root = tmp_path_factory.mktemp("searched-once")
    patch = pytest.MonkeyPatch()
    try:
        _lay_out(root)
        patch.chdir(root)
        _write_cache()
        code, output = invoke("search", CONFIG)
        assert code == 0, output
    finally:
        patch.undo()
    return root / "state"


@pytest.fixture
def searched(cached: Path, searched_once: Path) -> Path:
    """The workspace after one plain search. Returns the artifact that search wrote."""
    shutil.copytree(searched_once, Path("state"))
    (artifact,) = sorted(Path("state").glob("search-*.json"))
    return artifact


def _touches() -> bytes:
    """The workspace protocol, byte for byte. A read of the held-back window rewrites it."""
    return Path("protocol.yaml").read_bytes()


def _with_gate(artifact: Path, status: str, beaten: int | None) -> None:
    """Rewrite a search artifact's noise gate, as a search with a hundred trials would."""
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["noise_gate"].update(
        status=status, scoreable_trials=100, beaten=beaten, required=95, summary="edited by a test"
    )
    artifact.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# status: what exists, and the one command to run next.
# --------------------------------------------------------------------------


def test_status_with_nothing_cached_points_at_fetch(workspace: Path) -> None:
    code, output = invoke("status")

    assert code == 0
    assert "not cached" in output
    assert "Next: uv run strategylab fetch" in output


def test_status_with_a_cache_and_no_search_points_at_search(cached: Path) -> None:
    code, output = invoke("status")

    assert code == 0
    assert "253 bars" in output
    assert "searches run 0" in output
    assert "Next: uv run strategylab search" in output


def test_status_after_a_plain_search_does_not_point_at_validate(searched: Path) -> None:
    """The trap this used to set: `validate` was the suggested next step for any search.

    That spends a read of the held-back window, permanently, on a candidate
    nothing has compared against noise.
    """
    code, output = invoke("status")

    assert code == 0
    assert "searches run 1" in output
    assert "--trials 100" in output
    assert "Next: uv run strategylab validate" not in output


def test_status_points_at_validate_only_once_the_gate_has_passed(searched: Path) -> None:
    _with_gate(searched, "passed", 97)

    code, output = invoke("status")

    assert code == 0
    assert f"Next: uv run strategylab validate {searched.as_posix()}" in output
    assert "spends a walk-forward read" in output


def test_status_after_a_failed_gate_says_there_is_nothing_to_validate(searched: Path) -> None:
    _with_gate(searched, "failed", 12)

    code, output = invoke("status")

    assert code == 0
    assert "did not clear the noise ceiling" in output
    assert "Next: uv run strategylab report" in output
    assert "Next: uv run strategylab validate" not in output


def test_status_changes_nothing(searched: Path) -> None:
    before = {path: path.read_bytes() for path in Path().rglob("*") if path.is_file()}

    invoke("status")

    after = {path: path.read_bytes() for path in Path().rglob("*") if path.is_file()}
    assert after == before


def test_a_command_run_outside_a_workspace_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    code, output = invoke("status")

    assert code == 1
    assert "repository root" in output


# --------------------------------------------------------------------------
# search: what it leaves behind.
# --------------------------------------------------------------------------


def test_search_writes_one_artifact_that_names_what_it_ran_on(searched: Path) -> None:
    payload = json.loads(searched.read_text(encoding="utf-8"))

    assert payload["schema_version"] == SEARCH_SCHEMA_VERSION
    assert payload["config"] == CONFIG
    # The hash the command recorded is the hash of the file it was pointed at.
    assert payload["config_content_hash"] == config_content_hash(Path(CONFIG))
    assert payload["grid_size"] == 27
    assert payload["winner"] is not None
    assert payload["window"]["bars"] == 253
    assert payload["noise_gate"]["status"] == "not_computed"
    assert payload["noise_ceiling"] is None
    assert len(payload["equity_curves"]["cells"]) == 27


def test_search_says_its_result_is_not_evidence_and_cannot_be_validated(cached: Path) -> None:
    code, output = invoke("search", CONFIG)

    assert code == 0
    assert "No noise ceiling was computed" in output
    assert "validate will refuse this candidate" in output
    assert "buy and hold over" in output


def test_search_with_trials_reports_the_gate_and_records_every_trial(cached: Path) -> None:
    code, output = invoke("search", CONFIG, "--trials", "2")

    assert code == 0
    assert "No verdict from the noise gate" in output
    (artifact,) = sorted(Path("state").glob("search-*.json"))
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["noise_gate"]["status"] == "undetermined"
    assert len(payload["noise_ceiling"]["trial_best_sharpe"]) == 2


@pytest.mark.parametrize(("asked", "used"), [("1", 1), ("2", 2), ("9", 2)])
def test_search_says_how_many_processes_it_used_and_reports_each_trial(
    cached: Path, asked: str, used: int
) -> None:
    code, output = invoke("search", CONFIG, "--trials", "2", "--workers", asked)

    assert code == 0
    # Never more processes than trials: nine were asked for and two have work.
    assert f"spread over {used} process(es)" in output
    assert "surrogate 1 of 2 done" in output
    assert "surrogate 2 of 2 done" in output


@pytest.mark.parametrize(("flags", "expected"), [(["--workers", "3"], 3), ([], None)])
def test_search_hands_the_process_count_to_the_search(
    cached: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str], expected: int | None
) -> None:
    handed: dict[str, Any] = {}

    def stop_after_recording(*args: Any, **kwargs: Any) -> None:
        handed.update(kwargs)
        raise ValueError("stopped by the test once the arguments were seen")

    monkeypatch.setattr("strategylab.cli.run_search", stop_after_recording)

    runner.invoke(app, ["search", CONFIG, "--trials", "2", *flags])

    # With no flag the command asks the machine, and leaves two cores alone.
    assert handed["noise_workers"] == (default_workers() if expected is None else expected)
    assert handed["noise_trials"] == 2


def test_search_without_a_ceiling_says_nothing_about_processes(cached: Path) -> None:
    code, output = invoke("search", CONFIG, "--workers", "2")

    assert code == 0
    assert "process(es)" not in output
    assert "surrogate" not in output


def test_search_refuses_to_run_on_no_processes(cached: Path) -> None:
    result = runner.invoke(app, ["search", CONFIG, "--trials", "2", "--workers", "0"])

    assert result.exit_code == 2
    assert not list(Path("state").glob("search-*.json")) if Path("state").exists() else True


def test_a_ceiling_the_window_cannot_support_is_a_message_not_a_traceback(cached: Path) -> None:
    # Found by this file: the command caught only its own error type, so the
    # first --trials run on a short history ended in a stack trace.
    text = Path(CONFIG).read_text(encoding="utf-8")
    Path(CONFIG).write_text(text.replace(SMALL_SEARCH, LONG_SEARCH), encoding="utf-8")
    assert LONG_SEARCH in Path(CONFIG).read_text(encoding="utf-8")

    result = runner.invoke(app, ["search", CONFIG, "--trials", "2"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), "the command raised instead of exiting"
    assert "search could not run" in " ".join(result.output.split())
    assert not list(Path("state").glob("search-*.json")) if Path("state").exists() else True


def test_search_without_a_cache_is_refused_and_writes_nothing(workspace: Path) -> None:
    code, output = invoke("search", CONFIG)

    assert code == 1
    assert "is not cached" in output
    assert not Path("state").exists() or not list(Path("state").glob("search-*.json"))


def test_search_of_a_config_that_is_not_there_is_refused(cached: Path) -> None:
    code, output = invoke("search", "configs/absent.yaml")

    assert code == 1
    assert "no such config" in output


def test_search_of_a_malformed_config_is_refused(cached: Path) -> None:
    Path(CONFIG).write_text("name: broken\nentry: [", encoding="utf-8")

    code, output = invoke("search", CONFIG)

    assert code == 1
    assert "config error" in output


# --------------------------------------------------------------------------
# validate: every refusal happens before the held-back window is read.
# --------------------------------------------------------------------------


def test_validate_refuses_a_search_with_no_noise_gate_verdict(searched: Path) -> None:
    before = _touches()

    code, output = invoke("validate", searched.as_posix())

    assert code == 1
    assert "nothing to validate" in output
    assert "cannot be validated" in output
    assert _touches() == before, "a refusal must not move the access counter"
    assert not list(Path("state").glob("validation-*.json"))


def test_validate_refuses_a_candidate_inside_the_noise(searched: Path) -> None:
    _with_gate(searched, "failed", 12)
    before = _touches()

    code, output = invoke("validate", searched.as_posix())

    assert code == 1
    assert "cannot be validated" in output
    assert _touches() == before


def test_validate_refuses_a_config_edited_since_the_search(searched: Path) -> None:
    _with_gate(searched, "passed", 97)
    text = Path(CONFIG).read_text(encoding="utf-8")
    Path(CONFIG).write_text(text.replace("max: 15", "max: 20"), encoding="utf-8")
    before = _touches()

    code, output = invoke("validate", searched.as_posix())

    assert code == 1
    assert "edited after the search" in output
    assert _touches() == before


def test_validate_refuses_an_artifact_that_is_not_there(cached: Path) -> None:
    before = _touches()

    code, output = invoke("validate", "state/search-absent.json")

    assert code == 1
    assert _touches() == before


def test_validate_takes_no_option_that_could_skip_the_counter() -> None:
    """There is deliberately no dry run. A flag that looked without charging would be one."""
    code, output = invoke("validate", "--help")

    assert code == 0
    for flag in ("--dry-run", "--no-touch", "--force", "--protocol"):
        assert flag not in output


# --------------------------------------------------------------------------
# report: redraws what is on disk and reads no window.
# --------------------------------------------------------------------------


def test_report_draws_each_search_and_recomputes_nothing(searched: Path) -> None:
    before = searched.read_bytes()
    protocol_before = _touches()

    code, output = invoke("report")

    assert code == 0
    page = Path("reports") / f"{searched.stem}.html"
    assert page.exists()
    assert "search surface" in page.read_text(encoding="utf-8")
    assert searched.read_bytes() == before
    assert _touches() == protocol_before


def test_report_with_nothing_to_draw_is_refused(cached: Path) -> None:
    code, output = invoke("report")

    assert code == 1
    assert "no search results" in output


def test_report_skips_an_artifact_it_cannot_draw_and_draws_the_rest(searched: Path) -> None:
    Path("state/search-00000000T000000Z.json").write_text("{not json", encoding="utf-8")

    code, output = invoke("report")

    assert code == 0
    assert "skipped search-00000000T000000Z.json" in output
    assert (Path("reports") / f"{searched.stem}.html").exists()


# --------------------------------------------------------------------------
# fetch, run and diff: only the paths that never reach the network.
# --------------------------------------------------------------------------


def test_fetch_leaves_an_existing_cache_alone_and_downloads_nothing(cached: Path) -> None:
    before = {path: path.read_bytes() for path in Path("data").iterdir()}
    sys.modules.pop("yfinance", None)

    code, output = invoke("fetch")

    assert code == 0
    assert "already cached" in output
    assert {path: path.read_bytes() for path in Path("data").iterdir()} == before
    assert "yfinance" not in sys.modules, "the vendor library was imported, so a download began"


@pytest.mark.parametrize("environment", ["", "live", "DEMO", "demo "])
def test_run_refuses_any_environment_but_demo(
    searched: Path, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("T212_ENVIRONMENT", environment)
    monkeypatch.setenv("T212_API_KEY", "a-key-that-must-never-be-sent")

    code, output = invoke("run", searched.as_posix())

    assert code == 1
    assert "refusing to run" in output
    assert "a-key-that-must-never-be-sent" not in output
    assert not Path("state/decisions-live.jsonl").exists()


def test_run_refuses_to_start_without_a_key(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T212_ENVIRONMENT", "demo")

    code, output = invoke("run", searched.as_posix())

    assert code == 1
    assert "T212_API_KEY is not set" in output
    assert not Path("state/decisions-live.jsonl").exists()


class _CachedSource:
    """A price source that answers with the recorded fixture's bars, and no network."""

    name = "fixture"

    def fetch(self, ticker: str, start: object, end: object) -> pd.DataFrame:
        """Return the recorded bars, whatever instrument is asked for."""
        return pd.read_parquet(FIXTURE_BARS)


@pytest.mark.parametrize(
    ("secret", "sent"),
    [("a-secret-that-must-never-be-sent", "a-secret-that-must-never-be-sent"), ("", None)],
    ids=["set", "blank"],
)
def test_run_hands_the_venue_the_secret_when_one_is_set(
    searched: Path, monkeypatch: pytest.MonkeyPatch, secret: str, sent: str | None
) -> None:
    from strategylab.execution.t212 import T212Error

    seen: dict[str, Any] = {}

    def venue(key: str, environment: str, **kwargs: Any) -> Any:
        seen.update(kwargs, key=key, environment=environment)
        raise T212Error("stopped by the test before any request")

    monkeypatch.setenv("T212_ENVIRONMENT", "demo")
    monkeypatch.setenv("T212_API_KEY", "a-key-that-must-never-be-sent")
    monkeypatch.setenv("T212_API_SECRET", secret)
    monkeypatch.setattr("strategylab.cli.YFinanceSource", _CachedSource)
    monkeypatch.setattr("strategylab.cli.T212Venue", venue)

    code, output = invoke("run", searched.as_posix())

    assert code == 1, output
    assert "stopped by the test" in output
    assert seen["key"] == "a-key-that-must-never-be-sent"
    assert seen["environment"] == "demo"
    assert seen["api_secret"] == sent
    assert "must-never-be-sent" not in output


class _FlatVenue:
    """A venue holding nothing, that accepts every order, and whose fills are scripted."""

    def __init__(self, fills: list[Any] | Exception) -> None:
        """Answer ``get_fills`` with ``fills``, or raise it."""
        self.fills = fills
        self.orders: list[Any] = []

    def reconcile(self) -> Any:
        """Report the venue flat and nothing repaired."""
        from strategylab.execution.venue import ReconcileReport

        return ReconcileReport(datetime.now(UTC), (), (), (), False)

    def get_balance(self) -> Any:
        """Report a small all-cash account."""
        from strategylab.execution.venue import Balance

        return Balance(currency="GBP", cash=1000.0, equity=1000.0)

    def get_fills(self, ticker: str) -> list[Any]:
        """Return or raise the scripted fills."""
        if isinstance(self.fills, Exception):
            raise self.fills
        return self.fills

    def place_order(self, order: Any) -> str:
        """Accept the order."""
        self.orders.append(order)
        return "900"


def _run_against(
    venue: _FlatVenue, searched: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, str]:
    monkeypatch.setenv("T212_ENVIRONMENT", "demo")
    monkeypatch.setenv("T212_API_KEY", "a-key-that-must-never-be-sent")
    monkeypatch.setattr("strategylab.cli.YFinanceSource", _CachedSource)
    monkeypatch.setattr("strategylab.cli.T212Venue", lambda *args, **kwargs: venue)
    return invoke("run", searched.as_posix())


def test_run_records_an_earlier_orders_fill_before_deciding(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from strategylab.core.types import Side
    from strategylab.execution.venue import Fill, OrderId

    Path("state/account-live.jsonl").write_text(
        json.dumps({"order_outcome": "sent", "venue_order_id": "41"}) + "\n", encoding="utf-8"
    )
    fill = Fill(
        order_id=OrderId("41"),
        ticker="VUAG.L",
        side=Side.BUY,
        quantity=2.0,
        price=99.5,
        currency="GBP",
        filled_at=datetime(2026, 11, 17, 8, 0, tzinfo=UTC),
        charges=(),
    )

    code, output = _run_against(_FlatVenue([fill]), searched, monkeypatch)

    assert code == 0, output
    assert "filled order 41" in output
    (line,) = Path("state/fills-live.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["price"] == 99.5
    assert Path("state/decisions-live.jsonl").exists()


def test_run_goes_on_when_the_fills_cannot_be_read(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from strategylab.execution.t212 import T212Error

    Path("state/account-live.jsonl").write_text(
        json.dumps({"order_outcome": "sent", "venue_order_id": "41"}) + "\n", encoding="utf-8"
    )

    code, output = _run_against(_FlatVenue(T212Error("history unavailable")), searched, monkeypatch)

    assert code == 0, output
    assert "history unavailable" in output
    assert "the check goes on without them" in output
    assert not Path("state/fills-live.jsonl").exists()
    assert Path("state/decisions-live.jsonl").exists()


def test_a_second_check_on_the_same_bar_decides_nothing_and_still_reads_fills(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As on a bank holiday: the newest bar is the one the last check decided.
    from strategylab.core.types import Side
    from strategylab.execution.venue import Fill, OrderId

    first, output = _run_against(_FlatVenue([]), searched, monkeypatch)
    assert first == 0, output
    decided = Path("state/decisions-live.jsonl").read_text(encoding="utf-8")
    # An order an earlier check sent, whose fill the venue now reports.
    with Path("state/account-live.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"order_outcome": "sent", "venue_order_id": "41"}) + "\n")
    accounts = Path("state/account-live.jsonl").read_text(encoding="utf-8")
    fill = Fill(
        order_id=OrderId("41"),
        ticker="VUAG.L",
        side=Side.BUY,
        quantity=1.0,
        price=99.5,
        currency="GBP",
        filled_at=datetime(2026, 11, 17, 8, 0, tzinfo=UTC),
        charges=(),
    )

    again = _FlatVenue([fill])
    second, output = _run_against(again, searched, monkeypatch)

    assert second == 0, output
    assert "No new bar" in output
    assert "Nothing decided, nothing sent." in output
    assert Path("state/decisions-live.jsonl").read_text(encoding="utf-8") == decided
    assert Path("state/account-live.jsonl").read_text(encoding="utf-8") == accounts
    assert again.orders == []
    (line,) = Path("state/fills-live.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["venue_order_id"] == "41"


def test_a_damaged_live_log_stops_the_check_before_it_decides(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Path("state/decisions-live.jsonl").write_text("not json\n", encoding="utf-8")
    venue = _FlatVenue([])

    code, output = _run_against(venue, searched, monkeypatch)

    assert code == 1
    assert "decision log" in output
    assert venue.orders == []
    assert Path("state/decisions-live.jsonl").read_text(encoding="utf-8") == "not json\n"


def test_diff_with_no_live_log_is_refused(searched: Path) -> None:
    code, _ = invoke("diff", searched.as_posix())

    assert code == 1
    assert not Path("state/decisions-replay.jsonl").exists()


# ---------------------------------------------------------------------------
# several strategies (docs/decisions/0022)
# ---------------------------------------------------------------------------


class _HoldingVenue(_FlatVenue):
    """A venue that reports a holding of the execution instrument."""

    def __init__(self, units: float) -> None:
        """Report ``units`` of VUAG.L held, and no fills."""
        super().__init__([])
        self.units = units

    def reconcile(self) -> Any:
        """Report the holding."""
        from strategylab.execution.venue import Position, ReconcileReport

        held = Position(
            ticker="VUAG.L", quantity=self.units, average_price=1.0, market_value=self.units
        )
        return ReconcileReport(datetime.now(UTC), (held,), (), (), False)


def _forced(monkeypatch: pytest.MonkeyPatch, action: str) -> list[int]:
    """Make every decision ``action``, allowed at whatever share was asked; note the count."""
    from strategylab.core.types import Action, Intent
    from strategylab.risk.limits import RiskVerdict, Verdict

    seen: list[int] = []

    def decide(
        definition: Any, params: Any, bars: Any, state: Any, limits: Any, strategies: int = 1
    ) -> Any:
        seen.append(strategies)
        intent = Intent(action=Action(action), size_pct=95.0)
        return intent, RiskVerdict(Verdict.ALLOW, "test", 95.0 / strategies)

    monkeypatch.setattr("strategylab.cli.decide", decide)
    return seen


def _run_as(
    venue: _FlatVenue,
    searched: Path,
    monkeypatch: pytest.MonkeyPatch,
    *options: str,
) -> tuple[int, str]:
    monkeypatch.setenv("T212_ENVIRONMENT", "demo")
    monkeypatch.setenv("T212_API_KEY", "a-key-that-must-never-be-sent")
    monkeypatch.setattr("strategylab.cli.YFinanceSource", _CachedSource)
    monkeypatch.setattr("strategylab.cli.T212Venue", lambda *args, **kwargs: venue)
    return invoke("run", searched.as_posix(), *options)


def _accounts() -> list[dict[str, Any]]:
    path = Path("state/account-live.jsonl")
    return [json.loads(t) for t in path.read_text(encoding="utf-8").splitlines()]


def _sent_and_filled(strategy_id: str, order_id: str, side: str, units: float) -> None:
    Path("state").mkdir(exist_ok=True)
    with Path("state/account-live.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"order_outcome": "sent", "venue_order_id": order_id, "strategy_id": strategy_id}
            )
            + "\n"
        )
    with Path("state/fills-live.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"venue_order_id": order_id, "side": side, "quantity": units}) + "\n"
        )


def test_a_strategys_check_decides_in_its_own_log_within_its_share(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shares = _forced(monkeypatch, "enter_long")
    venue = _FlatVenue([])

    code, output = _run_as(
        venue, searched, monkeypatch, "--strategy-id", "alpha", "--strategies", "2"
    )

    assert code == 0, output
    assert shares == [2]
    assert Path("state/live/alpha/decisions-live.jsonl").exists()
    assert not Path("state/decisions-live.jsonl").exists()
    (order,) = venue.orders
    close = float(pd.read_parquet(FIXTURE_BARS)["close"].iloc[-1])
    # 47.5% of 1,000 equity at the last close.
    assert order.quantity == pytest.approx(1000.0 * 0.475 / close, rel=1e-12)
    (line,) = _accounts()
    assert (line["strategy_id"], line["strategies"], line["order_outcome"]) == ("alpha", 2, "sent")


def test_one_strategys_decision_on_a_bar_does_not_stop_anothers(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forced(monkeypatch, "hold")

    first, output = _run_as(_FlatVenue([]), searched, monkeypatch, "--strategy-id", "alpha")
    assert first == 0, output
    second, output = _run_as(_FlatVenue([]), searched, monkeypatch, "--strategy-id", "beta")

    assert second == 0, output
    assert "No new bar" not in output
    assert Path("state/live/beta/decisions-live.jsonl").exists()
    again, output = _run_as(_FlatVenue([]), searched, monkeypatch, "--strategy-id", "beta")
    assert "No new bar" in output


def test_an_exit_sells_only_the_strategys_own_units(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forced(monkeypatch, "exit_long")
    _sent_and_filled("alpha", "1", "buy", 2.5)
    _sent_and_filled("beta", "2", "buy", 4.0)
    venue = _HoldingVenue(6.5)

    code, output = _run_as(
        venue, searched, monkeypatch, "--strategy-id", "beta", "--strategies", "2"
    )

    assert code == 0, output
    (order,) = venue.orders
    assert (order.side.value, order.quantity) == ("sell", 4.0)


def test_an_entry_goes_ahead_while_another_strategy_holds_the_instrument(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forced(monkeypatch, "enter_long")
    _sent_and_filled("alpha", "1", "buy", 2.5)
    venue = _HoldingVenue(2.5)

    code, output = _run_as(
        venue, searched, monkeypatch, "--strategy-id", "beta", "--strategies", "2"
    )

    assert code == 0, output
    assert [o.side.value for o in venue.orders] == ["buy"]


def test_a_strategy_waiting_for_a_fill_decides_and_sends_nothing(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forced(monkeypatch, "enter_long")
    Path("state").mkdir(exist_ok=True)
    Path("state/account-live.jsonl").write_text(
        json.dumps({"order_outcome": "sent", "venue_order_id": "7", "strategy_id": "alpha"}) + "\n",
        encoding="utf-8",
    )
    venue = _FlatVenue([])

    code, output = _run_as(venue, searched, monkeypatch, "--strategy-id", "alpha")

    assert code == 0, output
    assert venue.orders == []
    assert "no recorded fill" in output
    assert Path("state/live/alpha/decisions-live.jsonl").exists()
    assert _accounts()[-1]["order_outcome"] == "none"


def test_units_the_ledgers_cannot_account_for_stop_every_order(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bought by hand in the venue's own app: the venue holds 3, the ledgers nothing.
    _forced(monkeypatch, "enter_long")
    venue = _HoldingVenue(3.0)

    code, output = _run_as(venue, searched, monkeypatch, "--strategy-id", "alpha")

    assert code == 0, output
    assert venue.orders == []
    assert "No order sent" in output
    assert "holds 3 units" in output


def test_a_damaged_ledger_stops_the_check(searched: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _forced(monkeypatch, "enter_long")
    _sent_and_filled("alpha", "1", "short", 2.0)
    venue = _FlatVenue([])

    code, output = _run_as(venue, searched, monkeypatch, "--strategy-id", "alpha")

    assert code == 1
    assert "ledger" in output
    assert venue.orders == []
    assert not Path("state/live/alpha/decisions-live.jsonl").exists()


def test_a_check_run_by_hand_still_uses_the_venues_position(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shares = _forced(monkeypatch, "exit_long")
    venue = _HoldingVenue(3.0)

    code, output = _run_as(venue, searched, monkeypatch)

    assert code == 0, output
    assert shares == [1]
    (order,) = venue.orders
    assert order.quantity == 3.0
    assert Path("state/decisions-live.jsonl").exists()
    (line,) = _accounts()
    assert (line["strategy_id"], line["strategies"]) == (None, 1)


def test_a_check_run_by_hand_sends_nothing_while_deployed_strategies_hold_units(
    searched: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forced(monkeypatch, "exit_long")
    _sent_and_filled("alpha", "1", "buy", 2.5)
    _sent_and_filled("beta", "2", "buy", 4.0)
    venue = _HoldingVenue(6.5)

    code, output = _run_as(venue, searched, monkeypatch)

    assert code == 0, output
    assert venue.orders == []
    assert "alpha, beta hold or await units" in output
    assert Path("state/decisions-live.jsonl").exists()


@pytest.mark.parametrize(
    ("options", "said"),
    [
        (("--strategies", "2"), "needs --strategy-id"),
        (("--strategy-id", "../escape"), "not a config file's name"),
        (("--strategy-id", "alpha", "--strategies", "0"), "0"),
    ],
    ids=["count-without-id", "id-with-a-path", "no-strategies"],
)
def test_run_refuses_a_strategy_it_cannot_name_safely(
    searched: Path, monkeypatch: pytest.MonkeyPatch, options: tuple[str, ...], said: str
) -> None:
    venue = _FlatVenue([])

    code, output = _run_as(venue, searched, monkeypatch, *options)

    assert code != 0
    assert said in output
    assert venue.orders == []
    assert not Path("state/live").exists()


def test_diff_compares_a_strategys_own_log(searched: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, output = _run_as(_FlatVenue([]), searched, monkeypatch, "--strategy-id", "alpha")
    assert code == 0, output

    plain, _ = invoke("diff", searched.as_posix())
    own, output = invoke("diff", searched.as_posix(), "--strategy-id", "alpha")

    assert plain == 1
    assert own == 0, output
    assert "Compared 1 bar(s): 0 diverge" in output


def test_diff_refuses_an_id_that_names_a_path(searched: Path) -> None:
    code, output = invoke("diff", searched.as_posix(), "--strategy-id", "../x")

    assert code == 1
    assert "not a config file's name" in output


def test_every_command_the_readme_names_exists() -> None:
    code, output = invoke("--help")

    assert code == 0
    for command in ("status", "fetch", "search", "report", "validate", "run", "diff"):
        assert command in output


def test_the_workspace_never_touches_this_repository(workspace: Path) -> None:
    """The guard on the guard: these tests must not be able to spend a real read."""
    assert Path.cwd().resolve() == workspace.resolve()
    assert REPOSITORY.resolve() not in (Path.cwd().resolve(), *Path.cwd().resolve().parents)


def test_a_search_asked_to_stop_ends_at_the_next_trial_and_writes_nothing(cached: Path) -> None:
    Path("stop-here").write_text("stop\n", encoding="utf-8")

    code, output = invoke(
        "search", CONFIG, "--trials", "3", "--workers", "1", "--stop-file", "stop-here"
    )

    assert code == STOPPED_EXIT_CODE == 3
    assert "Stopped as asked" in output
    assert "surrogate 1 of 3 done" in output
    assert "surrogate 2 of 3 done" not in output
    assert sorted(Path("state").glob("search-*.json")) == []
    assert not Path("state/graveyard.jsonl").exists()


def test_a_stop_file_that_never_appears_changes_nothing(cached: Path) -> None:
    code, output = invoke(
        "search", CONFIG, "--trials", "2", "--workers", "1", "--stop-file", "never-written"
    )

    assert code == 0
    assert "Stopped as asked" not in output
    assert len(sorted(Path("state").glob("search-*.json"))) == 1


def test_a_search_without_trials_never_looks_for_its_stop_file(cached: Path) -> None:
    Path("stop-here").write_text("stop\n", encoding="utf-8")

    code, output = invoke("search", CONFIG, "--stop-file", "stop-here")

    assert code == 0
    assert "Stopped as asked" not in output
    assert len(sorted(Path("state").glob("search-*.json"))) == 1

"""The command-line interface.

Every command is registered here with help text a stranger could follow. The
bodies are built one per session, in the order the roadmap gives, so that each
arrives with its own tests rather than as part of a large unreviewable change.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from strategylab.core.config import StrategyConfigError, load_strategy
from strategylab.core.strategy import required_history
from strategylab.data.source import (
    DataQualityError,
    YFinanceSource,
    cache_read,
    cache_write,
    is_cached,
    validate_bars,
)
from strategylab.execution.t212 import T212Error, T212Venue
from strategylab.live.worker import (
    DEFAULT_LIVE_LOG_PATH,
    DEFAULT_REPLAY_LOG_PATH,
    DEFAULT_SESSION_PATH,
    compare,
    decide,
    load_session,
    order_for,
    portfolio_state,
    record,
    replay,
    save_session,
)
from strategylab.logging.decisions import read_decisions, write_decision, write_decisions
from strategylab.protocol import Protocol, ProtocolError, load_protocol
from strategylab.report.surface import ReportError, render_surface
from strategylab.risk.limits import load_limits
from strategylab.search.graveyard import DEFAULT_GRAVEYARD_PATH
from strategylab.search.grid import expand_grid
from strategylab.search.loop import (
    DEFAULT_RESULTS_DIR,
    SearchError,
    SearchResult,
    run_search,
    write_search_result,
)
from strategylab.search.noise import trials_note
from strategylab.validate.candidate import CandidateError, read_candidate
from strategylab.validate.walkforward import (
    Outcome,
    walk_forward,
    write_validation_result,
)
from strategylab.windows import WindowError

DATA_DIR = Path("data")
#: Imported rather than declared as a second `Path("state")`. `status` counts
#: the artifacts in here and both writers put them here, so a divergence between
#: the two literals would have `status` reporting "validations run 0" forever
#: while files were written elsewhere.
STATE_DIR = DEFAULT_RESULTS_DIR

#: How many cells `search` prints. The whole surface goes to the result file;
#: a ninety-row table in a terminal is unreadable and would bury the caveat
#: underneath it, which is the one line of that output that must be read.
TOP_CELLS_SHOWN = 10

#: Printed in place of a metric that could not be computed. Never a zero: a
#: Sharpe of 0.000 in a table reads as "measured, and no edge", which is a claim
#: nobody made about a fold holding too few returns to have a deviation.
METRIC_ABSENT = "n/a"

#: How each verdict is coloured. A validation is read at a glance and the three
#: outcomes mean very different things, so they must not look alike.
VERDICT_STYLE = {
    Outcome.PASS: "green",
    Outcome.FAIL: "red",
    Outcome.INSUFFICIENT_DATA: "yellow",
}

console = Console()
err_console = Console(stderr=True)


def _load_or_exit() -> Protocol:
    """Load the protocol, or report the failure and exit non-zero.

    Every command needs the protocol, and a malformed one is an operator error
    rather than a crash worth a traceback.

    Returns:
        The loaded protocol.

    Raises:
        typer.Exit: With code 1 if the protocol cannot be loaded.
    """
    try:
        return load_protocol()
    except ProtocolError as exc:
        err_console.print(f"[bold red]protocol error[/bold red] {exc}")
        err_console.print("Run this command from the repository root.")
        raise typer.Exit(code=1) from exc


app = typer.Typer(
    name="strategylab",
    help=(
        "Backtest, validate, and paper-trade systematic strategies. "
        "Run 'strategylab status' first: it reports where the project is and "
        "names the next command to run."
    ),
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def status() -> None:
    """Report where the project is and which command comes next.

    Reads the protocol, checks whether price data is cached, counts the searches
    AND the validations run so far, and reports the pass criteria and how many
    times the held-back window has been read. Names the next command but NEVER
    runs it - a status command that changes state cannot be run freely, and one
    that cannot be run freely does not get run.

    The validation count is what selects the suggested next command: with a
    search on disk and no validation beside it, this points at `validate` and
    says what that costs. It counts artifacts in state/, not touches - the two
    can differ, because a validation that fails after the window is read spends
    a touch and writes nothing.
    """
    protocol = _load_or_exit()

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", no_wrap=True)
    table.add_column()

    table.add_row("protocol", f"v{protocol.protocol_version}, registered {protocol.created}")
    table.add_row(
        "research",
        f"{protocol.instruments.research}  "
        f"{protocol.windows.in_sample.start} to {protocol.windows.in_sample.end}",
    )
    table.add_row(
        "execution",
        f"{protocol.instruments.execution}  "
        f"held back {protocol.windows.walk_forward.start} to "
        f"{protocol.windows.walk_forward.end}",
    )

    cached = is_cached(protocol.instruments.research, DATA_DIR)
    unusable = False
    if cached:
        try:
            frame, entry = cache_read(protocol.instruments.research, DATA_DIR)
            table.add_row(
                "data",
                f"[green]cached[/green]  {entry.rows} bars, {entry.start} to {entry.end}, "
                f"from {entry.source}, hash {entry.content_hash[:12]}",
            )
        except DataQualityError as exc:
            cached = False
            unusable = True
            table.add_row("data", f"[red]cache is unusable[/red] {exc}")
    else:
        table.add_row("data", "[yellow]not cached[/yellow]")

    searches = len(list(STATE_DIR.glob("search-*.json"))) if STATE_DIR.exists() else 0
    validations = len(list(STATE_DIR.glob("validation-*.json"))) if STATE_DIR.exists() else 0
    table.add_row("searches run", str(searches))
    table.add_row("validations run", str(validations))
    table.add_row(
        "walk-forward reads",
        f"{protocol.walk_forward_touches}"
        + ("  (held-back window still untouched)" if protocol.walk_forward_touches == 0 else ""),
    )
    table.add_row(
        "pass bar",
        f"Sharpe {protocol.validation.effective_sharpe_bar:.2f} "
        f"({protocol.validation.min_sharpe:.2f} + {protocol.validation.pass_margin:.2f} margin), "
        f"drawdown ceiling {protocol.validation.max_drawdown_ceiling_pct:.0f}%",
    )

    console.print(table)
    console.print()

    if not cached:
        # A snapshot that exists but will not load needs --force; the bare
        # command would find the files present and decline to replace them.
        flag = " --force" if unusable else ""
        console.print(f"Next:  [bold]uv run strategylab fetch{flag}[/bold]")
        console.print("       Downloads and caches price data. Uses the network, as `run` does.")
        return

    if searches == 0:
        console.print("Next:  [bold]uv run strategylab search configs/ma_crossover.yaml[/bold]")
        console.print("       Walks the grid over the in-sample window, writes every cell to")
        console.print("       state/. Takes a few minutes.")
        return

    if validations == 0:
        latest = sorted(STATE_DIR.glob("search-*.json"))[-1]
        console.print(f"Next:  [bold]uv run strategylab validate {latest.as_posix()}[/bold]")
        console.print("       Walks that search's candidate through the held-back window.")
        console.print(
            "       [yellow]This spends a walk-forward read and records it permanently.[/yellow]"
        )
        console.print("       [bold]uv run strategylab report[/bold] draws the searches first,")
        console.print("       recomputing nothing and reading no window.")
    else:
        console.print("Next:  [bold]uv run strategylab report[/bold]")
        console.print("       Draws each stored search as a self-contained page in reports/.")
        console.print("       Recomputes nothing and reads no window.")
    console.print()
    console.print(
        "[yellow]A search reports a noise ceiling only when given --trials[/yellow]; "
        "without one, nothing it printed is evidence of an edge - and the validator "
        "does not gate on the ceiling either way."
    )


@app.command()
def fetch(
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-download even if a cached snapshot already exists."),
    ] = False,
) -> None:
    """Download and cache price data for the protocol's research instrument.

    One of two commands that touch the network - `run` is the other. Writes a parquet
    snapshot alongside its provenance - fetch timestamp, source, row count, and
    a content hash - because vendors revise history, and results are
    reproducible against the snapshot rather than against the vendor.
    """
    protocol = _load_or_exit()
    ticker = protocol.instruments.research

    if is_cached(ticker, DATA_DIR) and not force:
        try:
            _, entry = cache_read(ticker, DATA_DIR)
        except DataQualityError as exc:
            # An unreadable snapshot is the one case where re-downloading is
            # obviously right, so say so rather than surfacing a traceback.
            err_console.print(f"[bold red]the cached snapshot is unusable[/bold red] {exc}")
            err_console.print("Re-download with [bold]uv run strategylab fetch --force[/bold].")
            raise typer.Exit(code=1) from exc
        console.print(
            f"[yellow]{ticker} is already cached[/yellow] - {entry.rows} bars, "
            f"{entry.start} to {entry.end}, fetched {entry.fetched_at:%Y-%m-%d %H:%M} UTC"
        )
        console.print("Pass [bold]--force[/bold] to re-download and replace the snapshot.")
        return

    # Fetch the whole protocol span in one request. The in-sample window is what
    # a search may read; the walk-forward window is cached but must not be read
    # until validation, and nothing here reads it.
    start = protocol.windows.in_sample.start
    end = protocol.windows.walk_forward.end

    source = YFinanceSource()
    console.print(f"Fetching {ticker} from {source.name}, {start} to {end} ...")

    fetched_at = datetime.now(UTC)
    try:
        frame = source.fetch(ticker, start, end)
    except DataQualityError as exc:
        err_console.print(f"[bold red]fetch failed[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    try:
        validate_bars(frame)
    except DataQualityError as exc:
        err_console.print(f"[bold red]data quality check failed[/bold red] {exc}")
        err_console.print(
            "Nothing was cached. Inspect those dates before assuming the vendor is wrong; "
            "this project never silently repairs a bar."
        )
        raise typer.Exit(code=1) from exc

    entry = cache_write(frame, ticker, DATA_DIR, source=source.name, fetched_at=fetched_at)

    console.print(
        f"[green]cached[/green] {entry.rows} bars, {entry.start} to {entry.end}\n"
        f"  file  {entry.path.as_posix()}\n"
        f"  hash  {entry.content_hash}"
    )
    console.print(
        "\nResults are reproducible against this snapshot, not against the vendor - "
        "vendors revise history."
    )


def _report_ceiling(result: SearchResult) -> None:
    """Print the noise ceiling, or say plainly that there is none.

    THE COMPARISON IS THE POINT, not the number. A ceiling printed in a table
    below a winner is a fact a reader skips; the winner placed against the
    distribution is one they cannot. So this prints where the candidate sits.

    Args:
        result: The finished search.

    Returns:
        None.
    """
    ceiling = result.noise_ceiling
    if ceiling is None:
        err_console.print(
            "\n[bold yellow]No noise ceiling was computed[/bold yellow] - pass "
            "[bold]--trials N[/bold] to compute one. The best of this many cells on data "
            "with no edge in it is well above zero, so nothing above is yet evidence of "
            "anything."
        )
        return

    finite = ceiling.dropna()
    if finite.empty:
        err_console.print(
            "\n[bold red]The noise ceiling is empty[/bold red] - no surrogate produced a "
            "scoreable cell, so there is nothing to compare against."
        )
        return

    console.print(
        f"\nCeiling over {len(ceiling)} surrogate(s) of RESAMPLED HISTORY, not "
        "noise - blocks run as long as the grid's longest lookback, so each is a "
        "verbatim stretch of the real series. See docs/decisions/0008. "
        f"median {finite.quantile(0.50):.3f}, 90th {finite.quantile(0.90):.3f}, "
        f"95th {finite.quantile(0.95):.3f}, max {finite.max():.3f}"
    )
    if result.winner is None:
        console.print("[dim]No candidate to compare against it.[/dim]")
    else:
        beat = int((finite < result.winner.sharpe).sum())
        share = beat / len(finite) * 100.0
        verdict = (
            "[bold green]above[/bold green]" if share >= 95.0 else "[bold red]INSIDE[/bold red]"
        )
        console.print(
            f"The candidate's Sharpe of {result.winner.sharpe:.3f} beats {beat} of "
            f"{len(finite)} surrogates ({share:.0f}th percentile) - {verdict} the noise."
        )
        if share < 95.0:
            console.print(
                "[yellow]A candidate inside the noise distribution is what a search of this "
                "size finds on data with no edge in it.[/yellow]"
            )
    console.print(f"[dim]{trials_note(len(ceiling))}[/dim]")


def _metric(value: float, digits: int) -> str:
    """Render a metric for a table, or say plainly that there is none.

    A fold too short to have a standard deviation has no Sharpe, and printing
    ``0.000`` there would state a result nobody measured. This prints
    :data:`METRIC_ABSENT` instead.

    Args:
        value: The metric, in whatever unit that metric carries.
        digits: Decimal places to show.

    Returns:
        The formatted number, or :data:`METRIC_ABSENT` if it is not finite.
    """
    if not math.isfinite(value):
        return METRIC_ABSENT
    return f"{value:.{digits}f}"


def _repository_relative(path: Path) -> Path:
    """Render a path so nothing machine-specific is recorded in a result file.

    An absolute path on most machines contains the user's name, and a search
    artifact is a file this project's own documentation invites a reader to
    open. A path inside the working directory is written relative to it;
    anything else is reduced to its filename.

    Args:
        path: The path as the user typed it.

    Returns:
        A relative path carrying nothing about the machine it ran on.
    """
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return Path(path.name)


@app.command()
def search(
    config: Annotated[
        Path,
        typer.Argument(help="Strategy config to search, such as configs/ma_crossover.yaml."),
    ],
    trials: Annotated[
        int,
        typer.Option(
            "--trials",
            help=(
                "Surrogate series to run for the noise ceiling. 0 skips it. Each trial "
                "costs a full grid, so this multiplies the runtime directly."
            ),
        ),
    ] = 0,
    seed: Annotated[
        int,
        typer.Option("--seed", help="Seed for the surrogate resampler, so a ceiling repeats."),
    ] = 0,
) -> None:
    """Run the parameter grid over the in-sample window and record the whole surface.

    Walks every combination in the config's search block, scores cells by
    neighbourhood median rather than by their own result, rejects winners
    sitting on a grid boundary or breaching the drawdown ceiling, and flags a
    degenerate plateau. Writes every cell to state/search-<stamp>.json, appends
    each rejection to the graveyard, and NEVER reads the walk-forward window.

    Computes a noise ceiling only when `--trials` is given, because each trial
    re-runs the whole grid and the committed search takes minutes. Without one,
    the command says on every run that nothing it printed is evidence of an
    edge. It renders no HTML either - `strategylab report` does that, from the
    file this writes, so a template change costs no recomputation.
    """
    protocol = _load_or_exit()

    if not config.is_file():
        err_console.print(f"[bold red]no such config[/bold red] {config.as_posix()}")
        raise typer.Exit(code=1)

    try:
        definition = load_strategy(config)
    except StrategyConfigError as exc:
        err_console.print(f"[bold red]config error[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    ticker = protocol.instruments.research
    if not is_cached(ticker, DATA_DIR):
        err_console.print(f"[bold red]{ticker} is not cached[/bold red]")
        err_console.print("Run [bold]uv run strategylab fetch[/bold] first.")
        raise typer.Exit(code=1)
    try:
        bars, entry = cache_read(ticker, DATA_DIR)
    except DataQualityError as exc:
        err_console.print(f"[bold red]the cached snapshot is unusable[/bold red] {exc}")
        err_console.print("Re-download with [bold]uv run strategylab fetch --force[/bold].")
        raise typer.Exit(code=1) from exc

    # ValueError as well as SearchError: expand_grid refuses a malformed range
    # with a ValueError, and that is bad YAML rather than an internal fault.
    # Letting it escape prints a traceback, and a traceback carries the absolute
    # path of the file that raised - which on most machines contains a username.
    try:
        cells = len(expand_grid(definition.search))
    except ValueError as exc:
        err_console.print(f"[bold red]search block is not usable[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Searching [bold]{definition.name}[/bold] - {cells} cells over "
        f"{protocol.windows.in_sample.start} to {protocol.windows.in_sample.end} ..."
    )
    if trials > 0:
        # Say what this will cost BEFORE spending it. A noise ceiling is the
        # one thing here that can turn a four-minute command into an overnight
        # one, and discovering that halfway through is nobody's idea of a tool.
        console.print(
            f"  plus a noise ceiling: {trials} surrogate(s) x {cells} cells. "
            f"That is {trials + 1} full grids in total, so budget roughly "
            f"{trials + 1} times a plain search."
        )
        console.print(f"  [dim]{trials_note(trials)}[/dim]")

    started = datetime.now(UTC)
    try:
        result = run_search(
            definition,
            bars,
            protocol,
            config_path=_repository_relative(config),
            data_content_hash=entry.content_hash,
            noise_trials=trials,
            noise_seed=seed,
        )
    except SearchError as exc:
        err_console.print(f"[bold red]search could not run[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    path = write_search_result(result, started)

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="dim", no_wrap=True)
    summary.add_column()
    summary.add_row(
        "window", f"{result.window_start} to {result.window_end}, {result.bars_used} bars"
    )
    summary.add_row("snapshot", f"{result.instrument}, hash {result.data_content_hash[:12]}")
    summary.add_row(
        "grid",
        f"{result.grid_size} cells"
        + (f", [yellow]{result.failed_cells} failed[/yellow]" if result.failed_cells else ""),
    )
    console.print()
    console.print(summary)

    surface = Table(title="Best cells by plateau score", title_justify="left")
    parameters = [name for name in definition.search]
    for name in parameters:
        surface.add_column(name, justify="right")
    surface.add_column("sharpe", justify="right")
    surface.add_column("drawdown %", justify="right")
    surface.add_column("trades", justify="right")
    surface.add_column("plateau", justify="right")
    surface.add_column("neighbours", justify="right")
    ranked = result.results.sort_values("plateau_score", ascending=False).head(TOP_CELLS_SHOWN)
    for row in ranked.to_dict(orient="records"):
        surface.add_row(
            *[str(int(row[name])) for name in parameters],
            f"{row['sharpe']:.3f}",
            f"{row['max_drawdown_pct']:.2f}",
            str(int(row["trade_count"])),
            f"{row['plateau_score']:.3f}",
            str(int(row["neighbour_count"])),
        )
    console.print()
    console.print(surface)
    if len(result.results) > TOP_CELLS_SHOWN:
        console.print(
            f"[dim]{len(result.results) - TOP_CELLS_SHOWN} further cells are in the "
            f"result file. The shape of the whole surface is the output here, "
            f"not this table.[/dim]"
        )

    console.print()
    if result.winner is None:
        console.print("[yellow]No candidate survived.[/yellow]")
    else:
        winner = result.winner
        cell = ", ".join(f"{name}={winner.cell[name]}" for name in parameters)
        console.print(f"[bold green]Candidate[/bold green]  {cell}")
        console.print(
            f"  plateau score {winner.plateau_score:.3f} over {winner.neighbour_count} "
            f"cells, own Sharpe {winner.sharpe:.3f}, drawdown {winner.max_drawdown_pct:.2f}%"
        )
        if winner.degenerate:
            console.print(
                "  [yellow]degenerate plateau[/yellow] - its neighbours executed the same "
                "trades, so the flat region is insensitivity, not robustness"
            )

    if result.rejections:
        counts: dict[str, int] = {}
        for rejection in result.rejections:
            counts[rejection.reason] = counts.get(rejection.reason, 0) + 1
        tally = ", ".join(f"{count} {reason}" for reason, count in sorted(counts.items()))
        console.print(
            f"\nRejected: {tally}. All of them are in {DEFAULT_GRAVEYARD_PATH.as_posix()}."
        )

    console.print(f"\n  result  {path.as_posix()}")
    _report_ceiling(result)


@app.command()
def validate(
    result: Annotated[
        Path,
        typer.Argument(
            help="Search result whose candidate to validate, such as "
            "state/search-20260822T202249Z.json."
        ),
    ],
) -> None:
    """Walk a search's candidate forward through the held-back window.

    Rolls through the walk-forward window in folds, pools the results, and
    returns pass, fail, or insufficient_data. Increments the protocol's access
    counter on every run, including runs that end in insufficient_data: looking
    at held-back data costs a touch whether or not the look produced a verdict.

    WRITES `state/validation-<stamp>.json`, and the file is the point rather
    than a side effect: re-deriving a verdict costs another permanent read of
    the held-back window, so a verdict that lived only in this terminal would
    guarantee somebody spent one. It is written AFTER the verdict is printed,
    so a failure to write costs the file and never the answer.

    THE ARGUMENT IS A SEARCH ARTIFACT, NOT A SEPARATE CANDIDATE FILE. This
    command's help text used to name ``state/candidate.json``, and nothing has
    ever written one: the search loop deliberately declined to invent that
    schema, on the grounds that the validator owns it. Choosing the artifact
    instead settles it the other way, for three reasons.

    The artifact already carries everything a validation needs to be
    interpretable - the winning parameters, the protocol version in force, the
    content hash of the price snapshot, the costs charged, and the noise ceiling
    if one was computed. A candidate file would either duplicate those or drop
    them, and a verdict recorded without them cannot be traced to what produced
    it.

    A second file is a second thing to drift. This repository already carries
    one instance of that mistake - two decision-log shapes where the design says
    one - and it is documented in ``LIMITATIONS.md`` as a cost still being paid.

    And it keeps the chain honest end to end: a verdict points at a search,
    which points at a snapshot hash and a config. Nothing in between can be
    edited by hand without the trail showing it.

    THE COST OF THIS CHOICE, stated because it is real: a human cannot easily
    hand-write a candidate to validate. Testing an arbitrary parameter set means
    running a search that nominates it, which is minutes of work rather than
    seconds. That is a deliberate friction rather than an oversight - a
    parameter set nobody's search chose is not a candidate, it is a guess, and
    the held-back window is too expensive to spend on guesses.

    THERE IS NO CONFIRMATION PROMPT, and there is no dry run. Typing this
    command IS the decision to spend a touch; a prompt would only be a second
    place to change one's mind, and a mode that showed what validation would do
    without paying for it is the loophole the counter exists to close. What this
    command does instead is say the cost out loud before it is incurred, and
    again afterwards.
    """
    protocol = _load_or_exit()

    if not result.is_file():
        err_console.print(f"[bold red]no such search result[/bold red] {result.as_posix()}")
        err_console.print(f"Look in {STATE_DIR.as_posix()}/ for a search-*.json file.")
        raise typer.Exit(code=1)

    ticker = protocol.instruments.research
    if not is_cached(ticker, DATA_DIR):
        err_console.print(f"[bold red]{ticker} is not cached[/bold red]")
        err_console.print("Run [bold]uv run strategylab fetch[/bold] first.")
        raise typer.Exit(code=1)
    try:
        bars, entry = cache_read(ticker, DATA_DIR)
    except DataQualityError as exc:
        err_console.print(f"[bold red]the cached snapshot is unusable[/bold red] {exc}")
        err_console.print("Re-download with [bold]uv run strategylab fetch --force[/bold].")
        raise typer.Exit(code=1) from exc

    # Every refusal below happens BEFORE the held-back window is read, which is
    # the point of doing them here rather than discovering them mid-run.
    try:
        candidate = read_candidate(result, protocol, entry.content_hash)
    except CandidateError as exc:
        err_console.print(f"[bold red]nothing to validate[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    try:
        definition = load_strategy(candidate.config_path)
    except StrategyConfigError as exc:
        err_console.print(f"[bold red]config error[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    cell = ", ".join(f"{name}={value}" for name, value in candidate.params.items())
    console.print(f"Validating [bold]{definition.name}[/bold]  {cell}")
    header = Table(show_header=False, box=None, pad_edge=False)
    header.add_column(style="dim", no_wrap=True)
    header.add_column()
    header.add_row("from", result.as_posix())
    header.add_row("config", candidate.config_path.as_posix())
    header.add_row("snapshot", f"{ticker}, hash {entry.content_hash[:12]}")
    header.add_row(
        "window",
        f"{protocol.windows.walk_forward.start} to {protocol.windows.walk_forward.end}, "
        f"folds of {protocol.windows.fold_months} month(s)",
    )
    header.add_row(
        "bar",
        f"Sharpe {protocol.validation.effective_sharpe_bar:.2f} "
        f"({protocol.validation.min_sharpe:.2f} + {protocol.validation.pass_margin:.2f} "
        f"margin), drawdown ceiling "
        f"{protocol.validation.max_drawdown_ceiling_pct:.0f}%, "
        f"{protocol.validation.min_trades_pooled} trades pooled",
    )
    console.print(header)
    console.print(
        f"\n[bold yellow]This reads the held-back window.[/bold yellow] It has been read "
        f"{protocol.walk_forward_touches} time(s) before; this run makes it "
        f"{protocol.walk_forward_touches + 1}, recorded permanently in protocol.yaml.\n"
    )

    started = datetime.now(UTC)
    # Every exception type `walk_forward` documents is caught, because a
    # traceback carries the absolute path of the file that raised and on most
    # machines that contains a username. Five of its ValueErrors are raised
    # BEFORE the touch, and the WindowError on the in-sample cut is too; the
    # rest arrive after it. The message hedges accordingly rather than asserting
    # which happened.
    try:
        verdict = walk_forward(definition, candidate.params, bars, protocol)
    except (ValueError, WindowError, ProtocolError) as exc:
        err_console.print(f"[bold red]validation could not run[/bold red] {exc}")
        err_console.print(
            "If the failure came after the window was read, the touch has been charged. "
            "Check access_log.walk_forward_touches in protocol.yaml."
        )
        raise typer.Exit(code=1) from exc
    except OSError as exc:
        # The one failure mode that can DAMAGE the pre-registration - but only
        # one of its causes actually does. A read-only file or a permission
        # denial fails at `open("w")`, before any truncation: protocol.yaml is
        # byte-for-byte intact and NO touch was charged. A failure part-way
        # through the write is the damaging one, because the rewrite is not
        # atomic. Which happened is not knowable from here, so both are named
        # rather than the worse one being asserted.
        err_console.print(f"[bold red]the protocol file could not be rewritten[/bold red] {exc}")
        err_console.print(
            "If the file could not be opened for writing at all - read-only, permission "
            "denied - then protocol.yaml is untouched and NO walk-forward read was "
            "charged. If the write failed part-way, the counter is rewritten in place and "
            "NOT atomically, so the access_log block may be incomplete. Check it, and "
            "restore from version control if it is - that is where its authoritative copy "
            "lives."
        )
        raise typer.Exit(code=1) from exc

    folds = Table(title="Folds", title_justify="left")
    folds.add_column("#", justify="right")
    folds.add_column("from")
    folds.add_column("to")
    folds.add_column("sharpe", justify="right")
    folds.add_column("drawdown %", justify="right")
    folds.add_column("trades", justify="right")
    folds.add_column("outcome")
    for fold in verdict.folds:
        folds.add_row(
            str(fold.index),
            str(fold.start),
            str(fold.end),
            _metric(fold.sharpe, 3),
            _metric(fold.max_drawdown_pct, 2),
            str(fold.trade_count),
            fold.outcome.value,
        )
    console.print()
    if verdict.folds:
        console.print(folds)
        console.print(
            "[dim]Per-fold outcomes are advisory. The decision below is made on the folds "
            "pooled together, which is the less noisy statistic.[/dim]"
        )

    console.print()
    console.print(f"[bold {VERDICT_STYLE[verdict.outcome]}]{verdict.outcome.value.upper()}[/]")
    console.print(f"  {verdict.reason}")
    console.print(
        f"  pooled over {verdict.bars_used} held-back bar(s): "
        f"Sharpe {_metric(verdict.pooled_sharpe, 3)}, "
        f"drawdown {_metric(verdict.pooled_max_drawdown_pct, 2)}%, "
        f"{verdict.total_trades} trade(s)"
    )
    console.print(
        f"  in-sample, for comparison: Sharpe {_metric(candidate.search_sharpe, 3)} "
        f"(plateau {_metric(candidate.search_plateau_score, 3)})"
    )
    if candidate.degenerate:
        console.print(
            "  [yellow]the search flagged this plateau as degenerate[/yellow] - its "
            "neighbours executed the same trades"
        )

    console.print(
        f"\n  held-back window read [bold]{verdict.walk_forward_touches}[/bold] time(s) in total"
    )
    # PRINTED BEFORE THE WRITE, so it survives a failed one. This sentence is
    # the caveat `candidate.py` promises "travels into the verdict rather than
    # being dropped at the boundary", and on the write-failure path the terminal
    # is the only place it can reach.
    console.print(f"\n[dim]{candidate.noise_ceiling_note}[/dim]")

    # THE ARTIFACT IS WRITTEN LAST, AFTER THE VERDICT HAS BEEN PRINTED. An
    # earlier version wrote it inside the same try as `walk_forward` and exited
    # on failure, which threw away a verdict a permanent touch had already been
    # spent on - the exact loss the artifact exists to prevent, caused by the
    # error handling around it. Now a failed write costs the file and not the
    # answer: the operator has the verdict on screen, and is told what the
    # screen does NOT have.
    try:
        artifact = write_validation_result(verdict, candidate, protocol, started)
    except (ValueError, OSError) as exc:
        err_console.print(f"\n[bold red]the verdict could not be written[/bold red] {exc}")
        err_console.print(
            "The touch HAS been charged and what is printed above is the only copy. "
            "Re-running would spend another read of the held-back window, so save it now."
        )
        err_console.print(
            "The artifact would also have recorded the schema and protocol versions, the "
            "warm-up length, and every cost parameter the run was charged. Those are not "
            "on screen; take them from protocol.yaml and the search artifact."
        )
        raise typer.Exit(code=1) from exc
    console.print(f"  verdict  {artifact.as_posix()}")


@app.command()
def report() -> None:
    """Regenerate the HTML reports from stored results, without recomputing them.

    Reads results already on disk and re-renders them. Runs no backtest and
    touches no window, so a change to the report template costs nothing in
    protocol access.

    Every search artifact in state/ is re-rendered, not just the newest: a
    template change should improve every figure you have, and re-rendering one
    of a set would leave the rest silently on an older design.
    """
    artifacts = sorted(STATE_DIR.glob("search-*.json")) if STATE_DIR.exists() else []
    if not artifacts:
        err_console.print(f"[bold red]no search results in {STATE_DIR.as_posix()}/[/bold red]")
        err_console.print(
            "Run [bold]uv run strategylab search configs/ma_crossover.yaml[/bold] first."
        )
        raise typer.Exit(code=1)

    console.print(f"Rendering {len(artifacts)} result(s) ...")
    written: list[Path] = []
    for artifact in artifacts:
        try:
            written.append(render_surface(artifact))
        except (ReportError, ValueError) as exc:
            # One unrenderable artifact must not cost the operator the rest of
            # their reports, so this reports and carries on rather than exiting.
            #
            # ValueError covers malformed JSON too: `json.JSONDecodeError`
            # subclasses it, so a separate clause for that was unreachable and
            # has been removed rather than left looking like coverage.
            err_console.print(f"[yellow]skipped[/yellow] {artifact.name} - {exc}")

    if not written:
        err_console.print("[bold red]nothing could be rendered[/bold red]")
        raise typer.Exit(code=1)

    console.print()
    for path in written:
        console.print(f"  [green]{path.as_posix()}[/green]")
    console.print(
        "\n[dim]Self-contained pages - no network, no companion files. Hover a cell for "
        "its metrics.[/dim]"
    )
    console.print(
        "[dim]A figure shows a noise ceiling only if its search was run with --trials, "
        "and says which case applies on its face.[/dim]"
    )


#: How much history the worker fetches for the execution instrument, as a
#: multiple of the strategy's warm-up in bars, converted to calendar days at a
#: generous rate so weekends and holidays cannot leave the window short.
WARMUP_CALENDAR_DAYS_PER_BAR = 2
WARMUP_CALENDAR_DAYS_FLOOR = 60


def _candidate_or_exit(result: Path, protocol: Protocol) -> tuple[Any, Any]:
    """Load the search artifact's winner and its strategy, or exit non-zero.

    Args:
        result: The search artifact.
        protocol: The loaded protocol.

    Returns:
        The candidate and the parsed strategy definition.

    Raises:
        typer.Exit: With code 1 if the research snapshot is not cached, the
            artifact does not match it, or the config cannot be loaded.
    """
    ticker = protocol.instruments.research
    if not is_cached(ticker, DATA_DIR):
        err_console.print(f"[bold red]{ticker} is not cached[/bold red] - run `strategylab fetch`")
        raise typer.Exit(code=1)
    try:
        _, entry = cache_read(ticker, DATA_DIR)
        candidate = read_candidate(result, protocol, entry.content_hash)
        definition = load_strategy(candidate.config_path)
    except (DataQualityError, CandidateError, StrategyConfigError) as exc:
        err_console.print(f"[bold red]cannot load the candidate[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    return candidate, definition


@app.command()
def run(
    result: Annotated[
        Path,
        typer.Argument(
            help="Search artifact whose candidate to trade, such as "
            "state/search-20260822T213227Z.json. A validation is not required and "
            "nothing here checks for one: what to run is the operator's decision."
        ),
    ],
) -> None:
    """Run the paper-trading worker for ONE bar against the demo venue, then exit.

    Scheduled once per session after the close rather than left running. In
    order: fetches the execution instrument's latest bars (THE ONE OTHER COMMAND
    THAT USES THE NETWORK, besides fetch) from the day after the last cached
    bar or the warm-up span, whichever is earlier - so a pause longer than the
    span cannot leave a gap the validator refuses - and merges them into the
    cache with bars already cached KEPT, so history once written is never
    rewritten by a later fetch and `diff` replays what the deciding run saw. The
    cost of that rule: run DURING a session and the in-progress bar is cached
    at its interim close, permanently, and no command re-downloads the
    execution instrument. Run after the close. Then reconciles against the
    venue; evaluates the latest bar
    through the same core.evaluate the backtester uses; passes the intent
    through the risk layer; writes ONE decision record to
    state/decisions-live.jsonl; and then sends at most one market order.

    THE RECORD IS WRITTEN BEFORE THE ORDER IS SENT, so a venue failure never
    costs the log the decision that preceded it; the record says what the gate
    answered, and an order that failed is reported on the terminal and nowhere
    else. Live data never selects anything.

    Refuses to start unless T212_ENVIRONMENT is `demo`. Nothing can tell a
    demo KEY from a live one - the guard is on the environment string, and that
    same string selects the base URL. A live key is a separate, later decision,
    and this command does not take it. Optional: T212_TICKER, the venue's own
    spelling of the execution instrument, applied on the way out and translated
    back on the way in; unset, the protocol's symbol is sent as written.
    """
    protocol = _load_or_exit()

    environment = os.environ.get("T212_ENVIRONMENT", "")
    api_key = os.environ.get("T212_API_KEY", "")
    if environment != "demo":
        err_console.print(
            f"[bold red]refusing to run[/bold red] T212_ENVIRONMENT is {environment!r}; this "
            "command runs against the demo venue only. Export the values in .env first."
        )
        raise typer.Exit(code=1)
    if not api_key.strip():
        err_console.print("[bold red]refusing to run[/bold red] T212_API_KEY is not set")
        raise typer.Exit(code=1)

    candidate, definition = _candidate_or_exit(result, protocol)
    try:
        limits = load_limits()
    except (FileNotFoundError, ValueError) as exc:
        err_console.print(f"[bold red]risk limits[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    ticker = protocol.instruments.execution
    now = datetime.now(UTC)
    today = now.date()
    warmup = required_history(definition, candidate.params)
    span = max(WARMUP_CALENDAR_DAYS_FLOOR, WARMUP_CALENDAR_DAYS_PER_BAR * warmup)
    source = YFinanceSource()
    console.print(f"Fetching {ticker} from {source.name}, last {span} days ...")
    try:
        start = today - timedelta(days=span)
        cached = None
        if is_cached(ticker, DATA_DIR):
            cached, _ = cache_read(ticker, DATA_DIR)
            # From the day after the cache ends if that is earlier than the
            # span: a worker paused for longer than the span would otherwise
            # fetch a window that starts after the cache ends, and the merged
            # frame's gap would fail validation on every later run with no
            # command able to clear it.
            start = min(start, cached.index[-1].date() + timedelta(days=1))
        fresh = source.fetch(ticker, start, today)
        if cached is not None:
            # The CACHE wins on any date both hold. A vendor revision inside the
            # overlap is thereby not silently adopted, and what diff replays is
            # what the deciding run saw. The sidecar's fetched_at is the time of
            # this merge, not of every bar in it.
            fresh = cached.combine_first(fresh).sort_index()
        validate_bars(fresh)
        entry = cache_write(fresh, ticker, DATA_DIR, source=source.name, fetched_at=now)
    except (DataQualityError, OSError) as exc:
        err_console.print(f"[bold red]bars[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    bars = fresh
    if len(bars) < warmup:
        err_console.print(
            f"[bold red]too few bars[/bold red] {len(bars)} cached, {warmup} needed for warm-up"
        )
        raise typer.Exit(code=1)
    console.print(f"  cached {entry.rows} bars to {entry.path.as_posix()}")

    venue_ticker = os.environ.get("T212_TICKER", "").strip()
    symbol_map = {ticker: venue_ticker} if venue_ticker else None
    try:
        venue = T212Venue(api_key, environment, symbol_map=symbol_map)
        reconciled = venue.reconcile()
        balance = venue.get_balance()
        positions = list(reconciled.venue_positions)
    except (T212Error, ValueError, OSError) as exc:
        err_console.print(f"[bold red]venue[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    for line in reconciled.discrepancies:
        console.print(f"  [yellow]reconciled[/yellow] {line}")

    session = load_session(DEFAULT_SESSION_PATH, today, balance.equity)
    try:
        save_session(session, DEFAULT_SESSION_PATH)
    except OSError as exc:
        err_console.print(f"[bold red]session file[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    state = portfolio_state(balance, positions, session)

    bar_stamp = bars.index[-1].to_pydatetime()
    try:
        intent, verdict = decide(definition, candidate.params, bars, state, limits)
        entry_record = record(
            intent,
            verdict,
            bar_timestamp=bar_stamp,
            recorded_at=now,
            source="live",
            ticker=ticker,
            params=candidate.params,
            protocol_version=protocol.protocol_version,
        )
        write_decision(entry_record, DEFAULT_LIVE_LOG_PATH)
        order = order_for(
            intent,
            verdict,
            balance,
            positions,
            ticker,
            float(bars["close"].iloc[-1]),
            # Unique per intended order, as the interface asks: the bar alone
            # repeats when the worker is invoked twice after one close.
            client_ref=f"{ticker}-{bar_stamp:%Y%m%d}-{now:%Y%m%dT%H%M%S}",
        )
    except (ValueError, OSError) as exc:
        err_console.print(f"[bold red]decision[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", no_wrap=True)
    table.add_column()
    table.add_row("bar", f"{bar_stamp:%Y-%m-%d}  {ticker} close {bars['close'].iloc[-1]:.4f}")
    table.add_row(
        "account", f"{balance.equity:.2f} {balance.currency} equity, {len(positions)} position(s)"
    )
    table.add_row("intent", f"{intent.action.value}  requested {intent.size_pct:g}%")
    table.add_row("risk", f"{verdict.verdict.value}  {verdict.reason}")
    table.add_row("log", DEFAULT_LIVE_LOG_PATH.as_posix())
    console.print(table)

    if order is None:
        console.print("\nNo order to send.")
        return
    try:
        order_id = venue.place_order(order)
    except T212Error as exc:
        err_console.print(f"[bold red]order failed[/bold red] {exc}")
        err_console.print("The decision record above was written; the order was not confirmed.")
        raise typer.Exit(code=1) from exc
    console.print(
        f"\n[green]sent[/green] {order.side.value} {order.quantity:.6f} {order.ticker} "
        f"(venue id {order_id})"
    )


@app.command()
def diff(
    result: Annotated[
        Path,
        typer.Argument(help="The search artifact the worker was run with."),
    ],
) -> None:
    """Compare the live decision log against a replay of the same bars.

    Re-decides every bar in state/decisions-live.jsonl from the cached bars
    alone, through the same core.evaluate, writes the replay to
    state/decisions-replay.jsonl (replaced each run), and reports every field on
    which the two disagree. Uses no network: the bars are what `run` cached.

    Compares the strategy's decision - parameters, action, conditions,
    indicator values, thresholds, requested size - and NOT the risk fields, which differ by
    construction because the replay does not run the gate. A `params`
    divergence on every bar means this command was given a different search
    artifact than `run` was. A divergence is a finding about
    the simulator, not a failure; the exit code is zero either way.
    """
    protocol = _load_or_exit()
    try:
        live = read_decisions(DEFAULT_LIVE_LOG_PATH)
    except (OSError, ValueError) as exc:
        err_console.print(f"[bold red]live log[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    if not live:
        err_console.print("[bold red]nothing to diff[/bold red] the live log is empty; run first")
        raise typer.Exit(code=1)

    candidate, definition = _candidate_or_exit(result, protocol)
    ticker = protocol.instruments.execution
    if not is_cached(ticker, DATA_DIR):
        err_console.print(f"[bold red]{ticker} is not cached[/bold red] - `run` caches it")
        raise typer.Exit(code=1)
    try:
        bars, _ = cache_read(ticker, DATA_DIR)
        replayed = replay(definition, candidate.params, bars, live, datetime.now(UTC))
    except (DataQualityError, ValueError) as exc:
        err_console.print(f"[bold red]replay[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    try:
        DEFAULT_REPLAY_LOG_PATH.unlink(missing_ok=True)
        written = write_decisions(replayed, DEFAULT_REPLAY_LOG_PATH)
    except OSError as exc:
        err_console.print(f"[bold red]replay log[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    report = compare(live, replayed)
    console.print(
        f"Compared {report.compared} bar(s): {report.diverging_bars} diverge, "
        f"{len(report.missing_in_replay)} could not be replayed."
    )
    if report.divergences:
        table = Table(box=None, pad_edge=False)
        table.add_column("bar", style="dim", no_wrap=True)
        table.add_column("field", no_wrap=True)
        table.add_column("live")
        table.add_column("replay")
        for item in report.divergences:
            table.add_row(f"{item.bar_timestamp:%Y-%m-%d}", item.field, item.live, item.replay)
        console.print(table)
    for stamp in report.missing_in_replay:
        console.print(f"  [yellow]missing[/yellow] {stamp:%Y-%m-%d} is not in the cached bars")
    if written:
        console.print(f"\nReplay written to {DEFAULT_REPLAY_LOG_PATH.as_posix()}.")
    else:
        console.print("\nNo bar could be replayed, so no replay log was written.")
    console.print("The risk fields are not compared: the replay does not run the gate.")


if __name__ == "__main__":
    app()

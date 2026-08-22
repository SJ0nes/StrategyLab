"""The command-line interface.

Every command is registered here with help text a stranger could follow. The
bodies are built one per session, in the order the roadmap gives, so that each
arrives with its own tests rather than as part of a large unreviewable change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from strategylab.core.config import StrategyConfigError, load_strategy
from strategylab.data.source import (
    DataQualityError,
    YFinanceSource,
    cache_read,
    cache_write,
    is_cached,
    validate_bars,
)
from strategylab.protocol import Protocol, ProtocolError, load_protocol
from strategylab.report.surface import ReportError, render_surface
from strategylab.search.graveyard import DEFAULT_GRAVEYARD_PATH
from strategylab.search.grid import expand_grid
from strategylab.search.loop import (
    SearchError,
    SearchResult,
    run_search,
    write_search_result,
)
from strategylab.search.noise import trials_note

DATA_DIR = Path("data")
STATE_DIR = Path("state")

#: How many cells `search` prints. The whole surface goes to the result file;
#: a ninety-row table in a terminal is unreadable and would bury the caveat
#: underneath it, which is the one line of that output that must be read.
TOP_CELLS_SHOWN = 10

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

    Reads the protocol, checks whether price data is cached, counts the
    searches run so far, and reports the pass criteria and how many times the
    held-back window has been read. Names the next command
    but NEVER runs it - a status command that changes state cannot be run
    freely, and one that cannot be run freely does not get run.
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
    table.add_row("searches run", str(searches))
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
        console.print("       Downloads and caches price data. The one command using the network.")
        return

    if searches == 0:
        console.print("Next:  [bold]uv run strategylab search configs/ma_crossover.yaml[/bold]")
        console.print("       Walks the grid over the in-sample window, writes every cell to")
        console.print("       state/. Takes a few minutes.")
        return

    console.print("Next:  [bold]uv run strategylab report[/bold]")
    console.print("       Draws each stored search as a self-contained page in reports/.")
    console.print("       Recomputes nothing and reads no window.")
    console.print()
    console.print(
        "[yellow]A search reports a noise ceiling only when given --trials[/yellow]; "
        "without one, nothing it printed is evidence of an edge.\nThe validator is not "
        "built at all, so no candidate has been tested on the held-back window."
    )


@app.command()
def fetch(
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-download even if a cached snapshot already exists."),
    ] = False,
) -> None:
    """Download and cache price data for the protocol's research instrument.

    The one command in this project that touches the network. Writes a parquet
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
    """
    raise NotImplementedError


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


@app.command()
def run() -> None:
    """Start the live paper-trading worker against the demo venue.

    Reconciles against the venue on startup, then evaluates each bar through
    the same core.evaluate the backtester uses, passes every intent through the
    risk layer, and writes a decision record per bar. Live data never selects
    anything: it accumulates into history for future searches.
    """
    raise NotImplementedError


@app.command()
def diff() -> None:
    """Compare the live decision log against a backtest replay of the same period.

    Replays the backtester over the dates the live worker ran and diffs the two
    decision logs, which share a schema for exactly this purpose. Measures how
    far live results drift from what the backtest predicted - which tests the
    simulator, not the strategy.
    """
    raise NotImplementedError


if __name__ == "__main__":
    app()

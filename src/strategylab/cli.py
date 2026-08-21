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

from strategylab.data.source import (
    DataQualityError,
    YFinanceSource,
    cache_read,
    cache_write,
    is_cached,
    validate_bars,
)
from strategylab.protocol import Protocol, ProtocolError, load_protocol

DATA_DIR = Path("data")
STATE_DIR = Path("state")

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
    searches run so far, and reports the state of each. Names the next command
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
        console.print("Next:  [bold]uv run strategylab fetch[/bold]")
        console.print("       Downloads and caches price data. The one command using the network.")
        return

    console.print("Next:  [bold]uv run strategylab search configs/ma_crossover.yaml[/bold]")
    console.print("       Not built yet - the search component is a later session.")


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
        _, entry = cache_read(ticker, DATA_DIR)
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


@app.command()
def search(
    config: Annotated[
        Path,
        typer.Argument(help="Strategy config to search, such as configs/ma_crossover.yaml."),
    ],
) -> None:
    """Run the parameter grid over the in-sample window and write an HTML report.

    Walks every combination in the config's search block, scores cells by
    neighbourhood median rather than by their own result, rejects winners
    sitting on a grid boundary, flags degenerate plateaus, and reports the
    noise ceiling alongside the winner. Never touches the walk-forward window.
    """
    raise NotImplementedError


@app.command()
def validate(
    candidate: Annotated[
        Path,
        typer.Argument(help="Candidate produced by a search, such as state/candidate.json."),
    ],
) -> None:
    """Walk a chosen candidate forward through the held-back window.

    Rolls through the walk-forward window in folds, pools the results, and
    returns pass, fail, or insufficient_data. Increments the protocol's access
    counter on every run, including runs that end in insufficient_data: looking
    at held-back data costs a touch whether or not the look produced a verdict.
    """
    raise NotImplementedError


@app.command()
def report() -> None:
    """Regenerate the HTML reports from stored results, without recomputing them.

    Reads results already on disk and re-renders them. Runs no backtest and
    touches no window, so a change to the report template costs nothing in
    protocol access.
    """
    raise NotImplementedError


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

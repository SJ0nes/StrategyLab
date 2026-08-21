"""The command-line interface.

Every command is registered here with help text a stranger could follow. The
bodies are built one per session, in the order the roadmap gives, so that each
arrives with its own tests rather than as part of a large unreviewable change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

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
    raise NotImplementedError


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
    raise NotImplementedError


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

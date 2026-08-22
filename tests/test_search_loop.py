"""Tests for the search loop - the piece that joins the grid, the engine and the scoring.

EVERY EXPECTED NUMBER IN THIS FILE IS DERIVED FROM A RULE STATED BESIDE IT, or
recomputed from the fixture inside the test. Nothing here was read off what the
implementation printed.

**The window guard is the test that matters.** `strategylab fetch` caches
2005-2024 in one parquet file and nothing partitions it, so the in-sample /
walk-forward split - this project's central methodological claim - is enforced
by `_in_sample_window` and by nothing else.
`test_bars_outside_the_window_cannot_change_any_cell` corrupts every bar after
the window's end, passes the frame in INTACT, and asserts every cell's result is
unchanged. A loop that read one bar past the end fails it. That is the same
shape as `tests/test_engine.py`'s lookahead test, and for the same reason: the
guarantee lives in a slice, so the test must attack the slice.

**The plateau tables.** `select_candidate` is exercised against hand-built
frames rather than against real backtests, because the property under test is
"the winner is the neighbourhood median, not the peak" and a real grid may not
contain a peak and a plateau at convenient places. Each frame states its own
median table. Recall the rule from `search/plateau.py`: a cell's score is the
MEDIAN of the cells within `radius` steps of it along every axis, the cell
counting as its own neighbour, so at radius 1 on a single axis an interior cell
scores over three values and a boundary cell over two.

**Grid sizes.** A range `{min, max, step}` yields `floor((max - min) / step) + 1`
values, and a grid is the product over its parameters. `{min: 3, max: 7, step: 2}`
gives 3 values and `{min: 10, max: 20, step: 5}` gives 3, so the small grid used
throughout is 9 cells.

**Warm-up.** The engine needs the longest lookback anywhere in the strategy plus
one bar if any operator crosses. The small strategy's longest lookback is
`slow`, and both its operators cross, so a cell needs `slow + 1` bars and fails
outright when the window holds fewer.

**What no test here claims.** Nothing in this file is evidence about the
strategy, and no assertion is a figure the implementation produced. The one
end-to-end test over the committed config asserts shape - ninety rows, one per
cell - not performance.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from strategylab.backtest.costs import CostBreakdown, CostModel, Fill
from strategylab.core.config import load_strategy
from strategylab.core.types import Condition, IndicatorRef, Side, StrategyDef
from strategylab.protocol import Instruments, Protocol, Validation, Window, Windows
from strategylab.search.graveyard import REJECTION_REASONS
from strategylab.search.loop import (
    FAILED_PREFIX,
    NO_TRADES_FINGERPRINT,
    SEARCH_SCHEMA_VERSION,
    TRADED_PREFIX,
    Candidate,
    SearchError,
    failed_fingerprint,
    run_search,
    select_candidate,
    trade_fingerprint,
    write_search_result,
)
from strategylab.search.plateau import FINGERPRINT_COLUMN, METRIC_COLUMN, plateau_score

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CONFIG = REPO_ROOT / "configs" / "ma_crossover.yaml"
FIXTURE_BARS = Path(__file__).resolve().parent / "fixtures" / "SPY.parquet"

#: Last date the test protocol's in-sample window admits. The fixture spans
#: 2010-01-04 to 2011-01-03, so a mid-year cut leaves roughly half the frame
#: outside the window - enough bars to corrupt for the guard test to bite.
WINDOW_END = date(2010, 6, 30)

#: Costs used throughout, in FRACTIONS. Five basis points of commission, three
#: of spread, five of slippage - the committed protocol's rates, already
#: converted, so no test depends on the conversion this file is not testing.
TEST_COSTS = CostModel(
    spread_frac=0.0003,
    slippage_frac=0.0005,
    commission_frac=0.0005,
    commission_min_flat=0.0,
    fx_conversion_frac=0.0,
)


def _protocol(
    *,
    window_end: date = WINDOW_END,
    primary_metric: str = "sharpe",
    max_drawdown_ceiling_pct: float = 25.0,
) -> Protocol:
    """Build a protocol for the tests, without touching `protocol.yaml`.

    The committed protocol's in-sample window is 2005-2018, which the 2010
    fixture sits entirely inside - so it could never exercise the window guard.
    These tests need a window the fixture straddles.
    """
    return Protocol(
        protocol_version=1,
        created=date(2026, 8, 21),
        instruments=Instruments(research="SPY", execution="VUAG.L"),
        windows=Windows(
            in_sample=Window(start=date(2010, 1, 1), end=window_end),
            walk_forward=Window(start=date(2010, 7, 1), end=date(2011, 12, 31)),
            fold_months=12,
        ),
        costs=TEST_COSTS,
        validation=Validation(
            primary_metric=primary_metric,
            min_sharpe=1.0,
            min_trades_per_fold=10,
            min_trades_pooled=30,
            max_drawdown_ceiling_pct=max_drawdown_ceiling_pct,
            pass_margin=0.2,
        ),
        walk_forward_touches=0,
    )


def _small_strategy(search: dict[str, dict[str, float]] | None = None) -> StrategyDef:
    """A two-parameter crossover, small enough to backtest a whole grid quickly.

    Two conditions, both crossing operators, so the warm-up is `slow + 1`.
    """
    fast = IndicatorRef("sma", period_param="fast")
    slow = IndicatorRef("sma", period_param="slow")
    return StrategyDef(
        name="small_crossover",
        version=1,
        description="Test strategy. Enter when fast crosses above slow, exit when it crosses back.",
        entry=(Condition(fast, "crosses_above", slow),),
        exit=(Condition(fast, "crosses_below", slow),),
        search=search
        if search is not None
        else {
            "fast": {"min": 3, "max": 7, "step": 2},
            "slow": {"min": 10, "max": 20, "step": 5},
        },
        fixed={"position_size_pct": 95.0},
    )


@pytest.fixture(scope="module")
def fixture_bars() -> pd.DataFrame:
    """The recorded SPY bars in `tests/fixtures/`. No network call."""
    return pd.read_parquet(FIXTURE_BARS)


def _fill(side: Side, mid: float, quantity: float) -> Fill:
    """One fill, with a zeroed breakdown - the fingerprint does not read it."""
    return Fill(
        side=side,
        mid_price=mid,
        executed_price=mid,
        quantity=quantity,
        costs=CostBreakdown(spread=0.0, slippage=0.0, commission=0.0, fx=0.0),
    )


def _corrupt_after(bars: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Return a copy of `bars` with every bar after `cutoff` made absurd.

    Multiplied by a thousand rather than randomised, so the corruption is
    deterministic and its effect on any metric would be unmissable. A loop that
    reads one bar past the window produces a different Sharpe on the cells whose
    windows reach it, and the guard test says so.
    """
    corrupted = bars.copy()
    after = pd.DatetimeIndex(corrupted.index).normalize() > pd.Timestamp(cutoff)
    for column in ("open", "high", "low", "close"):
        corrupted.loc[after, column] = corrupted.loc[after, column] * 1000.0
    return corrupted


# --------------------------------------------------------------------------
# The window guard.
# --------------------------------------------------------------------------


def test_bars_outside_the_window_cannot_change_any_cell(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # The guarantee the whole project rests on. The frame is passed in INTACT -
    # nothing is sliced away before the call - so a loop that read a single bar
    # past the window's end would produce different metrics here.
    protocol = _protocol()
    strategy = _small_strategy()

    clean = run_search(
        strategy,
        fixture_bars,
        protocol,
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )
    corrupted = run_search(
        strategy,
        _corrupt_after(fixture_bars, WINDOW_END),
        protocol,
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )

    pd.testing.assert_frame_equal(clean.results, corrupted.results)
    assert clean.winner == corrupted.winner


def test_the_window_is_taken_from_the_protocol_not_from_the_frame(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    protocol = _protocol()
    result = run_search(
        _small_strategy(),
        fixture_bars,
        protocol,
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )

    # Recomputed from the fixture rather than hardcoded: the count is a property
    # of the recorded bars, and a literal here would go stale silently.
    stamps = pd.DatetimeIndex(fixture_bars.index).normalize()
    expected = int(
        ((stamps >= pd.Timestamp(date(2010, 1, 1))) & (stamps <= pd.Timestamp(WINDOW_END))).sum()
    )
    assert result.bars_used == expected
    assert result.bars_used < len(fixture_bars)
    assert result.window_end == WINDOW_END


def test_a_frame_with_no_bar_in_the_window_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # EVERY call in this file pins `graveyard_path`, including the ones that
    # expect a raise before anything could be written. A test that relies on the
    # guard it is testing to keep it away from the real `state/graveyard.jsonl`
    # scribbles all over it the moment that guard is broken - which is exactly
    # the run where the suite is being deliberately broken to check it goes red.
    protocol = _protocol(window_end=date(2009, 12, 31))
    # The window now ends before the fixture begins, so nothing is admissible.
    protocol = Protocol(
        protocol_version=protocol.protocol_version,
        created=protocol.created,
        instruments=protocol.instruments,
        windows=Windows(
            in_sample=Window(start=date(2009, 1, 1), end=date(2009, 12, 31)),
            walk_forward=Window(start=date(2010, 1, 1), end=date(2011, 12, 31)),
            fold_months=12,
        ),
        costs=protocol.costs,
        validation=protocol.validation,
        walk_forward_touches=0,
    )
    with pytest.raises(SearchError, match="no bar falls inside the in-sample window"):
        run_search(
            _small_strategy(),
            fixture_bars,
            protocol,
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


def test_a_frame_not_indexed_by_timestamp_is_refused(tmp_path: Path) -> None:
    bars = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=["not-a-date"],
    )
    with pytest.raises(SearchError, match="not indexed by timestamp"):
        run_search(
            _small_strategy(),
            bars,
            _protocol(),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


# --------------------------------------------------------------------------
# The primary-metric coupling `plateau.py` asks the loop to close.
# --------------------------------------------------------------------------


def test_a_protocol_naming_a_different_primary_metric_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    with pytest.raises(SearchError, match="primary metric"):
        run_search(
            _small_strategy(),
            fixture_bars,
            _protocol(primary_metric="sortino"),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


# --------------------------------------------------------------------------
# Fingerprints. The property `is_degenerate` depends on and cannot check.
# --------------------------------------------------------------------------


def test_a_cell_that_took_no_trades_gets_the_shared_no_trades_fingerprint() -> None:
    assert trade_fingerprint(()) == NO_TRADES_FINGERPRINT


def test_identical_fills_produce_identical_fingerprints() -> None:
    one = (_fill(Side.BUY, 100.0, 2.0), _fill(Side.SELL, 110.0, 2.0))
    two = (_fill(Side.BUY, 100.0, 2.0), _fill(Side.SELL, 110.0, 2.0))
    assert trade_fingerprint(one) == trade_fingerprint(two)


@pytest.mark.parametrize(
    "other",
    [
        # A different exit price: the same two trades on different days.
        (_fill(Side.BUY, 100.0, 2.0), _fill(Side.SELL, 111.0, 2.0)),
        # A different size: the same days, a different position.
        (_fill(Side.BUY, 100.0, 2.0), _fill(Side.SELL, 110.0, 3.0)),
        # The entry only: a position opened and never closed is not the same as
        # a round trip, because the two end holding different things.
        (_fill(Side.BUY, 100.0, 2.0),),
        # The same fills in the other order.
        (_fill(Side.SELL, 110.0, 2.0), _fill(Side.BUY, 100.0, 2.0)),
    ],
)
def test_different_trades_produce_different_fingerprints(other: tuple[Fill, ...]) -> None:
    baseline = (_fill(Side.BUY, 100.0, 2.0), _fill(Side.SELL, 110.0, 2.0))
    assert trade_fingerprint(baseline) != trade_fingerprint(other)


def test_a_traded_fingerprint_is_never_a_no_trades_or_failed_one() -> None:
    # The three kinds must be mutually exclusive, or a region of failures could
    # compare equal to a region of trades.
    fingerprint = trade_fingerprint((_fill(Side.BUY, 100.0, 2.0),))
    assert fingerprint.startswith(TRADED_PREFIX)
    assert fingerprint != NO_TRADES_FINGERPRINT
    assert not fingerprint.startswith(FAILED_PREFIX)


def test_two_failed_cells_never_share_a_fingerprint() -> None:
    # The case `plateau.py` calls out: giving both the empty string would make a
    # region of failures look like a region of agreement.
    assert failed_fingerprint({"fast": 3, "slow": 10}) != failed_fingerprint(
        {"fast": 3, "slow": 15}
    )


def test_a_failed_fingerprint_does_not_depend_on_key_order() -> None:
    assert failed_fingerprint({"fast": 3, "slow": 10}) == failed_fingerprint(
        {"slow": 10, "fast": 3}
    )


# --------------------------------------------------------------------------
# Cells that failed keep their rows.
# --------------------------------------------------------------------------


def _all_cells_fail_strategy() -> StrategyDef:
    """A strategy whose every cell needs more bars than the window holds.

    The window is roughly 125 bars; every `slow` here exceeds that, so the
    engine refuses each cell for a warm-up it cannot satisfy.
    """
    return _small_strategy(
        {
            "fast": {"min": 3, "max": 7, "step": 2},
            "slow": {"min": 300, "max": 400, "step": 50},
        }
    )


def test_a_cell_whose_backtest_raised_keeps_its_row_with_a_nan_metric(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    result = run_search(
        _all_cells_fail_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )

    # 3 values of `fast` times 3 of `slow` = 9 cells, all of them failing.
    assert result.grid_size == 9
    assert result.failed_cells == 9
    assert len(result.results) == 9
    assert result.results[METRIC_COLUMN].isna().all()
    assert result.winner is None


def test_every_failed_cell_is_recorded_in_the_graveyard(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    graveyard = tmp_path / "graveyard.jsonl"
    run_search(
        _all_cells_fail_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=graveyard,
    )

    records = [json.loads(line) for line in graveyard.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 9
    assert {record["reason"] for record in records} == {"backtest_failed"}
    # A non-finite metric is JSON null, so the file stays parseable by something
    # other than Python.
    assert all(record["metrics"]["sharpe"] is None for record in records)


def test_failed_cells_do_not_all_share_one_fingerprint(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    result = run_search(
        _all_cells_fail_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )
    fingerprints = result.results[FINGERPRINT_COLUMN].tolist()
    assert len(set(fingerprints)) == len(fingerprints)


# --------------------------------------------------------------------------
# select_candidate: plateau over peak, and the two rejection rules.
# --------------------------------------------------------------------------


def _one_axis_frame(sharpes: list[float], drawdowns: list[float] | None = None) -> pd.DataFrame:
    """A complete one-parameter grid, `x` running 1, 2, 3, ... one step apart.

    Every cell gets a distinct fingerprint, so nothing in these frames is
    degenerate and the degeneracy flag cannot confuse a rejection test.
    """
    return pd.DataFrame(
        {
            "x": list(range(1, len(sharpes) + 1)),
            METRIC_COLUMN: sharpes,
            "max_drawdown_pct": drawdowns if drawdowns is not None else [1.0] * len(sharpes),
            "trade_count": [5] * len(sharpes),
            FINGERPRINT_COLUMN: [f"{TRADED_PREFIX}cell{index}" for index in range(len(sharpes))],
        }
    )


def _one_axis_spec(count: int) -> dict[str, dict[str, float]]:
    """The search spec matching `_one_axis_frame`, for the edge check."""
    return {"x": {"min": 1, "max": float(count), "step": 1}}


#: A spike at x=3 and a plateau around x=6. Medians at radius 1, cell included:
#:
#:   x=1  median(0.1, 0.1)            = 0.10
#:   x=2  median(0.1, 0.1, 5.0)       = 0.10
#:   x=3  median(0.1, 5.0, 0.1)       = 0.10
#:   x=4  median(5.0, 0.1, 1.0)       = 1.00
#:   x=5  median(0.1, 1.0, 1.2)       = 1.00
#:   x=6  median(1.0, 1.2, 1.1)       = 1.10   <- joint highest
#:   x=7  median(1.2, 1.1, 0.9)       = 1.10   <- joint highest
#:   x=8  median(1.1, 0.9, 0.1)       = 0.90
#:   x=9  median(0.9, 0.1)            = 0.50
#:
#: The peak by Sharpe is x=3 at 5.0 and it scores 0.10, the worst on the grid.
#: The tie at 1.10 breaks to the lower x, so the winner is x=6.
SPIKE_AND_PLATEAU = [0.1, 0.1, 5.0, 0.1, 1.0, 1.2, 1.1, 0.9, 0.1]


def test_the_winner_is_the_plateau_and_not_the_peak() -> None:
    scored = plateau_score(_one_axis_frame(SPIKE_AND_PLATEAU), radius=1)
    winner, rejections = select_candidate(scored, _one_axis_spec(9), 25.0, radius=1)

    assert winner is not None
    assert winner.cell == {"x": 6}
    assert winner.plateau_score == pytest.approx(1.10)
    # Its own Sharpe is 1.2, well under the spike's 5.0. That is the point.
    assert winner.sharpe == pytest.approx(1.2)
    assert rejections == ()


def test_a_tie_on_plateau_score_breaks_to_the_lower_parameter_value() -> None:
    # x=6 and x=7 both score 1.10. Without a tie-break the winner would depend
    # on row order, and the determinism guarantee would be false.
    scored = plateau_score(_one_axis_frame(SPIKE_AND_PLATEAU), radius=1)
    reversed_rows = scored.iloc[::-1].reset_index(drop=True)
    winner = select_candidate(reversed_rows, _one_axis_spec(9), 25.0, radius=1)[0]
    assert winner is not None
    assert winner.cell == {"x": 6}


def test_an_edge_winner_is_rejected_and_the_search_moves_to_the_next_cell() -> None:
    # Rising to the boundary: x=5 scores median(0.4, 0.5) = 0.45, the highest,
    # and sits on the edge. x=4 scores median(0.3, 0.4, 0.5) = 0.40 and does not.
    scored = plateau_score(_one_axis_frame([0.1, 0.2, 0.3, 0.4, 0.5]), radius=1)
    winner, rejections = select_candidate(scored, _one_axis_spec(5), 25.0, radius=1)

    assert [rejection.cell for rejection in rejections] == [{"x": 5}]
    assert rejections[0].reason == "edge_winner"
    assert winner is not None
    assert winner.cell == {"x": 4}
    assert winner.plateau_score == pytest.approx(0.40)


def test_a_winner_breaching_the_drawdown_ceiling_is_rejected() -> None:
    # Same surface as the plateau test, but x=6 now draws down 30% against a
    # ceiling of 25%. The next-best interior cell is x=7, also at 1.10.
    drawdowns = [1.0] * 9
    drawdowns[5] = 30.0
    scored = plateau_score(_one_axis_frame(SPIKE_AND_PLATEAU, drawdowns), radius=1)
    winner, rejections = select_candidate(scored, _one_axis_spec(9), 25.0, radius=1)

    assert [(r.cell, r.reason) for r in rejections] == [({"x": 6}, "drawdown_ceiling")]
    assert winner is not None
    assert winner.cell == {"x": 7}


def test_a_cell_whose_own_backtest_failed_is_never_nominated() -> None:
    # x=5's own Sharpe is NaN but its neighbours are excellent, so its
    # plateau_score is the highest on the grid. Nominating it would nominate a
    # cell that produced no result at all.
    sharpes = [0.1, 0.1, 0.1, 9.0, math.nan, 9.0, 0.1, 0.1, 0.1]
    scored = plateau_score(_one_axis_frame(sharpes), radius=1)
    assert float(scored.loc[scored["x"] == 5, "plateau_score"].iloc[0]) == pytest.approx(9.0)

    winner = select_candidate(scored, _one_axis_spec(9), 25.0, radius=1)[0]
    assert winner is not None
    assert winner.cell != {"x": 5}


def test_no_surviving_cell_reports_no_winner() -> None:
    # Two cells, both on the edge of a two-value axis, so nothing survives.
    frame = _one_axis_frame([0.5, 0.4])
    scored = plateau_score(frame, radius=1)
    winner, rejections = select_candidate(scored, _one_axis_spec(2), 25.0, radius=1)

    assert winner is None
    assert {rejection.reason for rejection in rejections} == {"edge_winner"}
    assert len(rejections) == 2


def test_a_degenerate_winner_is_flagged_and_not_rejected() -> None:
    # Every cell carries the same fingerprint, so every neighbourhood is
    # unanimous and the winner is degenerate. CLAUDE.md says flagged, not
    # rejected - so a candidate still comes back.
    frame = _one_axis_frame(SPIKE_AND_PLATEAU)
    frame[FINGERPRINT_COLUMN] = NO_TRADES_FINGERPRINT
    scored = plateau_score(frame, radius=1)
    winner, rejections = select_candidate(scored, _one_axis_spec(9), 25.0, radius=1)

    assert winner is not None
    assert winner.degenerate is True
    assert "degenerate_plateau" not in {rejection.reason for rejection in rejections}


def test_every_reason_this_loop_writes_is_in_the_closed_vocabulary() -> None:
    # The graveyard raises on an unknown reason, so a typo would surface only on
    # the run that hit that branch. This checks all of them at once.
    scored = plateau_score(_one_axis_frame([0.1, 0.2, 0.3, 0.4, 0.5], [1, 1, 1, 1, 30.0]), radius=1)
    rejections = select_candidate(scored, _one_axis_spec(5), 25.0, radius=1)[1]
    assert {rejection.reason for rejection in rejections} <= REJECTION_REASONS
    assert "backtest_failed" in REJECTION_REASONS


# --------------------------------------------------------------------------
# Determinism, and the whole loop end to end.
# --------------------------------------------------------------------------


def test_two_runs_over_the_same_inputs_are_identical(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    strategy = _small_strategy()
    protocol = _protocol()
    first = run_search(
        strategy,
        fixture_bars,
        protocol,
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "one.jsonl",
    )
    second = run_search(
        strategy,
        fixture_bars,
        protocol,
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "two.jsonl",
    )

    pd.testing.assert_frame_equal(first.results, second.results)
    assert first.winner == second.winner
    assert first.rejections == second.rejections


def test_the_committed_config_searches_every_one_of_its_ninety_cells(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # The real config against the real fixture. 6 x 5 x 3 = 90 cells, from the
    # closed form in this file's docstring. The window is roughly 125 bars and
    # `trend` reaches 200, so many cells cannot warm up - which is the honest
    # outcome and is exactly why a failed cell must keep its row.
    strategy = load_strategy(REPO_CONFIG)
    result = run_search(
        strategy,
        fixture_bars,
        _protocol(),
        config_path=Path("configs/ma_crossover.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )

    assert result.grid_size == 90
    assert len(result.results) == 90
    assert set(result.results.columns) >= {
        "fast",
        "slow",
        "trend",
        METRIC_COLUMN,
        "max_drawdown_pct",
        "trade_count",
        FINGERPRINT_COLUMN,
        "plateau_score",
        "neighbour_count",
    }
    # No cell is missing, whatever happened to it.
    assert result.results[FINGERPRINT_COLUMN].map(lambda value: isinstance(value, str)).all()


# --------------------------------------------------------------------------
# The artifact.
# --------------------------------------------------------------------------


def _written(result_dir: Path, fixture_bars: pd.DataFrame) -> tuple[Path, dict[str, Any]]:
    """Run the small search and write its artifact, returning the parsed payload."""
    result = run_search(
        _small_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="abc123",
        graveyard_path=result_dir / "graveyard.jsonl",
    )
    path = write_search_result(result, datetime(2026, 8, 22, 13, 5, 0, tzinfo=UTC), result_dir)
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_the_artifact_is_named_so_status_can_count_it(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # `cli.status` globs `state/search-*.json` to report how many searches ran.
    path, _ = _written(tmp_path, fixture_bars)
    assert path.name == "search-20260822T130500Z.json"
    assert list(tmp_path.glob("search-*.json")) == [path]


def test_the_artifact_carries_the_whole_surface_and_its_provenance(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    _, payload = _written(tmp_path, fixture_bars)

    assert payload["schema_version"] == SEARCH_SCHEMA_VERSION
    assert payload["data_content_hash"] == "abc123"
    assert payload["config"] == "configs/small.yaml"
    assert payload["window"] == {
        "start": "2010-01-01",
        "end": WINDOW_END.isoformat(),
        "bars": payload["window"]["bars"],
    }
    # The output of this project is the SHAPE of the surface, so every cell is
    # in the file, not just the winner.
    assert len(payload["cells"]) == payload["grid_size"] == 9


def test_the_artifact_states_that_no_noise_ceiling_was_computed(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # An absent key reads as an oversight; an explicit null reads as a
    # statement. search/noise.py is a stub and the file must say so.
    _, payload = _written(tmp_path, fixture_bars)
    assert "noise_ceiling" in payload
    assert payload["noise_ceiling"] is None
    assert "stub" in payload["noise_ceiling_note"]


def test_the_artifact_is_strict_json_even_when_a_cell_failed(tmp_path: Path) -> None:
    # `json.loads` accepts the bare token NaN, so parsing with Python would not
    # catch this. Rejecting it is what a strict reader does.
    bars = pd.read_parquet(FIXTURE_BARS)
    result = run_search(
        _all_cells_fail_strategy(),
        bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )
    path = write_search_result(result, datetime(2026, 8, 22, 13, 5, 0, tzinfo=UTC), tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "NaN" not in text
    assert json.loads(text, parse_constant=_refuse_constant)["cells"][0]["sharpe"] is None


def _refuse_constant(token: str) -> float:
    """Raise on `NaN`, `Infinity` and `-Infinity`, which are not JSON."""
    raise AssertionError(f"the artifact contains the non-JSON token {token!r}")


def test_a_naive_generated_at_is_refused(fixture_bars: pd.DataFrame, tmp_path: Path) -> None:
    result = run_search(
        _small_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )
    with pytest.raises(ValueError, match="timezone-naive"):
        write_search_result(result, datetime(2026, 8, 22, 13, 5, 0), tmp_path)


def test_the_only_cell_that_can_win_a_three_by_three_grid_is_its_centre(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # Derived, not observed. `is_edge_winner` refuses any cell carrying the
    # lowest or highest value of any axis, and on a three-value axis that is
    # every value but the middle one. So of the nine cells here, eight are
    # refusable on the edge rule alone and only {fast: 5, slow: 15} can ever
    # win. Whether it does depends on one further fact, which the same file
    # supplies: its drawdown must clear the recorded ceiling.
    _, payload = _written(tmp_path, fixture_bars)
    centre = {"fast": 5, "slow": 15}
    cells = {tuple(sorted(cell["params"].items())): cell for cell in payload["cells"]}
    centre_cell = cells[tuple(sorted(centre.items()))]

    assert centre_cell["max_drawdown_pct"] <= payload["max_drawdown_ceiling_pct"]
    assert payload["winner"] is not None
    assert payload["winner"]["params"] == centre
    assert isinstance(payload["winner"]["degenerate"], bool)
    # Every other cell was refused, and the file says why.
    assert {rejection["reason"] for rejection in payload["rejections"]} == {"edge_winner"}


def test_the_candidate_type_carries_the_score_it_was_chosen_on() -> None:
    # Guards against a future edit that ranks on `sharpe` and reports
    # `plateau_score`, or the reverse - the two fields must stay distinct.
    candidate = Candidate(
        cell={"x": 1},
        sharpe=5.0,
        max_drawdown_pct=1.0,
        plateau_score=0.1,
        neighbour_count=2,
        degenerate=False,
    )
    assert candidate.sharpe != candidate.plateau_score

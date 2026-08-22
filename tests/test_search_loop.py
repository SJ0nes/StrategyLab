"""Tests for the search loop - the piece that joins the grid, the engine and the scoring.

EVERY EXPECTED NUMBER IN THIS FILE IS DERIVED FROM A RULE STATED BESIDE IT, or
recomputed from the fixture inside the test. Nothing here was read off what the
implementation printed.

**The window guard is the test that matters.** `strategylab fetch` caches
2005-2024 in one parquet file and nothing partitions it, so the in-sample /
walk-forward split - this project's central methodological claim - is enforced
by `_in_sample_window` and by nothing else.
`test_bars_outside_the_window_cannot_change_any_cell` corrupts every bar after
the window's end, passes the frame in INTACT, and asserts both that the bar
COUNT is the in-window count and that every cell's result is unchanged.

BOTH HALVES ARE LOAD-BEARING, and an audit is why. The corruption half alone
cannot see a one-bar leak on this fixture: a signal from bar `t` fills at
`t+1`'s open, so the window's last bar never produces a fill, and every cell is
flat at the window end - so one leaked bar adds an identical equity point to
both runs and the frames stay bit-identical. Measured: extending the slice by
one row leaves them equal, by two rows turns them red. The bar-count assertion
is what makes the one-bar case fail, and it sits in this test rather than a
neighbouring one so that the test the documents nominate is the test that
carries the claim.

That is NOT the same shape as `tests/test_engine.py`'s lookahead test, which an
earlier version of this docstring claimed it was. The engine's test fails on a
single-bar peek on its own; this one needs its count assertion to do so.

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
    deterministic and its effect on any metric it reaches would be unmissable.

    "Any metric it reaches" is the qualification that matters. A leak of two or
    more bars reaches a FILL and moves the Sharpe of every cell whose window
    touches it. A leak of exactly one bar reaches only a mark-to-market, and on
    this fixture every cell is flat there, so it moves nothing. The caller's
    bar-count assertion is what covers that case.
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
    # nothing is sliced away before the call - so a loop that read past the
    # window's end has the corrupted bars in front of it.
    #
    # THIS TEST CATCHES A ONE-BAR LEAK BY COUNT AND A LARGER ONE BY VALUE, and
    # the two halves are not interchangeable. An audit found the earlier version
    # of this test, which asserted only the frames, silently blind to the
    # smallest and most plausible off-by-one. The mechanism: a signal from bar t
    # fills at t+1's open, so the LAST bar in the window never produces a fill,
    # and every cell here is flat at 2010-06-30 - so one leaked bar contributes
    # no fill and no changed mark-to-market. Both runs then add the same equity
    # point and the frames match. Measured, not reasoned: extending the slice by
    # one row leaves the frames bit-identical, by two rows turns them red.
    #
    # So the bar count carries the one-bar case and the corruption carries the
    # rest, and both assertions live in the test the documents nominate rather
    # than one of them living somewhere else.
    protocol = _protocol()
    strategy = _small_strategy()

    stamps = pd.DatetimeIndex(fixture_bars.index).normalize()
    in_window = int(
        ((stamps >= pd.Timestamp(date(2010, 1, 1))) & (stamps <= pd.Timestamp(WINDOW_END))).sum()
    )

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

    # By count: one leaked bar changes this even when it changes nothing else.
    assert clean.bars_used == in_window
    assert corrupted.bars_used == in_window
    # By value: a leak large enough to reach a fill moves the metrics.
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


def _one_bar(index: Any) -> pd.DataFrame:
    """A single well-formed OHLCV row carrying whatever index is handed in."""
    return pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]},
        index=index,
    )


def test_a_frame_not_indexed_by_timestamp_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SearchError, match="not a DatetimeIndex"):
        run_search(
            _small_strategy(),
            _one_bar(["not-a-date"]),
            _protocol(),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


def test_an_integer_index_is_refused_rather_than_read_as_epoch_nanoseconds(
    tmp_path: Path,
) -> None:
    # pandas does not raise on this: it reads the integers as nanoseconds since
    # the epoch, dating the frame to 1970. Coercing first and complaining that
    # no bar falls in the window would report the wrong diagnosis, and against a
    # protocol whose window covered 1970 it would accept the frame outright.
    with pytest.raises(SearchError, match="nanoseconds since the epoch"):
        run_search(
            _small_strategy(),
            _one_bar([0, 1, 2][:1]),
            _protocol(),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


def test_a_timezone_aware_index_is_refused(tmp_path: Path) -> None:
    # The protocol's window is a plain date, so which bars fall inside a
    # tz-aware frame depends on a conversion nobody has chosen. Comparing the
    # two directly raises a bare TypeError out of pandas, which is not a failure
    # mode this loop's contract admits.
    aware = pd.DatetimeIndex(["2010-03-01"]).tz_localize("America/New_York")
    with pytest.raises(SearchError, match="timezone-aware"):
        run_search(
            _small_strategy(),
            _one_bar(aware),
            _protocol(),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


# --------------------------------------------------------------------------
# A bad input must not masquerade as ninety failed cells.
#
# `run_backtest` raises ValueError both for things that are properties of a
# CELL (a warm-up longer than the window) and for things that are properties of
# the INPUTS (an unsorted frame, a missing column, a bad starting equity). The
# loop catches ValueError per cell and records a failure, so without a check up
# front the second kind would return an ordinary result reporting "90 of 90
# cells failed" - which this module's own contract calls a legitimate finding -
# and would append 90 permanent records to an append-only graveyard.
#
# Each test below therefore asserts BOTH halves: that a SearchError is raised,
# and that the graveyard was never touched.
# --------------------------------------------------------------------------


def _bad_input_is_refused(bars: pd.DataFrame, tmp_path: Path, match: str, **kwargs: Any) -> None:
    """Assert the loop refuses these inputs outright and writes no graveyard."""
    graveyard = tmp_path / "graveyard.jsonl"
    with pytest.raises(SearchError, match=match):
        run_search(
            kwargs.pop("definition", _small_strategy()),
            bars,
            kwargs.pop("protocol", _protocol()),
            config_path=kwargs.pop("config_path", Path("configs/small.yaml")),
            data_content_hash="x",
            graveyard_path=graveyard,
            **kwargs,
        )
    assert not graveyard.exists(), "a refused search must leave no graveyard record"


def test_an_unsorted_frame_is_refused_not_reported_as_every_cell_failing(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    _bad_input_is_refused(fixture_bars.iloc[::-1], tmp_path, "not sorted ascending")


def test_a_frame_with_duplicate_timestamps_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    _bad_input_is_refused(
        pd.concat([fixture_bars, fixture_bars]).sort_index(), tmp_path, "duplicate timestamps"
    )


def test_a_frame_missing_a_required_column_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    _bad_input_is_refused(fixture_bars.drop(columns=["volume"]), tmp_path, "required column")


@pytest.mark.parametrize("equity", [0.0, -1.0, float("inf"), float("nan")])
def test_a_starting_equity_that_is_not_positive_and_finite_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path, equity: float
) -> None:
    _bad_input_is_refused(fixture_bars, tmp_path, "positive and finite", starting_equity=equity)


def test_a_strategy_with_no_searched_parameters_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # `expand_grid({})` returns one empty combination by design, which would
    # reach `plateau_score` as a frame with no parameter axis and raise a bare
    # ValueError about parameter columns - a confusing diagnosis for a config
    # that parsed perfectly well.
    _bad_input_is_refused(
        fixture_bars,
        tmp_path,
        "no searched parameters",
        definition=_small_strategy(search={}),
    )


def test_a_parameter_named_like_a_result_column_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # `{**cell, "sharpe": ...}` would let the metric overwrite the parameter, so
    # the frame would lose an axis and the failure would surface downstream as a
    # complaint about duplicate combinations.
    # The definition must actually SUPPLY every placeholder it references, or
    # the unsupplied-placeholder check fires first and this test would pass for
    # the wrong reason. So the offending parameter is one the conditions use.
    colliding = StrategyDef(
        name="collides",
        version=1,
        description="A strategy whose searched parameter is named like a metric column.",
        entry=(
            Condition(
                IndicatorRef("sma", period_param="sharpe"),
                "crosses_above",
                IndicatorRef("sma", period_param="slow"),
            ),
        ),
        exit=(
            Condition(
                IndicatorRef("sma", period_param="sharpe"),
                "crosses_below",
                IndicatorRef("sma", period_param="slow"),
            ),
        ),
        search={
            "sharpe": {"min": 3, "max": 7, "step": 2},
            "slow": {"min": 10, "max": 20, "step": 5},
        },
        fixed={"position_size_pct": 95.0},
    )
    _bad_input_is_refused(
        fixture_bars, tmp_path, "collide with the result columns", definition=colliding
    )


def _definition(**overrides: Any) -> StrategyDef:
    """A valid small definition with one field replaced, for the guard tests."""
    base = _small_strategy()
    fields = {
        "name": base.name,
        "version": base.version,
        "description": base.description,
        "entry": base.entry,
        "exit": base.exit,
        "search": base.search,
        "fixed": base.fixed,
    }
    fields.update(overrides)
    return StrategyDef(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("side", ["entry", "exit"])
def test_an_empty_condition_list_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path, side: str
) -> None:
    # `evaluate` refuses it, so every cell would fail identically. The config
    # loader refuses it too - but `run_search` takes a StrategyDef, not a path,
    # and a definition built in code never passes the loader.
    _bad_input_is_refused(
        fixture_bars,
        tmp_path,
        f"empty '{side}' condition list",
        definition=_definition(**{side: ()}),
    )


def test_an_indicator_the_engine_does_not_implement_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    entry = (
        Condition(
            IndicatorRef("ema", period_param="fast"),
            "crosses_above",
            IndicatorRef("sma", period_param="slow"),
        ),
    )
    _bad_input_is_refused(
        fixture_bars, tmp_path, "not in the vocabulary", definition=_definition(entry=entry)
    )


def test_an_operator_the_engine_does_not_implement_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    entry = (
        Condition(
            IndicatorRef("sma", period_param="fast"),
            "crosses_sideways",
            IndicatorRef("sma", period_param="slow"),
        ),
    )
    _bad_input_is_refused(
        fixture_bars, tmp_path, "not in the vocabulary", definition=_definition(entry=entry)
    )


def test_a_placeholder_nothing_supplies_is_refused(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # `$trend` is referenced but appears in neither `search` nor `fixed`, so
    # `evaluate` would raise "params omits 'trend'" on every single cell.
    entry = (
        *_small_strategy().entry,
        Condition(IndicatorRef("close"), "above", IndicatorRef("sma", period_param="trend")),
    )
    _bad_input_is_refused(
        fixture_bars,
        tmp_path,
        r"placeholder\(s\) \['trend'\]",
        definition=_definition(entry=entry),
    )


def test_an_absolute_config_path_is_refused(fixture_bars: pd.DataFrame, tmp_path: Path) -> None:
    # It is recorded verbatim in the artifact, and an absolute path carries the
    # username of the machine it ran on into a file meant to be read by others.
    _bad_input_is_refused(
        fixture_bars,
        tmp_path,
        "absolute",
        config_path=(tmp_path / "small.yaml"),
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

    The window is 124 bars; every `slow` here exceeds that, so the engine
    refuses each cell for a warm-up it cannot satisfy.
    """
    return _small_strategy(
        {
            "fast": {"min": 3, "max": 7, "step": 2},
            "slow": {"min": 300, "max": 400, "step": 50},
        }
    )


def _mixed_failure_strategy() -> StrategyDef:
    """A strategy where some cells backtest and some cannot warm up.

    The warm-up is `slow + 1` bars against a 124-bar window, so `slow` of 100
    and 120 need 101 and 121 bars and succeed, while 140 needs 141 and fails.
    Three values of `fast` times three of `slow` is nine cells, of which the
    three carrying `slow = 140` fail - which is the case that matters, because
    a grid with SOME failures is the one that must still produce a surface.
    """
    return _small_strategy(
        {
            "fast": {"min": 3, "max": 7, "step": 2},
            "slow": {"min": 100, "max": 140, "step": 20},
        }
    )


#: Cells of `_mixed_failure_strategy` that cannot warm up: one per `fast` value,
#: all three carrying `slow = 140`.
MIXED_FAILURES = 3
MIXED_CELLS = 9


def test_a_grid_in_which_every_cell_fails_is_an_error_not_a_finding(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # An earlier version returned an ordinary result reporting "9 of 9 failed"
    # and called that a legitimate finding about the window being too short. It
    # cannot be: `run_backtest` raises the same ValueError for faults in the
    # DEFINITION or the DATA - an empty condition list, a placeholder nothing
    # supplies, a non-finite bar - and each of those makes every cell fail
    # identically. A malformed argument was therefore indistinguishable from a
    # research outcome, and left one permanent graveyard record per cell.
    graveyard = tmp_path / "graveyard.jsonl"
    with pytest.raises(SearchError, match="every one of the 9 cells failed"):
        run_search(
            _all_cells_fail_strategy(),
            fixture_bars,
            _protocol(),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=graveyard,
        )
    assert not graveyard.exists(), "a search that produced no surface must record nothing"


def test_the_error_carries_the_first_failure_so_the_cause_is_visible(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    # "Everything failed" on its own is not a diagnosis. The underlying message
    # is what tells the operator whether the window is short or the strategy is
    # malformed, so it is carried out rather than swallowed.
    with pytest.raises(SearchError, match="warm-up needs"):
        run_search(
            _all_cells_fail_strategy(),
            fixture_bars,
            _protocol(),
            config_path=Path("configs/small.yaml"),
            data_content_hash="x",
            graveyard_path=tmp_path / "graveyard.jsonl",
        )


def test_a_cell_whose_backtest_raised_keeps_its_row_with_a_nan_metric(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    result = run_search(
        _mixed_failure_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )

    # Every cell keeps its row. A dropped row is a hole in the GRID, which makes
    # cells two steps apart look adjacent to the neighbourhood walk.
    assert result.grid_size == MIXED_CELLS
    assert len(result.results) == MIXED_CELLS
    assert result.failed_cells == MIXED_FAILURES
    assert int(result.results[METRIC_COLUMN].isna().sum()) == MIXED_FAILURES
    # The failures are exactly the cells whose warm-up exceeds the window.
    failed_rows = result.results[result.results[METRIC_COLUMN].isna()]
    assert set(failed_rows["slow"]) == {140}


def test_every_failed_cell_is_recorded_in_the_graveyard(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    graveyard = tmp_path / "graveyard.jsonl"
    run_search(
        _mixed_failure_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=graveyard,
    )

    records = [json.loads(line) for line in graveyard.read_text(encoding="utf-8").splitlines()]
    failures = [record for record in records if record["reason"] == "backtest_failed"]
    assert len(failures) == MIXED_FAILURES
    # A non-finite metric is JSON null, so the file stays parseable by something
    # other than Python.
    assert all(record["metrics"]["sharpe"] is None for record in failures)
    # Selection rejections land in the same file, under their own reasons.
    assert {record["reason"] for record in records} <= REJECTION_REASONS


def test_failed_cells_do_not_all_share_one_fingerprint(
    fixture_bars: pd.DataFrame, tmp_path: Path
) -> None:
    result = run_search(
        _mixed_failure_strategy(),
        fixture_bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )
    failed = result.results[result.results[METRIC_COLUMN].isna()]
    fingerprints = failed[FINGERPRINT_COLUMN].tolist()
    assert len(fingerprints) == MIXED_FAILURES
    assert len(set(fingerprints)) == MIXED_FAILURES


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
    #   x=4  median(0.1, 9.0, NaN excluded)  = 4.55
    #   x=5  median(9.0, NaN excluded, 9.0)  = 9.00   <- highest, and unusable
    #   x=6  median(9.0, NaN excluded, 0.1)  = 4.55
    # So x=5 outranks everything and must be skipped; x=4 and x=6 then tie at
    # 4.55, and the lower-value tie-break settles it on x=4. Asserting the
    # winner IS x=4, rather than merely that it is not x=5, is what makes this
    # test fail for the right reason.
    sharpes = [0.1, 0.1, 0.1, 9.0, math.nan, 9.0, 0.1, 0.1, 0.1]
    scored = plateau_score(_one_axis_frame(sharpes), radius=1)
    assert float(scored.loc[scored["x"] == 5, "plateau_score"].iloc[0]) == pytest.approx(9.0)

    winner = select_candidate(scored, _one_axis_spec(9), 25.0, radius=1)[0]
    assert winner is not None
    assert winner.cell == {"x": 4}
    assert winner.plateau_score == pytest.approx(4.55)


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
    # x=5 rises to the boundary and scores highest, so it is refused on the edge
    # rule; the drawdown of 30.0 against a 25.0 ceiling never gets consulted for
    # it, because the edge check runs first. x=4 then wins on 0.40.
    scored = plateau_score(_one_axis_frame([0.1, 0.2, 0.3, 0.4, 0.5], [1, 1, 1, 1, 30.0]), radius=1)
    winner, rejections = select_candidate(scored, _one_axis_spec(5), 25.0, radius=1)

    # Asserting the set is non-empty matters: `set() <= REJECTION_REASONS` is
    # vacuously true, so the subset check alone would pass on no rejections.
    reasons = {rejection.reason for rejection in rejections}
    assert reasons == {"edge_winner"}
    assert reasons <= REJECTION_REASONS
    assert winner is not None
    assert winner.cell == {"x": 4}


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
    # The bar count is recomputed from the fixture. An earlier version of this
    # assertion compared payload["window"]["bars"] against itself, which is a
    # tautology dressed as a check.
    stamps = pd.DatetimeIndex(fixture_bars.index).normalize()
    expected_bars = int(
        ((stamps >= pd.Timestamp(date(2010, 1, 1))) & (stamps <= pd.Timestamp(WINDOW_END))).sum()
    )
    assert payload["window"] == {
        "start": "2010-01-01",
        "end": WINDOW_END.isoformat(),
        "bars": expected_bars,
    }
    # The output of this project is the SHAPE of the surface, so every cell is
    # in the file, not just the winner.
    assert len(payload["cells"]) == payload["grid_size"] == 9

    # The costs are recorded, and as FRACTIONS. A surface whose costs a reader
    # cannot see is a surface they cannot check, and costs are the largest
    # determinant of every Sharpe in the file.
    assert payload["costs_frac"] == {
        "spread": TEST_COSTS.spread_frac,
        "slippage": TEST_COSTS.slippage_frac,
        "commission": TEST_COSTS.commission_frac,
        "fx_conversion": TEST_COSTS.fx_conversion_frac,
    }
    # A cash amount, not a rate, which is why it sits outside `costs_frac`.
    assert payload["commission_min_flat"] == TEST_COSTS.commission_min_flat
    assert payload["max_drawdown_ceiling_pct"] == 25.0


def test_the_artifact_schema_version_matches_the_documented_field_set() -> None:
    # The version exists so a reader meeting an unfamiliar artifact can say so.
    # It was bumped to 2 when `costs_frac` and `commission_min_flat` were added:
    # an artifact written by commit db180ce says 1 and lacks both, so leaving it
    # at 1 would have labelled two different shapes identically.
    assert SEARCH_SCHEMA_VERSION == 2


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
        _mixed_failure_strategy(),
        bars,
        _protocol(),
        config_path=Path("configs/small.yaml"),
        data_content_hash="x",
        graveyard_path=tmp_path / "graveyard.jsonl",
    )
    path = write_search_result(result, datetime(2026, 8, 22, 13, 5, 0, tzinfo=UTC), tmp_path)

    text = path.read_text(encoding="utf-8")
    assert "NaN" not in text
    payload = json.loads(text, parse_constant=_refuse_constant)
    nulls = [cell for cell in payload["cells"] if cell["sharpe"] is None]
    assert len(nulls) == MIXED_FAILURES


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


def test_the_winner_reports_the_plateau_score_and_its_own_sharpe_separately() -> None:
    # Deleted from this file: a version of this test that built a Candidate by
    # hand and asserted `candidate.sharpe != candidate.plateau_score`. That
    # compared two literals the test itself supplied and could not fail.
    # The real property is that `select_candidate` fills the two fields from
    # DIFFERENT columns, which needs a frame where they differ.
    scored = plateau_score(_one_axis_frame(SPIKE_AND_PLATEAU), radius=1)
    winner = select_candidate(scored, _one_axis_spec(9), 25.0, radius=1)[0]

    assert winner is not None
    row = scored.loc[scored["x"] == winner.cell["x"]].iloc[0]
    assert winner.sharpe == pytest.approx(float(row[METRIC_COLUMN]))
    assert winner.plateau_score == pytest.approx(float(row["plateau_score"]))
    assert winner.sharpe != pytest.approx(winner.plateau_score)

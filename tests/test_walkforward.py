"""Tests for walk-forward validation against the held-back window.

**No test here touches the repository's own `protocol.yaml`.** Every run that
reads the held-back window is pointed at a temporary copy, and one test asserts
that the repository's counter is unchanged by a run - not that it reads zero,
which would stop being true the moment a real validation is performed. A counter
polluted by test noise could not be read as evidence of anything, and this
repository's whole argument is that its numbers can be.

**What the fold arithmetic tests can and cannot see.** `_fold_bounds` is pure and
its expected dates below are hand-listed from the calendar, including the
committed protocol's own window. Nothing in them was copied from what the
implementation printed.

**What the end-to-end tests can and cannot see.** The pooled Sharpe of a real
strategy over a real window cannot be hand-computed, so nothing here asserts a
literal value for it. What they assert instead is the composition: that the
equity curve covers exactly as many bars as the held-back window holds, that
corrupting bars the validator must not read moves nothing, that the folds' trade
counts sum to the pooled count, and that two runs agree. The metrics themselves
are covered by `tests/test_metrics.py`, and the engine that produces them by
`tests/test_engine.py`.

**The writer's fixture is a verdict the module could actually reach**, and one
test exists purely to keep it that way. A fixture pairing an outcome with a
trade count that could not have produced it teaches a reader a rule the code
does not have, even though nothing fails - the writer does not adjudicate.

**The verdict tests are the ones that carry the pass decision**, and they drive
`_verdict` and `_fold_outcome` directly with hand-picked numbers rather than
through a backtest, because a threshold test that can only be reached by finding
a strategy with the right Sharpe is a threshold test nobody writes.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategylab.backtest.costs import CostModel
from strategylab.backtest.engine import run_backtest
from strategylab.core.types import Condition, IndicatorRef, StrategyDef
from strategylab.protocol import (
    Instruments,
    Protocol,
    Validation,
    Window,
    Windows,
    load_protocol,
)
from strategylab.search.loop import STARTING_EQUITY
from strategylab.search.noise import GATE_PASSED, NoiseGate
from strategylab.validate.candidate import CandidateRef
from strategylab.validate.walkforward import (
    VALIDATION_SCHEMA_VERSION,
    FoldResult,
    Outcome,
    ValidationResult,
    _add_months,
    _completed_exits,
    _fold_bounds,
    _fold_outcome,
    _verdict,
    walk_forward,
    write_validation_result,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_PROTOCOL = REPO_ROOT / "protocol.yaml"

# The recorded fixture spans 2010-01-04 to 2011-01-03. These windows carve three
# pieces out of it: an in-sample stretch that can supply a warm-up, a held-back
# stretch that yields two folds - the second of them carrying a remainder - and a
# tail of bars dated after the held-back window, which no validation may read.
IN_SAMPLE = Window(start=date(2010, 1, 1), end=date(2010, 4, 30))
HELD_BACK = Window(start=date(2010, 5, 1), end=date(2010, 11, 15))
FOLD_MONTHS = 3

PARAMS = {"fast": 3, "slow": 8}
# max lookback 8, plus one bar because both conditions are crossings.
WARMUP = 9

COSTS = CostModel(spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005)


def _validation(
    *,
    min_sharpe: float = 1.0,
    pass_margin: float = 0.2,
    min_trades_per_fold: int = 10,
    min_trades_pooled: int = 30,
    max_drawdown_ceiling_pct: float = 25.0,
) -> Validation:
    """Pass criteria with the committed defaults, overridable one at a time."""
    return Validation(
        primary_metric="sharpe",
        min_sharpe=min_sharpe,
        min_trades_per_fold=min_trades_per_fold,
        min_trades_pooled=min_trades_pooled,
        max_drawdown_ceiling_pct=max_drawdown_ceiling_pct,
        pass_margin=pass_margin,
    )


def _protocol(
    *,
    windows: Windows | None = None,
    validation: Validation | None = None,
    costs: CostModel | None = None,
) -> Protocol:
    """A protocol whose two windows split the recorded fixture in half."""
    return Protocol(
        protocol_version=1,
        created=date(2026, 8, 21),
        instruments=Instruments(research="SPY", execution="VUAG.L"),
        windows=windows
        or Windows(in_sample=IN_SAMPLE, walk_forward=HELD_BACK, fold_months=FOLD_MONTHS),
        costs=costs or COSTS,
        validation=validation or _validation(),
        walk_forward_touches=0,
    )


def _definition() -> StrategyDef:
    """A two-average crossover with both periods left as placeholders."""
    fast = IndicatorRef(indicator="sma", period_param="fast")
    slow = IndicatorRef(indicator="sma", period_param="slow")
    return StrategyDef(
        name="crossover",
        version=1,
        description="Enter when the fast average crosses above the slow, exit on the reverse.",
        entry=(Condition(left=fast, operator="crosses_above", right=slow),),
        exit=(Condition(left=fast, operator="crosses_below", right=slow),),
        search={
            "fast": {"min": 3, "max": 5, "step": 1},
            "slow": {"min": 8, "max": 10, "step": 1},
        },
        fixed={"position_size_pct": 95.0},
    )


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    """The recorded SPY bars, spanning both windows. No network call."""
    return pd.read_parquet(Path(__file__).resolve().parent / "fixtures" / "SPY.parquet")


@pytest.fixture
def protocol_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the real protocol, safe to increment."""
    path = tmp_path / "protocol.yaml"
    path.write_bytes(REPO_PROTOCOL.read_bytes())
    return path


def _held_back(bars: pd.DataFrame) -> pd.DataFrame:
    """The fixture's held-back bars, selected by date without using the module."""
    stamps = pd.DatetimeIndex(bars.index).normalize()
    inside = (stamps >= pd.Timestamp(HELD_BACK.start)) & (stamps <= pd.Timestamp(HELD_BACK.end))
    selected: pd.DataFrame = bars.loc[inside]
    return selected


# --------------------------------------------------------------------------
# Fold arithmetic. Pure, and every expected date is read off a calendar.
# --------------------------------------------------------------------------


def test_the_committed_window_splits_into_six_twelve_month_folds() -> None:
    # The protocol's own comment says this window yields six folds. Hand-listed.
    bounds = _fold_bounds(Window(start=date(2019, 1, 1), end=date(2024, 12, 31)), 12)

    assert bounds == (
        (date(2019, 1, 1), date(2019, 12, 31)),
        (date(2020, 1, 1), date(2020, 12, 31)),
        (date(2021, 1, 1), date(2021, 12, 31)),
        (date(2022, 1, 1), date(2022, 12, 31)),
        (date(2023, 1, 1), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
    )


def test_a_fold_length_is_not_a_fold_count() -> None:
    # Twelve months over six years is six folds, not twelve.
    assert len(_fold_bounds(Window(start=date(2019, 1, 1), end=date(2024, 12, 31)), 12)) == 6
    assert len(_fold_bounds(Window(start=date(2019, 1, 1), end=date(2024, 12, 31)), 6)) == 12


def test_a_trailing_remainder_is_absorbed_into_the_final_fold() -> None:
    # Six and a half years of twelve-month folds: six folds, the last eighteen
    # months long, and no seventh short one.
    bounds = _fold_bounds(Window(start=date(2019, 1, 1), end=date(2025, 6, 30)), 12)

    assert len(bounds) == 6
    assert bounds[-1] == (date(2024, 1, 1), date(2025, 6, 30))


def test_the_folds_cover_the_window_exactly_and_never_overlap() -> None:
    window = Window(start=date(2019, 1, 1), end=date(2025, 6, 30))

    bounds = _fold_bounds(window, 5)

    assert bounds[0][0] == window.start
    assert bounds[-1][1] == window.end
    for earlier, later in zip(bounds[:-1], bounds[1:], strict=True):
        assert earlier[1] < later[0], "folds overlap"
        assert (later[0] - earlier[1]).days == 1, "a day falls between two folds"


def test_a_window_shorter_than_one_fold_is_refused() -> None:
    # Eleven months against a twelve-month fold. Reporting a verdict on a period
    # the protocol never asked for is worse than refusing.
    with pytest.raises(ValueError, match="shorter than one fold"):
        _fold_bounds(Window(start=date(2019, 1, 1), end=date(2019, 11, 30)), 12)


def test_a_fold_length_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        _fold_bounds(Window(start=date(2019, 1, 1), end=date(2024, 12, 31)), 0)


def test_adding_months_clamps_to_the_target_months_length() -> None:
    assert _add_months(date(2019, 1, 31), 1) == date(2019, 2, 28)
    assert _add_months(date(2019, 1, 31), 13) == date(2020, 2, 29)
    assert _add_months(date(2019, 12, 1), 1) == date(2020, 1, 1)
    assert _add_months(date(2019, 3, 15), 0) == date(2019, 3, 15)


def test_a_clamped_month_does_not_drag_the_later_folds_with_it() -> None:
    # Every boundary is computed from the window's start, so February's clamp
    # must not shorten March. A running "previous end plus one month" would give
    # 03-28 here instead of 03-31.
    bounds = _fold_bounds(Window(start=date(2019, 1, 31), end=date(2019, 6, 30)), 1)

    assert [start for start, _ in bounds[:3]] == [
        date(2019, 1, 31),
        date(2019, 2, 28),
        date(2019, 3, 31),
    ]


# --------------------------------------------------------------------------
# The pass decision. Hand-picked numbers, driven straight at the criteria.
# --------------------------------------------------------------------------


def test_a_marginal_pass_is_a_fail() -> None:
    # Sharpe exactly at the 1.0 floor, and the bar is 1.0 + 0.20.
    outcome, reason = _verdict(1.0, 10.0, 50, _validation())

    assert outcome is Outcome.FAIL
    assert "below the bar of 1.20" in reason


def test_sharpe_exactly_at_the_effective_bar_passes() -> None:
    # The comparison is `< bar`, so a Sharpe exactly AT the bar clears it.
    #
    # The bar is taken from the criteria rather than written as the literal 1.2.
    # `1.0 + 0.2 == 1.2` is True in IEEE-754, so a literal happens to work for
    # THESE operands - but `1.1 + 0.1 == 1.2` is False, so a protocol carrying
    # min_sharpe 1.1 and pass_margin 0.1 would flip this test with no bug
    # present. The test must pin the boundary, not one machine representation.
    criteria = _validation()

    outcome, reason = _verdict(criteria.effective_sharpe_bar, 10.0, 50, criteria)

    assert outcome is Outcome.PASS
    assert "clears the bar of 1.20" in reason


def test_the_margin_is_additive_not_multiplicative() -> None:
    # A multiplicative reading of a 0.20 margin would put the bar at 1.20 too,
    # so the discriminating case is a floor that is not 1.0: 2.0 + 0.20 = 2.20
    # additive, 2.40 multiplicative. 2.30 passes one and fails the other.
    outcome, _ = _verdict(2.3, 10.0, 50, _validation(min_sharpe=2.0))

    assert outcome is Outcome.PASS


def test_a_drawdown_breach_fails_however_good_the_sharpe() -> None:
    outcome, reason = _verdict(3.0, 25.01, 50, _validation())

    assert outcome is Outcome.FAIL
    assert "breaches the ceiling of 25.00%" in reason


def test_a_drawdown_exactly_at_the_ceiling_is_not_a_breach() -> None:
    outcome, _ = _verdict(1.5, 25.0, 50, _validation())

    assert outcome is Outcome.PASS


def test_both_missed_criteria_are_named() -> None:
    outcome, reason = _verdict(0.4, 40.0, 50, _validation())

    assert outcome is Outcome.FAIL
    assert "drawdown" in reason
    assert "Sharpe" in reason


def test_the_trade_floor_is_tested_before_the_finiteness_guard() -> None:
    # Both would give INSUFFICIENT_DATA, so the outcome cannot tell them apart -
    # the REASON is what says which criterion bound, and it is the reason an
    # operator reads. Pinned because two fixtures have now carried a reason
    # string nobody checked.
    _, reason = _verdict(float("nan"), float("nan"), 0, _validation())

    assert "below the floor of 30" in reason
    assert "not a finite number" not in reason


def test_too_few_pooled_trades_is_insufficient_data_not_a_fail() -> None:
    # 29 against a floor of 30, with a Sharpe that would otherwise pass easily.
    # Calling this a FAIL would discard a candidate nothing has refuted.
    outcome, reason = _verdict(3.0, 5.0, 29, _validation())

    assert outcome is Outcome.INSUFFICIENT_DATA
    assert "not tested, not refuted" in reason


def test_the_pooled_trade_floor_is_reached_at_its_own_value() -> None:
    outcome, _ = _verdict(3.0, 5.0, 30, _validation())

    assert outcome is Outcome.PASS


def test_a_non_finite_pooled_sharpe_is_never_a_pass() -> None:
    # `nan < 1.20` is False, so a bare threshold test would pass this.
    outcome, reason = _verdict(float("nan"), 5.0, 50, _validation())

    assert outcome is Outcome.INSUFFICIENT_DATA
    assert "not a finite number" in reason


def test_a_fold_uses_the_per_fold_trade_floor_not_the_pooled_one() -> None:
    criteria = _validation(min_trades_per_fold=10, min_trades_pooled=30)

    # 12 trades clears the per-fold floor of 10 while sitting below the pooled
    # floor of 30. A fold judged against the pooled floor would call this
    # insufficient.
    assert _fold_outcome(1.5, 5.0, 12, criteria) is Outcome.PASS
    assert _fold_outcome(1.5, 5.0, 9, criteria) is Outcome.INSUFFICIENT_DATA
    # Exactly at the floor, so `<` and `<=` are told apart. The pooled floor is
    # pinned at its own exact value in the test above; this is the same check
    # for the other threshold.
    assert _fold_outcome(1.5, 5.0, 10, criteria) is Outcome.PASS


def test_a_fold_with_an_unmeasurable_sharpe_is_insufficient_not_passed() -> None:
    assert _fold_outcome(float("nan"), 5.0, 50, _validation()) is Outcome.INSUFFICIENT_DATA


def test_a_fold_fails_on_the_same_two_criteria_as_the_pooled_decision() -> None:
    criteria = _validation()

    assert _fold_outcome(1.19, 5.0, 50, criteria) is Outcome.FAIL
    assert _fold_outcome(3.0, 25.01, 50, criteria) is Outcome.FAIL
    assert _fold_outcome(1.2, 25.0, 50, criteria) is Outcome.PASS


# --------------------------------------------------------------------------
# The touch. It is charged, it cannot be avoided, and it is charged once.
# --------------------------------------------------------------------------


def test_one_run_costs_exactly_one_touch(bars: pd.DataFrame, protocol_copy: Path) -> None:
    result = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)

    assert load_protocol(protocol_copy).walk_forward_touches == 1
    assert result.walk_forward_touches == 1


def test_a_second_run_costs_a_second_touch(bars: pd.DataFrame, protocol_copy: Path) -> None:
    # The split degrades with use, and the counter is what makes that visible.
    for expected in (1, 2, 3):
        result = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)
        assert result.walk_forward_touches == expected

    assert load_protocol(protocol_copy).walk_forward_touches == 3


def test_an_insufficient_data_run_still_costs_a_touch(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    # A 400-bar average against 253 fixture bars: nothing can be evaluated, and
    # the window has still been looked at. Looking is the cost, not concluding.
    result = walk_forward(
        _definition(), {"fast": 3, "slow": 400}, bars, _protocol(), protocol_path=protocol_copy
    )

    assert result.outcome is Outcome.INSUFFICIENT_DATA
    assert result.folds == ()
    assert result.bars_used == 0
    assert load_protocol(protocol_copy).walk_forward_touches == 1


def test_a_refusal_before_the_read_costs_nothing(bars: pd.DataFrame, protocol_copy: Path) -> None:
    # `slow` is referenced by the definition and absent from params, which is
    # knowable without reading a single held-back bar.
    with pytest.raises(ValueError, match="params omits 'slow'"):
        walk_forward(_definition(), {"fast": 3}, bars, _protocol(), protocol_path=protocol_copy)

    assert load_protocol(protocol_copy).walk_forward_touches == 0


def test_a_window_shorter_than_a_fold_costs_nothing(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    protocol = _protocol(
        windows=Windows(in_sample=IN_SAMPLE, walk_forward=HELD_BACK, fold_months=12)
    )

    with pytest.raises(ValueError, match="shorter than one fold"):
        walk_forward(_definition(), PARAMS, bars, protocol, protocol_path=protocol_copy)

    assert load_protocol(protocol_copy).walk_forward_touches == 0


def test_an_in_sample_window_after_the_held_back_one_is_refused(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    # Legal per the protocol's non-overlap rule, and still wrong here: the
    # warm-up would be drawn from bars dated after the period being judged.
    protocol = _protocol(
        windows=Windows(
            in_sample=Window(start=date(2010, 12, 1), end=date(2010, 12, 31)),
            walk_forward=HELD_BACK,
            fold_months=FOLD_MONTHS,
        )
    )

    with pytest.raises(ValueError, match="not before the held-back window"):
        walk_forward(_definition(), PARAMS, bars, protocol, protocol_path=protocol_copy)

    assert load_protocol(protocol_copy).walk_forward_touches == 0


@pytest.mark.parametrize(
    "criteria",
    [
        _validation(max_drawdown_ceiling_pct=float("inf")),
        _validation(min_sharpe=float("inf")),
        _validation(pass_margin=float("nan")),
    ],
    ids=["infinite ceiling", "infinite floor", "nan margin"],
)
def test_a_non_finite_threshold_is_refused_before_any_touch(
    bars: pd.DataFrame, protocol_copy: Path, criteria: Validation
) -> None:
    # `load_protocol` checks max_drawdown_ceiling_pct only for being positive
    # and min_sharpe not at all, so `.inf` loads. The drawdown test is then
    # `drawdown > inf`, which never binds - the ceiling silently stops applying
    # and a PASS can be returned on a criterion nobody applied. Refused up
    # front, so that verdict is never computed, never printed, and never
    # written.
    with pytest.raises(ValueError, match="not finite"):
        walk_forward(
            _definition(),
            PARAMS,
            bars,
            _protocol(validation=criteria),
            protocol_path=protocol_copy,
        )

    assert load_protocol(protocol_copy).walk_forward_touches == 0


def test_a_run_leaves_the_repositorys_own_counter_alone(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    # Not that it reads zero - that stops being true the first time a real
    # validation runs - but that a test run never moves it.
    before = load_protocol(REPO_PROTOCOL).walk_forward_touches

    walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)

    assert load_protocol(REPO_PROTOCOL).walk_forward_touches == before


# --------------------------------------------------------------------------
# What the run actually reads.
# --------------------------------------------------------------------------


def test_the_equity_curve_covers_the_held_back_window_and_nothing_else(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    held_back = _held_back(bars)

    result = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)

    # The warm-up is prepended from the in-sample window, so the first bar the
    # engine can evaluate is the window's first bar - not `WARMUP - 1` bars into
    # it, which is what handing over the window alone would give.
    assert result.bars_used == len(held_back)
    assert result.warmup_bars == WARMUP


def test_corrupting_the_future_beyond_the_window_moves_nothing(
    bars: pd.DataFrame, tmp_path: Path
) -> None:
    # The held-back window ends before the fixture does. Everything after it is
    # data no validation is entitled to, and corrupting it must change nothing.
    corrupted = bars.copy()
    after = pd.DatetimeIndex(corrupted.index).normalize() > pd.Timestamp(HELD_BACK.end)
    assert bool(after.any()), "the fixture must extend past the window to test this"
    for column in ("open", "high", "low", "close"):
        corrupted.loc[after, column] = 1.0

    clean_path = tmp_path / "clean.yaml"
    clean_path.write_bytes(REPO_PROTOCOL.read_bytes())
    dirty_path = tmp_path / "dirty.yaml"
    dirty_path.write_bytes(REPO_PROTOCOL.read_bytes())

    clean = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=clean_path)
    dirty = walk_forward(_definition(), PARAMS, corrupted, _protocol(), protocol_path=dirty_path)

    assert clean.folds == dirty.folds
    assert clean.total_trades == dirty.total_trades
    assert clean.pooled_sharpe == dirty.pooled_sharpe
    assert clean.bars_used == dirty.bars_used


def test_corrupting_the_past_beyond_the_warm_up_moves_nothing(
    bars: pd.DataFrame, tmp_path: Path
) -> None:
    # Only `WARMUP - 1` in-sample bars are prepended. Everything earlier is read
    # by nothing, and this fails if the whole in-sample window is handed over.
    held_back = _held_back(bars)
    first_held_back = held_back.index[0]
    before = bars.index < first_held_back
    prefix_start = bars.index[before][-(WARMUP - 1)]

    corrupted = bars.copy()
    stale = corrupted.index < prefix_start
    assert int(stale.sum()) > 0, "the fixture must have bars before the warm-up"
    for column in ("open", "high", "low", "close"):
        corrupted.loc[stale, column] = 1.0

    clean_path = tmp_path / "clean.yaml"
    clean_path.write_bytes(REPO_PROTOCOL.read_bytes())
    dirty_path = tmp_path / "dirty.yaml"
    dirty_path.write_bytes(REPO_PROTOCOL.read_bytes())

    clean = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=clean_path)
    dirty = walk_forward(_definition(), PARAMS, corrupted, _protocol(), protocol_path=dirty_path)

    assert clean.folds == dirty.folds
    assert clean.pooled_sharpe == dirty.pooled_sharpe


def test_the_pooled_figures_are_one_continuous_run_over_the_window(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    # Driven independently: the same warm-up prefix and the same window, handed
    # straight to the engine. A validator that ran a backtest per fold, or that
    # started the curve in the wrong place, disagrees here.
    held_back = _held_back(bars)
    before = bars.loc[bars.index < held_back.index[0]]
    window = pd.concat([before.iloc[-(WARMUP - 1) :], held_back])
    # STARTING_EQUITY rather than a literal 10,000: the comparison is only valid
    # if this matches `walk_forward`'s own default, and that default IS this
    # constant. A literal here would be a number copied out of the
    # implementation and would drift silently if the constant moved.
    expected = run_backtest(_definition(), PARAMS, window, COSTS, STARTING_EQUITY)

    result = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)

    assert result.total_trades == expected.trade_count
    assert result.pooled_sharpe == pytest.approx(expected.sharpe)
    assert result.pooled_max_drawdown_pct == pytest.approx(expected.max_drawdown_pct)
    assert result.bars_used == len(expected.equity)


def test_a_trade_is_counted_once_in_the_fold_that_closed_it() -> None:
    # THE ATTRIBUTION RULE, tested directly rather than hoped for. A position
    # opened in one fold and closed in the next must be counted once, in the
    # fold that closed it. Counted in both, or in neither, and the folds stop
    # summing to the pooled count - but a fixture that happens to contain no
    # boundary-spanning trade would be green either way, so the case is
    # constructed here instead of looked for.
    #
    # `position_units` is the holding AFTER this bar's open was executed, so the
    # round trip completes on the bar where it falls from positive to zero.
    decisions = pd.DataFrame(
        {"position_units": [0.0, 0.0, 5.0, 5.0, 5.0, 0.0, 0.0]},
        index=pd.to_datetime(
            [
                "2010-07-28",
                "2010-07-29",
                "2010-07-30",  # entry executed, still fold 0
                "2010-08-02",  # held across the boundary into fold 1
                "2010-08-03",
                "2010-08-04",  # exit executed here: one completed round trip
                "2010-08-05",
            ]
        ),
    )

    # A numpy bool array, so these subscripts are positional indexing and not
    # pandas' deprecated label-then-position fallback - which would warn today
    # and raise KeyError on pandas 3. Asserted, so the distinction survives a
    # change to the return type rather than being a comment nobody rechecks.
    exits = _completed_exits(decisions)
    assert isinstance(exits, np.ndarray)

    assert exits.sum() == 1, "one round trip, not two and not none"
    assert bool(exits[5]), "the round trip belongs to the bar that closed it"
    assert not bool(exits[2]), "the bar that opened it completes nothing"


def test_the_folds_trade_counts_sum_to_the_pooled_count(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    result = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)

    assert sum(fold.trade_count for fold in result.folds) == result.total_trades
    assert result.total_trades > 0, "the fixture must produce trades to test this"


def test_the_folds_are_chronological_and_match_the_protocols_split(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    result = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=protocol_copy)

    # 2010-05-01 to 2010-11-15 in three-month folds: two whole folds, with the
    # remaining fortnight absorbed into the second rather than made a third.
    assert [(fold.index, fold.start, fold.end) for fold in result.folds] == [
        (0, date(2010, 5, 1), date(2010, 7, 31)),
        (1, date(2010, 8, 1), date(2010, 11, 15)),
    ]


def test_the_threshold_recorded_is_the_effective_bar(
    bars: pd.DataFrame, protocol_copy: Path
) -> None:
    protocol = _protocol(validation=_validation(min_sharpe=1.0, pass_margin=0.2))

    result = walk_forward(_definition(), PARAMS, bars, protocol, protocol_path=protocol_copy)

    assert result.threshold == pytest.approx(1.2)


def test_two_runs_reach_the_same_verdict(bars: pd.DataFrame, tmp_path: Path) -> None:
    first_path = tmp_path / "first.yaml"
    first_path.write_bytes(REPO_PROTOCOL.read_bytes())
    second_path = tmp_path / "second.yaml"
    second_path.write_bytes(REPO_PROTOCOL.read_bytes())

    first = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=first_path)
    second = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=second_path)

    assert first == second


def test_a_lowered_pooled_floor_turns_the_same_run_into_a_verdict(
    bars: pd.DataFrame, tmp_path: Path
) -> None:
    # The fixture's half-year yields too few trades for the committed floor of
    # 30, so the default run is insufficient_data. With a floor this window can
    # meet, the same run reaches a real pass-or-fail decision. This is what the
    # two floors are for: one says "not tested", the other says "tested".
    strict_path = tmp_path / "strict.yaml"
    strict_path.write_bytes(REPO_PROTOCOL.read_bytes())
    lenient_path = tmp_path / "lenient.yaml"
    lenient_path.write_bytes(REPO_PROTOCOL.read_bytes())

    strict = walk_forward(_definition(), PARAMS, bars, _protocol(), protocol_path=strict_path)
    lenient = walk_forward(
        _definition(),
        PARAMS,
        bars,
        _protocol(validation=_validation(min_trades_per_fold=1, min_trades_pooled=1)),
        protocol_path=lenient_path,
    )

    assert strict.outcome is Outcome.INSUFFICIENT_DATA
    assert lenient.outcome in (Outcome.PASS, Outcome.FAIL)
    assert lenient.total_trades == strict.total_trades


# --------------------------------------------------------------------------
# The artifact. A verdict that lives only in a scrollback costs another touch.
# --------------------------------------------------------------------------


def _candidate() -> CandidateRef:
    """A candidate reference standing in for one read from a search artifact."""
    return CandidateRef(
        source=Path("state/search-20260822T213227Z.json"),
        # The name the committed config actually declares, matching
        # tests/test_candidate.py's fixture. The two files assert nothing across
        # each other, but a reader reconciling them should not have to discover
        # that one of them named a strategy no config declares.
        strategy="ma_crossover",
        config_path=Path("configs/ma_crossover.yaml"),
        config_content_hash="c" * 64,
        params=dict(PARAMS),
        data_content_hash="0" * 64,
        protocol_version=1,
        search_sharpe=0.64,
        search_plateau_score=0.59,
        degenerate=False,
        noise_ceiling_note="100 trials supports reading the upper percentiles.",
        noise_gate=NoiseGate(GATE_PASSED, 0.64, 100, 97, 95),
    )


# Six folds over the committed held-back window. EVERY FIGURE IS INVENTED,
# chosen to exercise every branch of `_fold_outcome` - these are not the
# committed candidate's real numbers, which are eight round trips in total
# across all six years, not 52. What they are is internally consistent with the
# rules the module applies:
#   2019  9 trades  - below min_trades_per_fold (10), so insufficient_data
#   2020  12 trades - measurable, -0.411 below the 1.20 bar, so fail
#   2021  0 trades  - a fold spent holding a position opened in 2020, so
#                     insufficient_data despite a finite Sharpe
#   2022  11 trades - measurable, -2.032 below the bar, so fail
#   2023  10 trades - exactly at the fold floor, 1.507 clears the bar, so pass
#   2024  10 trades - as 2023
# Sum: 9 + 12 + 0 + 11 + 10 + 10 = 52 round trips, which clears the pooled floor
# of 30, so the pooled verdict reaches the Sharpe comparison rather than
# stopping at insufficient_data. Pooled Sharpe 0.5 is below 1.20 and pooled
# drawdown 18.40% is inside the 25% ceiling, so the verdict is FAIL on the
# Sharpe alone - which is what `reason` says.
#
# POOLED DRAWDOWN MUST DOMINATE EVERY FOLD'S, and 18.40 is above 2022's 16.67
# for that reason rather than by taste. The folds are date slices of ONE run, so
# a fold's running peak is taken over a subset of the bars the pooled running
# peak is taken over; a larger peak against the same trough gives a larger
# decline; so the pooled figure is at least the maximum fold figure, always.
#
# THE COHERENCE IS THE POINT, and it has now been got wrong twice. The first
# version of this fixture paired outcome=FAIL with 3 pooled trades against a
# floor of 30. The version that fixed THAT set a pooled drawdown of 12.5%
# beneath a fold's 16.67%. Neither could fail a test, because the writer does
# not adjudicate - which is exactly why the guard below asserts the invariants
# rather than trusting the comment above them.
_FOLDS = (
    (2019, 0.812, 4.50, 9, Outcome.INSUFFICIENT_DATA),
    (2020, -0.411, 11.89, 12, Outcome.FAIL),
    (2021, 0.886, 3.90, 0, Outcome.INSUFFICIENT_DATA),
    (2022, -2.032, 16.67, 11, Outcome.FAIL),
    (2023, 1.507, 5.78, 10, Outcome.PASS),
    (2024, 1.506, 8.07, 10, Outcome.PASS),
)
# 252 + 253 + 252 + 251 + 250 + 252 NYSE sessions across 2019-2024, derived from
# a calendar. Whether a given price snapshot actually holds all 1,510 is a
# property of that snapshot and is not asserted anywhere.
POOLED_BARS = 1_510
POOLED_TRADES = 52
POOLED_SHARPE = 0.5
POOLED_DRAWDOWN_PCT = 18.40


def _folds() -> tuple[FoldResult, ...]:
    """The six folds above, as the module would report them."""
    return tuple(
        FoldResult(
            index=index,
            start=date(year, 1, 1),
            end=date(year, 12, 31),
            sharpe=fold_sharpe,
            max_drawdown_pct=drawdown,
            trade_count=trades,
            outcome=outcome,
        )
        for index, (year, fold_sharpe, drawdown, trades, outcome) in enumerate(_FOLDS)
    )


def _result() -> ValidationResult:
    """A coherent verdict over the committed window, for the writer's tests.

    Takes no overrides. An earlier version had a ``folds=`` parameter, and the
    one call site that used it rebuilt both fixture defects at once: zero pooled
    trades against a floor of thirty, carrying an outcome of FAIL, beside a
    ``bars_used`` for six years and a single one-year fold. A test needing a
    different verdict builds its own, in full, where a reader can see it.
    """
    folds = _folds()
    return ValidationResult(
        outcome=Outcome.FAIL,
        folds=folds,
        pooled_sharpe=POOLED_SHARPE,
        pooled_max_drawdown_pct=POOLED_DRAWDOWN_PCT,
        total_trades=sum(fold.trade_count for fold in folds),
        bars_used=POOLED_BARS,
        warmup_bars=WARMUP,
        threshold=1.2,
        # The full sentence the module renders, floor and margin included. The
        # first version of this fixture carried a truncated one, and the guard
        # below caught it on its first run - which is the argument for asserting
        # the reason rather than only the outcome.
        reason=("pooled Sharpe 0.500 is below the bar of 1.20 (1.00 floor + 0.20 margin)"),
        walk_forward_touches=1,
    )


def test_the_writers_fixture_is_a_verdict_the_module_could_reach() -> None:
    # Guards the fixture against drifting back into an impossible state, which
    # it has now done twice. Five invariants: the folds sum to the pooled count;
    # that count clears the pooled floor; the pooled OUTCOME AND REASON are the
    # ones the criteria actually render; each fold's outcome is the one the
    # criteria give it; and the pooled drawdown dominates every fold's, because
    # the folds are slices of one continuous run and a fold's running peak is
    # taken over a subset of the pooled run's bars.
    criteria = _validation()
    result = _result()

    assert result.total_trades == POOLED_TRADES
    assert result.total_trades >= criteria.min_trades_pooled

    outcome, reason = _verdict(
        result.pooled_sharpe, result.pooled_max_drawdown_pct, result.total_trades, criteria
    )
    assert outcome is result.outcome
    assert reason == result.reason, "the fixture's reason is not the one the module renders"

    for fold in result.folds:
        assert (
            _fold_outcome(fold.sharpe, fold.max_drawdown_pct, fold.trade_count, criteria)
            is fold.outcome
        )

    measurable = [
        fold.max_drawdown_pct for fold in result.folds if not math.isnan(fold.max_drawdown_pct)
    ]
    assert result.pooled_max_drawdown_pct >= max(measurable), (
        "a fold's running peak is taken over a subset of the pooled run's bars, so the "
        "pooled drawdown can never be the smaller number"
    )


def test_the_artifact_is_named_for_the_moment_it_was_generated(tmp_path: Path) -> None:
    stamp = datetime(2026, 9, 6, 14, 30, 5, tzinfo=UTC)

    path = write_validation_result(_result(), _candidate(), _protocol(), stamp, tmp_path)

    assert path.name == "validation-20260906T143005Z.json"
    # The literal rather than `stamp.isoformat()`: comparing the output against
    # the same call the writer makes pins "some ISO-8601 rendering" and nothing
    # about which one.
    assert (
        json.loads(path.read_text(encoding="utf-8"))["generated_at"] == "2026-09-06T14:30:05+00:00"
    )


@pytest.mark.parametrize(
    ("offset_hours", "hour", "expected_name", "expected_stamp"),
    [
        # BOTH CASES CROSS MIDNIGHT, and that is the point. 14:30 at +05:00
        # converts to 09:30 on the SAME calendar day, so the date in the
        # filename would read identically whether the conversion happened or
        # not - an implementation converting the time and taking the date from
        # the unconverted stamp would pass. These two pin the date as well, and
        # in opposite directions, so the sign of the conversion is fixed too.
        (5, 2, "validation-20260905T213005Z.json", "2026-09-05T21:30:05+00:00"),
        (-5, 22, "validation-20260907T033005Z.json", "2026-09-07T03:30:05+00:00"),
    ],
)
def test_a_non_utc_stamp_is_converted_rather_than_stamped_with_a_lying_z(
    tmp_path: Path,
    offset_hours: int,
    hour: int,
    expected_name: str,
    expected_stamp: str,
) -> None:
    # The filename ends in a literal Z. Accepting an offset and writing it
    # through unchanged would name the file for a UTC time the payload
    # contradicts.
    stamp = datetime(2026, 9, 6, hour, 30, 5, tzinfo=timezone(timedelta(hours=offset_hours)))

    path = write_validation_result(_result(), _candidate(), _protocol(), stamp, tmp_path)

    assert path.name == expected_name
    assert json.loads(path.read_text(encoding="utf-8"))["generated_at"] == expected_stamp


def test_the_artifact_records_the_verdict_and_the_bar_it_was_measured_against(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        write_validation_result(
            _result(), _candidate(), _protocol(), datetime(2026, 9, 6, tzinfo=UTC), tmp_path
        ).read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == VALIDATION_SCHEMA_VERSION
    assert payload["outcome"] == "fail"
    assert payload["criteria"]["effective_sharpe_bar"] == pytest.approx(1.2)
    assert payload["criteria"]["min_sharpe"] == pytest.approx(1.0)
    assert payload["criteria"]["pass_margin"] == pytest.approx(0.2)
    assert payload["walk_forward_touches"] == 1
    assert payload["pooled"]["trades"] == POOLED_TRADES
    assert payload["source_search"] == "state/search-20260822T213227Z.json"
    assert payload["params"] == PARAMS


def test_the_artifact_names_what_its_config_said_and_not_only_where_it_was(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        write_validation_result(
            _result(), _candidate(), _protocol(), datetime(2026, 9, 6, tzinfo=UTC), tmp_path
        ).read_text(encoding="utf-8")
    )

    assert payload["config"] == "configs/ma_crossover.yaml"
    assert payload["config_content_hash"] == "c" * 64
    assert payload["config_content_hash"] != payload["data_content_hash"]


def test_the_artifact_carries_every_cost_parameter(tmp_path: Path) -> None:
    # ALL FIVE, and the two zeros are the reason. protocol.yaml spends fifteen
    # lines saying that commission_min_flat and fx_conversion_pct are DECIDED at
    # zero rather than omitted, because "a cost silently at zero is the single
    # most effective way to turn a losing strategy into a winning one". An
    # artifact carrying three of five reintroduces at its own boundary exactly
    # the decided-versus-absent ambiguity that file exists to remove.
    #
    # The four rates are FRACTIONS and live under costs_frac; commission_min_flat
    # is a CASH amount in account currency units and sits outside it, unconverted.
    # This test does not exercise the percent-to-fraction conversion - _protocol()
    # builds a CostModel from fractions directly and never calls load_protocol.
    # tests/test_protocol.py owns that boundary.
    payload = json.loads(
        write_validation_result(
            _result(), _candidate(), _protocol(), datetime(2026, 9, 6, tzinfo=UTC), tmp_path
        ).read_text(encoding="utf-8")
    )

    assert payload["costs_frac"] == {
        "spread": pytest.approx(0.0003),
        "slippage": pytest.approx(0.0005),
        "commission": pytest.approx(0.0005),
        "fx_conversion": pytest.approx(0.0),
    }
    assert payload["commission_min_flat"] == pytest.approx(0.0)
    assert "commission_min_flat" not in payload["costs_frac"], "a cash amount is not a rate"


def test_the_two_costs_decided_at_zero_are_read_rather_than_hardcoded(
    tmp_path: Path,
) -> None:
    # The committed protocol sets fx_conversion and commission_min_flat to zero,
    # so the test above cannot tell a writer that READS them from one that emits
    # a literal 0.0 and ignores the model. These two are the very fields
    # protocol.yaml argues are DECIDED at zero rather than absent, so "the
    # writer looks at them" is the property worth pinning. Distinct non-zero
    # values, so a swap between the two would fail as well.
    costs = CostModel(
        spread_frac=0.0003,
        slippage_frac=0.0005,
        commission_frac=0.0005,
        commission_min_flat=1.25,
        fx_conversion_frac=0.0015,
    )
    protocol = _protocol(costs=costs)

    payload = json.loads(
        write_validation_result(
            _result(), _candidate(), protocol, datetime(2026, 9, 6, tzinfo=UTC), tmp_path
        ).read_text(encoding="utf-8")
    )

    assert payload["costs_frac"]["fx_conversion"] == pytest.approx(0.0015)
    assert payload["commission_min_flat"] == pytest.approx(1.25)


def test_a_non_finite_fold_metric_is_written_as_null_not_zero(tmp_path: Path) -> None:
    # Built in full rather than by overriding the shared fixture's folds, which
    # is how an earlier version of this test rebuilt both defects the guard
    # above exists to catch. Every field here is consistent with the others: one
    # fold holding no bar at all, so no Sharpe and no drawdown; no trades, so
    # the pooled count is zero, which is below the floor of thirty and therefore
    # insufficient_data rather than a fail; and a bars_used to match.
    empty = FoldResult(
        index=0,
        start=date(2019, 1, 1),
        end=date(2019, 12, 31),
        sharpe=float("nan"),
        max_drawdown_pct=float("nan"),
        trade_count=0,
        outcome=Outcome.INSUFFICIENT_DATA,
    )
    # Outcome and reason DERIVED rather than typed out. The guard test above
    # covers `_result()` and caught a truncated reason there; this fixture is
    # the one it does not cover, and it carried the same truncation. Taking both
    # from `_verdict` makes them correct by construction, and it also settles
    # which criterion binds when several could: the pooled trade floor is tested
    # before the finiteness guard, so a run with no trades and no measurable
    # Sharpe reports the floor.
    criteria = _validation()
    outcome, reason = _verdict(float("nan"), float("nan"), 0, criteria)
    assert outcome is Outcome.INSUFFICIENT_DATA

    unmeasurable = ValidationResult(
        outcome=outcome,
        folds=(empty,),
        pooled_sharpe=float("nan"),
        pooled_max_drawdown_pct=float("nan"),
        total_trades=0,
        bars_used=0,
        warmup_bars=WARMUP,
        threshold=criteria.effective_sharpe_bar,
        reason=reason,
        walk_forward_touches=1,
    )

    text = write_validation_result(
        unmeasurable, _candidate(), _protocol(), datetime(2026, 9, 6, tzinfo=UTC), tmp_path
    ).read_text(encoding="utf-8")

    assert "NaN" not in text, "a bare NaN token makes the file unreadable to a strict parser"
    payload = json.loads(text)
    assert payload["folds"][0]["sharpe"] is None
    assert payload["folds"][0]["max_drawdown_pct"] is None
    assert payload["pooled"]["sharpe"] is None
    assert payload["pooled"]["max_drawdown_pct"] is None


def test_the_artifact_repeats_the_searchs_own_noise_caveat(tmp_path: Path) -> None:
    # The search's own sentence about what its ceiling supports is carried into
    # the verdict verbatim, rather than being dropped at the boundary.
    payload = json.loads(
        write_validation_result(
            _result(), _candidate(), _protocol(), datetime(2026, 9, 6, tzinfo=UTC), tmp_path
        ).read_text(encoding="utf-8")
    )

    assert payload["noise_ceiling_note"] == "100 trials supports reading the upper percentiles."
    # And the verdict the gate reached on it, with the counts it rested on.
    assert payload["noise_gate"] == {
        "status": "passed",
        "candidate_sharpe": 0.64,
        "scoreable_trials": 100,
        "beaten": 97,
        "required": 95,
    }


def test_a_naive_timestamp_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-naive"):
        write_validation_result(
            _result(), _candidate(), _protocol(), datetime(2026, 9, 6), tmp_path
        )


def test_the_writer_serialises_a_real_run_faithfully(
    bars: pd.DataFrame, protocol_copy: Path, tmp_path: Path
) -> None:
    # What this shows is that the writer copies the fields it was handed, on a
    # result that came from a real run rather than a fixture. It is NOT evidence
    # that the run was right: every assertion compares the payload against the
    # object the payload was written from.
    protocol = _protocol()
    result = walk_forward(_definition(), PARAMS, bars, protocol, protocol_path=protocol_copy)

    payload = json.loads(
        write_validation_result(
            result, _candidate(), protocol, datetime(2026, 9, 6, tzinfo=UTC), tmp_path
        ).read_text(encoding="utf-8")
    )

    assert payload["outcome"] == result.outcome.value
    assert payload["reason"] == result.reason
    assert len(payload["folds"]) == len(result.folds)
    assert payload["window"]["fold_months"] == FOLD_MONTHS
    assert payload["window"]["bars"] == result.bars_used
    assert payload["pooled"]["trades"] == result.total_trades
    assert math.isclose(payload["pooled"]["max_drawdown_pct"], result.pooled_max_drawdown_pct)

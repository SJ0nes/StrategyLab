"""Tests for the surrogate series and the noise ceiling built on them.

WHAT A SURROGATE MUST DO IS A METHODOLOGICAL CLAIM, NOT AN IMPLEMENTATION
DETAIL, so most of this file asserts properties rather than values. The claim,
from `CLAUDE.md`: block resampling rather than an i.i.d. shuffle, "because
shuffling destroys the autocorrelation that a trend strategy trades, which makes
the surrogate an unfairly weak opponent and sets the bar far too low."

The tests below therefore measure the surrogate against BOTH ends: it must keep
the autocorrelation a shuffle would destroy, and it must not simply be the real
series back again.

**The one derived number.** `block_length_for` returns the largest `max` any
searched range declares. For the committed grid - fast to 30, slow to 120, trend
to 200 - that is 200. Nothing else in this file is a hand-derived value; the
rest are relationships that hold whatever the fixture contains.

**Why the grids here are tiny.** One ceiling trial runs an entire grid, so these
tests use two-parameter grids of a few cells over the recorded fixture. A
realistic ceiling is hours of work and belongs in a manual run, not a suite.

**What no test here claims.** Nothing about whether the committed strategy beats
its ceiling. No such comparison exists in this repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from strategylab.backtest.costs import CostModel
from strategylab.core.strategy import required_history
from strategylab.core.types import Condition, IndicatorRef, StrategyDef
from strategylab.search.grid import expand_grid
from strategylab.search.noise import RELIABLE_TRIALS, noise_ceiling, trials_note
from strategylab.search.surrogate import (
    MIN_BLOCK_BARS,
    MIN_BLOCKS,
    block_length_for,
    block_resample,
)

FIXTURE_BARS = Path(__file__).resolve().parent / "fixtures" / "SPY.parquet"

#: Costs in FRACTIONS - the committed protocol's rates, already converted. The
#: ceiling must be computed against the same costs as the real search, or the
#: two are not comparable.
TEST_COSTS = CostModel(
    spread_frac=0.0003,
    slippage_frac=0.0005,
    commission_frac=0.0005,
    commission_min_flat=0.0,
    fx_conversion_frac=0.0,
)

COMMITTED_SPEC: dict[str, dict[str, float]] = {
    "fast": {"min": 5, "max": 30, "step": 5},
    "slow": {"min": 40, "max": 120, "step": 20},
    "trend": {"min": 100, "max": 200, "step": 50},
}

#: A four-cell grid, small enough that a handful of ceiling trials is quick.
TINY_SPEC: dict[str, dict[str, float]] = {
    "fast": {"min": 3, "max": 5, "step": 2},
    "slow": {"min": 10, "max": 20, "step": 10},
}


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    """The recorded SPY bars. No network call."""
    return pd.read_parquet(FIXTURE_BARS)


def _strategy() -> StrategyDef:
    """A two-parameter crossover matching `TINY_SPEC`."""
    fast = IndicatorRef("sma", period_param="fast")
    slow = IndicatorRef("sma", period_param="slow")
    return StrategyDef(
        name="tiny",
        version=1,
        description="Enter when fast crosses above slow, exit when it crosses back.",
        entry=(Condition(fast, "crosses_above", slow),),
        exit=(Condition(fast, "crosses_below", slow),),
        search=TINY_SPEC,
        fixed={"position_size_pct": 95.0},
    )


def _log_returns(frame: pd.DataFrame) -> Any:
    """Close-to-close log returns of a frame, as a numpy array."""
    closes = frame["close"].to_numpy(dtype="float64")
    return np.log(closes[1:] / closes[:-1])


# --------------------------------------------------------------------------
# Block length: the choice that decides whether the ceiling is a fair opponent.
# --------------------------------------------------------------------------


def test_the_block_is_as_long_as_the_longest_lookback_the_grid_reaches() -> None:
    # The committed grid tops out at trend = 200, so a block must be 200 bars -
    # long enough that a full trend fits inside one. See docs/decisions/0008.
    assert block_length_for(COMMITTED_SPEC) == 200
    assert block_length_for(TINY_SPEC) == 20


def test_a_single_parameter_grid_sizes_its_block_from_that_parameter() -> None:
    assert block_length_for({"only": {"min": 2, "max": 37, "step": 5}}) == 37


def test_a_tiny_lookback_still_gets_a_block_of_at_least_two() -> None:
    # A block of one IS the i.i.d. shuffle the project rules forbid.
    assert block_length_for({"p": {"min": 1, "max": 1, "step": 1}}) == MIN_BLOCK_BARS


def test_the_block_is_sized_from_what_the_grid_reaches_not_what_it_declares() -> None:
    # A range is inclusive of `max` only on a step boundary. {1, 10, 4} tops out
    # at 9, so the block is 9 - the same rule `plateau.is_edge_winner` applies,
    # and taken from the same place so the two cannot disagree. An earlier
    # version read the declared bound and returned 10.
    spec: dict[str, dict[str, float]] = {"p": {"min": 1, "max": 10, "step": 4}}
    assert sorted({cell["p"] for cell in expand_grid(spec)}) == [1, 5, 9]
    assert block_length_for(spec) == 9


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ({}, "empty"),
        ({"p": {"min": 1, "step": 1}}, "step"),
        ({"p": {"min": 1, "max": "x", "step": 1}}, "max"),
        ({"p": {"min": 10, "max": 2, "step": 1}}, "max"),
    ],
)
def test_a_spec_whose_lookbacks_cannot_be_read_is_refused(spec: dict[str, Any], match: str) -> None:
    # Guessing a block length from a malformed spec would silently change the
    # methodology rather than fail. Everything but the empty case is refused by
    # `expand_grid` and propagated, so a bad range is reported once, in one
    # vocabulary, by the function that owns it.
    with pytest.raises(ValueError, match=match):
        block_length_for(spec)


# --------------------------------------------------------------------------
# The surrogate: what it keeps and what it destroys.
# --------------------------------------------------------------------------


def test_a_surrogate_has_the_same_shape_and_calendar_as_the_real_series(
    bars: pd.DataFrame,
) -> None:
    # The grid's warm-up arithmetic must be identical on real and surrogate
    # data, or the comparison is not like for like.
    surrogate = block_resample(bars, 20, np.random.default_rng(0))
    assert surrogate.shape == bars.shape
    assert surrogate.index.equals(bars.index)
    assert list(surrogate.columns) == list(bars.columns)


def test_every_surrogate_price_is_positive(bars: pd.DataFrame) -> None:
    # Compounding LOG returns can never reach zero. It matters: a non-positive
    # price reaches `apply_costs`, which refuses it, and would fail the
    # surrogate's cells rather than scoring them - quietly computing a ceiling
    # from fewer trials than it claims.
    surrogate = block_resample(bars, 20, np.random.default_rng(1))
    assert bool((surrogate[["open", "high", "low", "close"]] > 0).all().all())


def test_a_surrogate_bar_keeps_a_real_bar_s_shape(bars: pd.DataFrame) -> None:
    # Blocks are drawn over bar INDICES, so each bar's own high/low/open ratios
    # travel with its return. A surrogate bar is a real bar at a new price
    # level rather than a synthesised one.
    surrogate = block_resample(bars, 20, np.random.default_rng(2))
    assert bool((surrogate["high"] >= surrogate["low"]).all())
    assert bool((surrogate["high"] >= surrogate["close"]).all())
    assert bool((surrogate["low"] <= surrogate["close"]).all())


def test_the_first_bar_is_the_real_first_bar(bars: pd.DataFrame) -> None:
    # It has no predecessor and therefore no return; starting the compounding
    # anywhere else would move the surrogate to a different price level for no
    # reason.
    surrogate = block_resample(bars, 20, np.random.default_rng(3))
    for column in ("open", "high", "low", "close"):
        assert surrogate.iloc[0][column] == pytest.approx(bars.iloc[0][column])


def _clustering(returns: Any) -> float:
    """Lag-one autocorrelation of ABSOLUTE returns: volatility clustering."""
    return float(np.corrcoef(np.abs(returns[:-1]), np.abs(returns[1:]))[0, 1])


def _regime_bars(periods: int = 1200, regime: int = 150, seed: int = 0) -> pd.DataFrame:
    """A synthetic series alternating between calm and volatile regimes.

    THE RECORDED FIXTURE CANNOT CARRY THIS TEST, which is why this exists.
    `tests/fixtures/SPY.parquet` is a single year - 253 bars - and its lag-one
    absolute-return autocorrelation is about 0.04, indistinguishable from an
    i.i.d. shuffle of itself. There is no clustering in it to preserve, so a
    test using it would pass whatever `block_resample` did. The full cached
    series does show the effect strongly, but `data/` is gitignored and no test
    may depend on it.

    So the signal is built here: 150 bars of low volatility then 150 of high,
    repeating. That is a caricature of the real effect rather than a model of
    it, and a caricature is what a test needs - large, deterministic and
    unmistakable.
    """
    generator = np.random.default_rng(seed)
    scale = np.where((np.arange(periods) // regime) % 2 == 0, 0.002, 0.02)
    returns = generator.normal(0.0, scale)
    closes = 100.0 * np.exp(np.cumsum(returns))
    return pd.DataFrame(
        {
            "open": closes * 0.999,
            "high": closes * 1.004,
            "low": closes * 0.996,
            "close": closes,
            "volume": np.full(periods, 1_000_000),
        },
        index=pd.date_range("2005-01-03", periods=periods, freq="B"),
    )


def test_the_surrogate_keeps_the_volatility_clustering_a_shuffle_destroys() -> None:
    # THE CLAIM THE WHOLE METHOD RESTS ON, measured against both ends rather
    # than asserted. CLAUDE.md: an i.i.d. shuffle "destroys the autocorrelation
    # that a trend strategy trades, which makes the surrogate an unfairly weak
    # opponent and sets the bar far too low."
    source = _regime_bars()
    real = _log_returns(source)
    surrogate = _log_returns(block_resample(source, 60, np.random.default_rng(4)))
    shuffled = np.random.default_rng(4).permutation(real)

    # The synthetic series must actually exhibit the effect, or nothing below is
    # a test of anything.
    assert _clustering(real) > 0.3

    # DERIVED, NOT EYEBALLED. One adjacent pair in every B straddles a block
    # seam and contributes no covariance, so the surrogate's autocorrelation is
    # about `(1 - 1/B)` of the real one - 0.983 at B = 60. An earlier version
    # asserted `> real / 2`, which reduces to `B > 2` and would pass with
    # three-bar blocks: it looked like a test of block resampling and was a test
    # of almost nothing.
    expected = (1.0 - 1.0 / 60) * _clustering(real)
    assert _clustering(surrogate) == pytest.approx(expected, rel=0.15)

    # The shuffle keeps essentially none of it. Under the null of no
    # autocorrelation the sample estimate has SD about `1/sqrt(n)`, which is
    # 0.029 for this series, so the bound is three sigma. An earlier version
    # used 0.05 - 1.7 sigma - which roughly one seed in eighteen would breach.
    sigma = 1.0 / np.sqrt(len(real))
    assert abs(_clustering(shuffled)) < 3.0 * sigma
    assert _clustering(surrogate) > abs(_clustering(shuffled)) * 4


def test_the_recorded_fixture_is_too_short_to_show_clustering(bars: pd.DataFrame) -> None:
    # Pinned so the reasoning above cannot quietly rot. If a longer fixture is
    # ever committed this goes red, and whoever changed it should move the
    # clustering test onto real bars rather than leave the synthetic one.
    #
    # The bound is OBSERVED, not derived: the fixture's value is about 0.04.
    # Two sigma under the no-autocorrelation null is 0.126 for 252 returns, so
    # this says the fixture is indistinguishable from having none - which is
    # exactly why it cannot carry the test above.
    assert len(bars) < 300
    two_sigma = 2.0 / np.sqrt(len(bars) - 1)
    assert abs(_clustering(_log_returns(bars))) < two_sigma


def test_a_surrogate_is_not_simply_the_real_series_again(bars: pd.DataFrame) -> None:
    # The other end of the same claim: preserve everything and the strategy
    # finds the real edge again, making the ceiling unbeatable.
    surrogate = block_resample(bars, 20, np.random.default_rng(5))
    assert not np.allclose(
        surrogate["close"].to_numpy(dtype="float64"),
        bars["close"].to_numpy(dtype="float64"),
    )


def test_the_same_seed_gives_the_same_surrogate(bars: pd.DataFrame) -> None:
    # A ceiling that moves between runs cannot be compared against anything.
    first = block_resample(bars, 20, np.random.default_rng(7))
    second = block_resample(bars, 20, np.random.default_rng(7))
    pd.testing.assert_frame_equal(first, second)


def test_a_different_seed_gives_a_different_surrogate(bars: pd.DataFrame) -> None:
    first = block_resample(bars, 20, np.random.default_rng(7))
    second = block_resample(bars, 20, np.random.default_rng(8))
    assert not first.equals(second)


@pytest.mark.parametrize(
    ("frame_rows", "block", "match"),
    [
        (1, 20, "a return needs two"),
        (100, 1, "below the floor"),
        (100, 500, "longest block leaving"),
    ],
)
def test_a_resample_that_cannot_be_done_is_refused(
    bars: pd.DataFrame, frame_rows: int, block: int, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        block_resample(bars.iloc[:frame_rows], block, np.random.default_rng(0))


def test_a_frame_missing_a_price_column_is_refused(bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="required column"):
        block_resample(bars.drop(columns=["high"]), 20, np.random.default_rng(0))


# --------------------------------------------------------------------------
# The ceiling.
# --------------------------------------------------------------------------


def test_the_ceiling_holds_one_value_per_trial(bars: pd.DataFrame) -> None:
    ceiling = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=3, seed=0)
    assert len(ceiling) == 3
    assert list(ceiling.index) == [0, 1, 2]
    assert ceiling.name == "best_in_grid_sharpe"


def test_the_ceiling_is_reproducible_from_its_seed(bars: pd.DataFrame) -> None:
    first = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=3, seed=11)
    second = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=3, seed=11)
    pd.testing.assert_series_equal(first, second)


def test_a_different_seed_moves_the_ceiling(bars: pd.DataFrame) -> None:
    first = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=3, seed=11)
    second = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=3, seed=12)
    assert not first.equals(second)


def test_the_ceiling_is_above_zero(bars: pd.DataFrame) -> None:
    # The entire reason the component exists: the expected maximum across many
    # draws of noise is well above zero, so a positive Sharpe on real data is
    # not by itself evidence of anything. If this ever asserted a ceiling AT
    # zero, the comparison it feeds would be meaningless.
    ceiling = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=5, seed=3)
    assert ceiling.max() > 0.0


def test_a_warm_up_can_never_be_the_thing_that_empties_a_trial() -> None:
    # A consequence of the block-length choice, worth pinning because it is not
    # obvious and it removes a whole class of failure: if a surrogate can be
    # built at all, every cell in the grid can warm up on it.
    #
    # ASSERTED AGAINST THE REAL FUNCTIONS. An earlier version computed both
    # sides itself and compared `longest + 1 >= longest + 1`, which is `X >= X`
    # and could not fail - a test of arithmetic the test had just done, not of
    # the code. This one asks `block_resample` what the shortest window it
    # accepts is, and `required_history` what the largest cell needs.
    for spec in (TINY_SPEC, COMMITTED_SPEC):
        block = block_length_for(spec)
        shortest_window = block * MIN_BLOCKS + 1

        # The shortest window a surrogate can be built from, per the guard.
        source = _regime_bars(periods=shortest_window)
        assert block_resample(source, block, np.random.default_rng(0)) is not None

        # The hungriest cell in that grid, per the engine's own rule.
        hungriest = max(required_history(_spec_strategy(spec), cell) for cell in expand_grid(spec))
        assert hungriest <= shortest_window


def _spec_strategy(spec: dict[str, dict[str, float]]) -> StrategyDef:
    """A crossover whose two placeholders are the first two names in a spec."""
    names = list(spec)
    left = IndicatorRef("sma", period_param=names[0])
    right = IndicatorRef("sma", period_param=names[-1])
    return StrategyDef(
        name="probe",
        version=1,
        description="Probe strategy for warm-up arithmetic.",
        entry=(Condition(left, "crosses_above", right),),
        exit=(Condition(left, "crosses_below", right),),
        search=spec,
        fixed={"position_size_pct": 95.0},
    )


def test_a_trial_where_no_cell_scored_is_nan_rather_than_dropped(
    bars: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The trial must still appear: a caller counting the values it got back must
    # not be silently handed fewer than it asked for.
    #
    # Forced, because the natural cause cannot happen - see the test above. What
    # stays reachable is an account driven to zero, which `max_drawdown`
    # refuses. Rather than contrive a surrogate that bankrupts every cell, the
    # engine is made to raise: the branch under test is the loop's handling of a
    # failed cell, not the engine's reason for failing.
    def always_fails(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("forced cell failure")

    monkeypatch.setattr("strategylab.search.noise.run_backtest", always_fails)
    ceiling = noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=2, seed=0)

    assert len(ceiling) == 2
    assert bool(ceiling.isna().all())


def test_a_non_positive_trial_count_is_refused(bars: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="at least one surrogate"):
        noise_ceiling(_strategy(), TINY_SPEC, bars, TEST_COSTS, n_trials=0)


def test_a_window_too_short_for_the_grid_s_block_is_refused(bars: pd.DataFrame) -> None:
    # The block length comes from the GRID, not from the data, so a long
    # lookback against a short window cannot be resampled at all.
    with pytest.raises(ValueError, match="longest block leaving"):
        noise_ceiling(_strategy(), TINY_SPEC, bars.iloc[:15], TEST_COSTS, n_trials=1)


def test_a_window_giving_too_few_blocks_is_refused(bars: pd.DataFrame) -> None:
    # THE CHECK THAT STOPS A SURROGATE BEING THE INPUT. A block as long as the
    # series is one block drawn once, so the "surrogate" is the real series and
    # the ceiling measures the strategy against itself. That produced no error
    # before this guard existed.
    returns = len(bars) - 1
    with pytest.raises(ValueError, match="measure the strategy against itself"):
        block_resample(bars, returns, np.random.default_rng(0))
    # And the boundary from the accepting side, so an off-by-one would show.
    largest = returns // MIN_BLOCKS
    assert block_resample(bars, largest, np.random.default_rng(0)) is not None
    with pytest.raises(ValueError, match="longest block leaving"):
        block_resample(bars, largest + 1, np.random.default_rng(0))


# --------------------------------------------------------------------------
# What a trial count buys, said out loud.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("trials", [1, 10, RELIABLE_TRIALS - 1])
def test_a_short_run_says_its_tail_is_unstable(trials: int) -> None:
    note = trials_note(trials)
    assert "not as a threshold" in note
    assert str(RELIABLE_TRIALS) in note


@pytest.mark.parametrize("trials", [RELIABLE_TRIALS, RELIABLE_TRIALS + 500])
def test_a_long_run_says_the_tail_can_be_read(trials: int) -> None:
    note = trials_note(trials)
    assert "supports reading the upper percentiles" in note
    # Still hedged: one price series, one grid.
    assert "estimate" in note

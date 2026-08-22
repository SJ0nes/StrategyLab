"""Tests for grid expansion, and for the seam it closes.

EVERY EXPECTED NUMBER IN THIS FILE IS DERIVED FROM A CLOSED FORM STATED BESIDE
IT, not read off what the implementation printed. Two forms carry almost all of
them, and both are written out here once so a reader checking the arithmetic
never has to open `src/`.

**Cells along one axis.** A range `{min, max, step}` is inclusive of `min`, and
inclusive of `max` only when `max - min` is an exact multiple of `step`. So the
values are `min + k * step` for `k = 0, 1, ...` while that stays at or below
`max`, and the count is

    floor((max - min) / step) + 1

The size of the whole grid is the product of that count over every parameter.

**Length of an equity curve.** THE WARM-UP IS ONE NUMBER FOR THE WHOLE STRATEGY,
not one per condition, and that distinction decides this file's arithmetic. The
engine asks for the longest lookback appearing ANYWHERE in the strategy, then
adds a single bar if ANY condition uses a crossing operator - whether or not the
condition holding that longest lookback is the crossing one. So for a strategy
with a longest lookback of `L` anywhere in it and at least one crossing operator,
the warm-up is `L + 1` bars, the first evaluable bar sits at index `L`, and the
curve - which starts at the first evaluable bar, not at bar zero - holds

    len(bars) - (L + 1) + 1  =  len(bars) - L

points. Spelled out because the per-condition reading is the tempting one and it
is wrong here: in `configs/ma_crossover.yaml` the longest lookback is usually
`trend`, which sits under `above` rather than under a crossing, and a
per-condition rule would give `max(slow + 1, trend)` - one bar different on 78 of
the 90 cells. The uniform rule costs one usable bar on those cells and is
conservative rather than dangerous, but it is the rule, and no other document in
this repository states it.

In that config the three lookbacks are `fast`, `slow` and `trend`, and `fast`
never exceeds 30 while `slow` never falls below 40 - so `L = max(slow, trend)`
for every cell in the grid, and `fast` never decides it.

WHAT THIS FILE DOES NOT CLAIM. The end-to-end tests run the REAL committed config
against the REAL recorded price fixture and the REAL protocol costs - nothing is
stubbed, because the claim they carry is precisely that the pieces fit each other
rather than fitting a mock. But the fixture is one year of bars against longest
lookbacks of 100 to 200 - a warm-up of `L + 1`, so 101 to 201 bars, and the two
are not the same number. What each cell has left to trade on is `253 - L`:

    L = 100 -> 153 bars, 24 cells   (trend = 100 with slow <= 100: 4 x 6)
    L = 120 -> 133 bars,  6 cells   (trend = 100 with slow = 120:  1 x 6)
    L = 150 -> 103 bars, 30 cells   (trend = 150, any slow:        5 x 6)
    L = 200 ->  53 bars, 30 cells   (trend = 200, any slow:        5 x 6)

So a third of the grid gets 53 bars and the rest gets 103 or more. How many
round trips that produces is not stated here, because no assertion in this file
establishes it and an unmeasured count does not belong in a docstring. What the
file does assert is that at least one cell traded at all.

Nothing here is evidence about the strategy. No assertion in this file is a
figure the implementation printed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from strategylab.backtest.costs import CostModel
from strategylab.backtest.engine import BacktestResult, run_backtest
from strategylab.core.config import load_strategy
from strategylab.core.types import IndicatorRef, StrategyDef
from strategylab.protocol import load_protocol
from strategylab.search.grid import expand_grid

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CONFIG = REPO_ROOT / "configs" / "ma_crossover.yaml"
REPO_PROTOCOL = REPO_ROOT / "protocol.yaml"
FIXTURE_BARS = Path(__file__).resolve().parent / "fixtures" / "SPY.parquet"

# `run_backtest`'s documented default starting equity, in account currency units.
# `tests/test_engine.py` asserts the same constant exactly.
STARTING_EQUITY = 10_000.0

# The `search` block of configs/ma_crossover.yaml, copied here on purpose. A test
# that read the file to build its own expectation would agree with the file
# whatever the file said; this literal is what the config is asserted AGAINST.
COMMITTED_SEARCH: dict[str, dict[str, float]] = {
    "fast": {"min": 5, "max": 30, "step": 5},
    "slow": {"min": 40, "max": 120, "step": 20},
    "trend": {"min": 100, "max": 200, "step": 50},
}

# floor((30 - 5) / 5) + 1 = 5 + 1 = 6
FAST_VALUES = [5, 10, 15, 20, 25, 30]
# floor((120 - 40) / 20) + 1 = 4 + 1 = 5
SLOW_VALUES = [40, 60, 80, 100, 120]
# floor((200 - 100) / 50) + 1 = 2 + 1 = 3
TREND_VALUES = [100, 150, 200]
# 6 * 5 * 3 = 90
COMMITTED_CELLS = 90

# L = max(slow, trend) over the whole grid. trend contributes 100, 150 and 200;
# slow contributes 120, reachable only when trend = 100, because trend = 150 and
# trend = 200 each dominate every available slow. Nothing else is reachable.
LONGEST_LOOKBACKS = {100, 120, 150, 200}


# --------------------------------------------------------------------------
# Enumeration
# --------------------------------------------------------------------------


def test_the_committed_grid_has_the_ninety_cells_the_config_claims() -> None:
    # The config's own comment says "→ 90 combinations". 6 * 5 * 3 = 90.
    assert len(expand_grid(COMMITTED_SEARCH)) == COMMITTED_CELLS
    assert len(FAST_VALUES) * len(SLOW_VALUES) * len(TREND_VALUES) == COMMITTED_CELLS


@pytest.mark.parametrize(
    ("param", "expected"),
    [("fast", FAST_VALUES), ("slow", SLOW_VALUES), ("trend", TREND_VALUES)],
)
def test_each_axis_holds_the_values_min_plus_k_times_step(param: str, expected: list[int]) -> None:
    grid = expand_grid({param: COMMITTED_SEARCH[param]})
    assert [cell[param] for cell in grid] == expected


def test_the_max_is_included_when_it_falls_on_a_step_boundary() -> None:
    # 1 + 3*3 = 10 exactly, so 10 is a cell: floor((10 - 1) / 3) + 1 = 3 + 1 = 4.
    grid = expand_grid({"p": {"min": 1, "max": 10, "step": 3}})
    assert [cell["p"] for cell in grid] == [1, 4, 7, 10]


def test_the_max_is_excluded_when_it_does_not_fall_on_a_step_boundary() -> None:
    # 1 + 2*4 = 9 and 1 + 3*4 = 13 > 10, so 10 is never reached:
    # floor((10 - 1) / 4) + 1 = 2 + 1 = 3.
    grid = expand_grid({"p": {"min": 1, "max": 10, "step": 4}})
    assert [cell["p"] for cell in grid] == [1, 5, 9]


def test_a_range_whose_min_equals_its_max_yields_one_cell() -> None:
    # floor((7 - 7) / 3) + 1 = 0 + 1 = 1.
    assert expand_grid({"p": {"min": 7, "max": 7, "step": 3}}) == [{"p": 7}]


def test_a_step_wider_than_the_span_yields_only_the_minimum() -> None:
    # floor((9 - 5) / 10) + 1 = 0 + 1 = 1. A range never yields zero cells.
    assert expand_grid({"p": {"min": 5, "max": 9, "step": 10}}) == [{"p": 5}]


def test_an_empty_spec_yields_a_single_empty_combination() -> None:
    # The empty product has exactly one element, and a strategy with nothing to
    # search has exactly one parameter set: the one it was written with.
    assert expand_grid({}) == [{}]


def test_the_last_parameter_varies_fastest() -> None:
    # Two axes of 2 and 3 values: 2 * 3 = 6 cells, reading like an odometer.
    grid = expand_grid(
        {"a": {"min": 1, "max": 2, "step": 1}, "b": {"min": 10, "max": 30, "step": 10}}
    )
    assert grid == [
        {"a": 1, "b": 10},
        {"a": 1, "b": 20},
        {"a": 1, "b": 30},
        {"a": 2, "b": 10},
        {"a": 2, "b": 20},
        {"a": 2, "b": 30},
    ]


def test_parameters_appear_in_the_order_the_spec_gave_them() -> None:
    grid = expand_grid(COMMITTED_SEARCH)
    assert list(grid[0]) == ["fast", "slow", "trend"]


def test_every_cell_in_the_committed_grid_is_distinct() -> None:
    # A duplicate cell would inflate the noise ceiling's trial count without
    # adding a trial, which flatters the real result it is compared against.
    grid = expand_grid(COMMITTED_SEARCH)
    assert len({tuple(sorted(cell.items())) for cell in grid}) == COMMITTED_CELLS


def test_each_cell_is_a_fresh_dict_the_caller_may_mutate() -> None:
    grid = expand_grid(COMMITTED_SEARCH)
    grid[0]["fast"] = -1
    assert grid[1]["fast"] == FAST_VALUES[0]


def test_the_spec_is_not_modified_by_expanding_it() -> None:
    spec: dict[str, dict[str, float]] = {"p": {"min": 1, "max": 3, "step": 1}}
    expand_grid(spec)
    assert spec == {"p": {"min": 1, "max": 3, "step": 1}}


def test_a_float_bound_gives_the_same_grid_as_the_equal_integer_one() -> None:
    # YAML gives back `5` and `5.0` depending on how it was written; the grid
    # must not depend on which. `==` alone would not carry this, because 5 == 5.0
    # in Python, so the int-ness is asserted separately on BOTH paths.
    written_as_int: dict[str, dict[str, float]] = {"p": {"min": 5, "max": 30, "step": 5}}
    as_int = expand_grid(written_as_int)
    as_float = expand_grid({"p": {"min": 5.0, "max": 30.0, "step": 5.0}})
    assert as_int == as_float == [{"p": value} for value in FAST_VALUES]
    assert all(isinstance(cell["p"], int) for cell in as_int)
    assert all(isinstance(cell["p"], int) for cell in as_float)


def test_every_value_of_every_cell_is_an_int_and_not_a_float() -> None:
    # `evaluate` uses a period as a window length. A float would either raise
    # deep inside pandas or, worse, be truncated. Checked on every cell and every
    # key, not on the first one.
    for cell in expand_grid(COMMITTED_SEARCH):
        assert all(isinstance(value, int) for value in cell.values())


# --------------------------------------------------------------------------
# Refusals
#
# Ten conditions. Seven are additions to the contract the stub carried, which
# named only a missing key, a non-positive step and a max below min. Each closes
# a case whose alternative was a silently wrong grid, or an exception of a type
# the contract does not admit, rather than a refused one.
#
# THE ORDER IS RESTATED HERE rather than pointed at, because two conditions can
# hold at once and only the first is reported, and a reader checking these tests
# should not have to open `src/` to know which. `search_spec` is checked first,
# then each parameter in the order given, and within a parameter:
#
#   1. search_spec is a dict          6. no missing key
#   2. the key is a string            7. each bound converts, min then max then
#   3. the key's charset                 step: not bool, int or float, finite,
#   4. the range is a dict               whole
#   5. no unknown key                 8. step >= 1
#                                     9. max >= min
#                                    10. min >= 1
#
# The numbering matches `expand_grid`'s own Raises block item for item, so a
# reader can move between the two without re-deriving the correspondence.
# --------------------------------------------------------------------------


def test_a_search_spec_that_is_not_a_dict_is_refused() -> None:
    with pytest.raises(ValueError, match="not a dict"):
        expand_grid([("fast", {"min": 1.0, "max": 10.0, "step": 1.0})])  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["$fast", "fast$", "fa$st", "", "my param", "a/b", "fast\n"])
def test_a_parameter_name_outside_the_allowed_charset_is_refused(name: str) -> None:
    # The same charset `load_strategy` holds these names to, for the same
    # reason - the name becomes part of a result key. A `$fast` key here would
    # otherwise surface as a missing-parameter error from a different module,
    # and a trailing newline is what a YAML block scalar produces by accident.
    with pytest.raises(ValueError, match="letters, digits, underscores or hyphens"):
        expand_grid({name: {"min": 1.0, "max": 10.0, "step": 1.0}})


@pytest.mark.parametrize("name", [5, None, (1, 2)])
def test_a_parameter_name_that_is_not_a_string_is_refused(name: object) -> None:
    # Reported separately from the charset check, and before it, so the message
    # names the fault that fired rather than the one that did not. A non-string
    # key reported as failing a charset rule sends the reader looking for a
    # character that is not there.
    with pytest.raises(ValueError, match="is not a string"):
        expand_grid({name: {"min": 1.0, "max": 10.0, "step": 1.0}})  # type: ignore[dict-item]


@pytest.mark.parametrize("spec", [5, None, "minmaxstep", {"min", "max", "step"}])
def test_a_range_that_is_not_a_dict_is_refused(spec: object) -> None:
    # Without this, an int range raises TypeError on iteration and a str range
    # is reported as carrying the unknown keys 'a', 'e', 'i' - neither of which
    # is a failure mode this function's contract admits.
    with pytest.raises(ValueError, match="is not a dict"):
        expand_grid({"p": spec})  # type: ignore[dict-item]


def test_an_unrecognised_key_inside_a_range_is_refused() -> None:
    # A misspelled key silently ignored is a range that is not the range its
    # author wrote.
    spec: dict[str, Any] = {"min": 1.0, "max": 10.0, "step": 1.0, "mode": "log"}
    with pytest.raises(ValueError, match="unknown keys"):
        expand_grid({"p": spec})


@pytest.mark.parametrize("omitted", ["min", "max", "step"])
def test_a_range_missing_a_key_is_refused(omitted: str) -> None:
    spec = {"min": 1.0, "max": 10.0, "step": 1.0}
    del spec[omitted]
    with pytest.raises(ValueError, match=omitted):
        expand_grid({"p": spec})


@pytest.mark.parametrize("bound", ["min", "max", "step"])
@pytest.mark.parametrize("value", [2.5, float("nan"), float("inf")])
def test_a_bound_that_is_not_a_finite_whole_number_is_refused(bound: str, value: float) -> None:
    # Refused rather than rounded: rounding would hand the engine a lookback the
    # config never declared, and the search would report a result for a cell
    # that does not exist.
    spec: dict[str, float] = {"min": 1.0, "max": 10.0, "step": 1.0}
    spec[bound] = value
    with pytest.raises(ValueError, match="whole"):
        expand_grid({"p": spec})


@pytest.mark.parametrize("value", ["5", None, [5]])
def test_a_bound_that_is_neither_an_int_nor_a_float_is_refused(value: object) -> None:
    spec: dict[str, Any] = {"min": value, "max": 10.0, "step": 1.0}
    with pytest.raises(ValueError, match="neither an int nor a float"):
        expand_grid({"p": spec})


def test_a_boolean_bound_is_refused() -> None:
    # `True` is an `int` in Python and would otherwise pass as a step of one bar.
    spec: dict[str, Any] = {"min": 1.0, "max": 10.0, "step": True}
    with pytest.raises(ValueError, match="boolean"):
        expand_grid({"p": spec})


def test_a_very_large_integer_bound_does_not_escape_as_an_overflow() -> None:
    # Python ints are unbounded and `math.isfinite` converts to float, which
    # raises OverflowError past about 1.8e308 - not a ValueError, and so not
    # something the contract admits. A whole int must never reach that call.
    grid = expand_grid({"p": {"min": 10**400, "max": 10**400, "step": 1}})
    assert grid == [{"p": 10**400}]


@pytest.mark.parametrize("step", [0.0, -5.0])
def test_a_step_below_one_bar_is_refused(step: float) -> None:
    # A step of zero never advances and a negative one walks away from `max`;
    # both are an infinite or empty grid rather than a coarse one.
    with pytest.raises(ValueError, match="at least 1 bar"):
        expand_grid({"p": {"min": 1.0, "max": 10.0, "step": step}})


def test_a_max_below_its_min_is_refused() -> None:
    with pytest.raises(ValueError, match="below its 'min'"):
        expand_grid({"p": {"min": 10.0, "max": 1.0, "step": 1.0}})


@pytest.mark.parametrize("low", [0.0, -10.0])
def test_a_min_below_one_bar_is_refused(low: float) -> None:
    # Half a bar does not exist and neither does a lookback of minus ten. Without
    # this, a hand-built spec gets a weaker guarantee than `load_strategy` gives
    # a config file, which is the gap this check exists to close.
    with pytest.raises(ValueError, match="at least 1 bar"):
        expand_grid({"p": {"min": low, "max": 10.0, "step": 1.0}})


def test_an_unknown_key_is_reported_before_the_key_it_displaces() -> None:
    # `stp` both adds an unknown key and omits `step`. The documented order
    # reports the misspelling, which is the fault, rather than the omission,
    # which is its consequence.
    spec: dict[str, Any] = {"min": 1.0, "max": 10.0, "stp": 1.0}
    with pytest.raises(ValueError, match="unknown keys"):
        expand_grid({"p": spec})


@pytest.mark.parametrize(
    ("spec", "reported"),
    [
        ({"min": 2.5, "max": 3.5, "step": 1.0}, "min"),
        ({"min": 1.0, "max": 3.5, "step": 0.5}, "max"),
    ],
)
def test_the_bounds_are_converted_in_the_order_min_max_step(
    spec: dict[str, float], reported: str
) -> None:
    # Two bounds are malformed in each case; the contract fixes which is named.
    with pytest.raises(ValueError, match=f"'{reported}' of"):
        expand_grid({"p": spec})


def test_a_key_whose_rendering_raises_does_not_replace_the_message() -> None:
    # An unknown key is rendered into the message, and for a hand-built spec a
    # key is an arbitrary object whose `__str__` is arbitrary code. If that code
    # raises anything, the refusal this function meant to report is replaced by
    # an unrelated failure. The digit-limit case raises ValueError and so would
    # survive a narrow guard; this one raises RuntimeError and would not.
    class Unrenderable:
        def __str__(self) -> str:
            raise RuntimeError("this object refuses to render")

    spec: dict[Any, Any] = {"min": 1.0, "max": 10.0, "step": 1.0, Unrenderable(): 1}
    with pytest.raises(ValueError, match="unknown keys"):
        expand_grid({"p": spec})


def test_a_bound_whose_rendering_raises_does_not_replace_the_message() -> None:
    # The same hazard as the unrenderable KEY above, but on a BOUND, and reached
    # through the non-whole branch. A float subclass is explicitly admitted -
    # `numpy.float64` is the case the contract names - and its `__format__` is
    # arbitrary code. `__format__` and not `__str__`, because an empty format
    # spec on a float subclass goes to `float.__format__`, which never consults
    # `__str__`.
    class Unrenderable(float):
        def __format__(self, spec: str) -> str:
            raise RuntimeError("this bound refuses to render")

    spec: dict[str, Any] = {"min": Unrenderable(2.5), "max": 10.0, "step": 1.0}
    with pytest.raises(ValueError, match="not a finite whole"):
        expand_grid({"p": spec})


def test_an_enormous_integer_bound_does_not_replace_the_message_with_a_digit_limit() -> None:
    # CPython refuses to render an int of more than 4300 digits as a string. A
    # bound that large reaches the message through the int fast path, so an
    # unguarded f-string would raise about digit limits instead of about the
    # bound. The exception type would still be ValueError, which is why this is
    # not caught by asserting the type.
    with pytest.raises(ValueError, match="a lookback is at least 1 bar"):
        expand_grid({"p": {"min": -(10**5000), "max": 10, "step": 1}})


def test_a_bound_is_checked_for_wholeness_before_it_is_compared_to_another() -> None:
    # Documented ordering: two conditions are true here - `max` is not whole AND
    # it is below `min` - and the non-whole bound is the one reported. `step` is
    # valid and plays no part, so this pins down the max-versus-min ordering
    # only; the min/max/step conversion order is the test above.
    #
    # `min` is deliberately WHOLE and `max` the offender. With both malformed the
    # test could not tell the orderings apart: `min` is converted first either
    # way, so it would report "whole" even in an implementation that compared the
    # pair the moment it had two numbers to compare.
    with pytest.raises(ValueError, match="whole"):
        expand_grid({"p": {"min": 5, "max": 1.5, "step": 1.0}})


# --------------------------------------------------------------------------
# End to end: load_strategy -> expand_grid -> run_backtest
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed_strategy() -> StrategyDef:
    """The real config in `configs/`, loaded by the real loader."""
    return load_strategy(REPO_CONFIG)


@pytest.fixture(scope="module")
def fixture_bars() -> pd.DataFrame:
    """The recorded SPY bars in `tests/fixtures/`. No network call."""
    return pd.read_parquet(FIXTURE_BARS)


@pytest.fixture(scope="module")
def whole_grid_run(
    committed_strategy: StrategyDef, fixture_bars: pd.DataFrame
) -> list[tuple[dict[str, int], BacktestResult]]:
    """Every cell of the committed grid, backtested once, with the real costs.

    Module-scoped because running all ninety cells takes several seconds and
    every test below asks a different question of the same run.
    """
    costs = load_protocol(REPO_PROTOCOL).costs
    grid = expand_grid(committed_strategy.search)
    return [(cell, run_backtest(committed_strategy, cell, fixture_bars, costs)) for cell in grid]


def test_the_committed_config_declares_the_search_block_this_file_asserts_against(
    committed_strategy: StrategyDef,
) -> None:
    # If someone edits the config's `search` block, this fails first and names
    # the cause, rather than every hand-derived count below failing at once.
    assert committed_strategy.search == COMMITTED_SEARCH


def test_the_grid_supplies_exactly_the_placeholders_the_config_left_unresolved(
    committed_strategy: StrategyDef,
) -> None:
    # THIS IS THE SEAM. `load_strategy` leaves a period as `$fast`; `evaluate`
    # looks `fast` up in the cell. Neither half has ever checked the other.
    placeholders = {
        side.period_param
        for condition in committed_strategy.entry + committed_strategy.exit
        for side in (condition.left, condition.right)
        if isinstance(side, IndicatorRef) and side.period_param is not None
    }
    assert placeholders == {"fast", "slow", "trend"}
    for cell in expand_grid(committed_strategy.search):
        assert set(cell) == placeholders


def test_the_grid_built_from_the_real_config_carries_ints(
    committed_strategy: StrategyDef,
) -> None:
    # The int-ness guarantee asserted on the path that can actually break it. A
    # config written `min: 5.0` reaches `expand_grid` as a float, and `5 == 5.0`
    # means no equality assertion anywhere would notice.
    for cell in expand_grid(committed_strategy.search):
        assert all(isinstance(value, int) for value in cell.values())


def test_every_cell_of_the_committed_grid_backtests(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
) -> None:
    # The work is done by the fixture, where a raise on any of the ninety cells
    # errors every test in this section. This line only records the count.
    assert len(whole_grid_run) == COMMITTED_CELLS


def test_each_result_carries_the_cell_that_produced_it(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
) -> None:
    for cell, result in whole_grid_run:
        assert result.params == cell


def test_each_equity_curve_is_as_long_as_the_warm_up_leaves_room_for(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
    fixture_bars: pd.DataFrame,
) -> None:
    # len(bars) - L, where L = max(slow, trend) is the longest lookback anywhere
    # in the cell. See this module's docstring for the derivation, and note that
    # the +1 for the crossing operator is applied once for the whole strategy.
    for cell, result in whole_grid_run:
        longest_lookback = max(cell["slow"], cell["trend"])
        assert len(result.equity) == len(fixture_bars) - longest_lookback


def test_the_curve_lengths_are_exactly_the_four_the_grid_can_produce(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
    fixture_bars: pd.DataFrame,
) -> None:
    # The SET, not merely the count: four wrong-but-distinct lengths would pass a
    # cardinality check. With the fixture at 253 rows the four are 153, 133, 103
    # and 53, from L in {100, 120, 150, 200}.
    expected = {len(fixture_bars) - lookback for lookback in LONGEST_LOOKBACKS}
    assert {len(result.equity) for _, result in whole_grid_run} == expected


def test_every_curve_starts_at_the_starting_equity(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
) -> None:
    # Exact, not approximate. The curve's first point is the starting equity with
    # nothing yet traded, so any tolerance here would swallow a cost charged
    # during the warm-up, which is one of the bugs this test exists to exclude.
    for _, result in whole_grid_run:
        assert result.equity.iloc[0] == STARTING_EQUITY


def test_the_seam_carries_values_through_to_the_arithmetic(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
) -> None:
    # Finiteness first: a set of NaNs has 90 members, because NaN is equal to
    # nothing including itself, so a "more than one distinct value" check alone
    # passes on an engine that returns NaN everywhere.
    finals = [float(result.equity.iloc[-1]) for _, result in whole_grid_run]
    assert all(math.isfinite(value) for value in finals)
    assert len(set(finals)) > 1


def test_at_least_one_cell_actually_traded(
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
) -> None:
    # Without this, every assertion above is satisfied by a grid that entered no
    # position at all - the curve would be flat at the starting equity and still
    # the right length. Deliberately a floor of one rather than a count: how many
    # cells trade is a property of the fixture, not of any rule stated here.
    assert any(result.fills for _, result in whole_grid_run)


def test_the_fast_parameter_reaches_the_result(
    committed_strategy: StrategyDef, fixture_bars: pd.DataFrame
) -> None:
    # `slow` and `trend` alone already produce distinct outcomes, so a grid that
    # dropped `fast` entirely would pass every other test in this file. These two
    # cells differ in nothing else.
    costs = load_protocol(REPO_PROTOCOL).costs
    short_period_cell = {"fast": 5, "slow": 40, "trend": 100}
    long_period_cell = {"fast": 30, "slow": 40, "trend": 100}
    short = run_backtest(committed_strategy, short_period_cell, fixture_bars, costs)
    long = run_backtest(committed_strategy, long_period_cell, fixture_bars, costs)

    # A PRECONDITION, not coverage: `fast` is below `slow` in both cells, so it
    # cannot enter the warm-up under any reading of the rule and the two curves
    # are the same length for a structural reason. It cannot fail.
    assert len(short.equity) == len(long.equity)

    # Also a precondition. If neither cell ever traded, both finals would be the
    # starting equity and the assertion below would go red for a reason that has
    # nothing to do with whether `fast` reached anything. The fixture gives this
    # cell 153 bars to trade on: 253 - max(40, 100).
    assert short.fills or long.fills

    assert float(short.equity.iloc[-1]) != float(long.equity.iloc[-1])


def test_the_protocol_costs_reach_the_fills_and_reduce_the_result(
    committed_strategy: StrategyDef, fixture_bars: pd.DataFrame
) -> None:
    # Without this, every other end-to-end assertion passes unchanged if
    # `load_protocol(...).costs` returned a model of all zeroes - so the file
    # would claim to run "with the real protocol costs" and nothing would carry
    # it. A cell that trades is required, hence the fills precondition.
    #
    # The direction is the only part hand-derivable here: a buyer crosses half
    # the spread upward and pays commission out of cash, so a charged run cannot
    # end above a free one. The size of the gap depends on the fixture and is
    # deliberately not asserted.
    charged = load_protocol(REPO_PROTOCOL).costs
    free = CostModel(
        spread_frac=0.0,
        slippage_frac=0.0,
        commission_frac=0.0,
        commission_min_flat=0.0,
        fx_conversion_frac=0.0,
    )
    assert charged.spread_frac > 0.0
    cell = {"fast": 30, "slow": 40, "trend": 100}
    with_costs = run_backtest(committed_strategy, cell, fixture_bars, charged)
    without_costs = run_backtest(committed_strategy, cell, fixture_bars, free)
    assert with_costs.fills
    assert float(with_costs.equity.iloc[-1]) < float(without_costs.equity.iloc[-1])


def test_a_second_pass_over_a_cell_reproduces_it_exactly(
    committed_strategy: StrategyDef,
    fixture_bars: pd.DataFrame,
    whole_grid_run: list[tuple[dict[str, int], BacktestResult]],
) -> None:
    # Determinism across the seam, not just inside the engine: the same config
    # file, re-expanded and re-run, gives the same curve.
    costs = load_protocol(REPO_PROTOCOL).costs
    cell, first = whole_grid_run[0]
    reexpanded = expand_grid(committed_strategy.search)[0]
    again = run_backtest(committed_strategy, reexpanded, fixture_bars, costs)
    assert again.params == cell
    # check_exact, because the default for floats is rtol=1e-5 - a tolerance of
    # about 0.10 on an equity near 10,000, which is not what "exactly" means.
    # `tests/test_engine.py` asserts its determinism the same way.
    pd.testing.assert_series_equal(again.equity, first.equity, check_exact=True)

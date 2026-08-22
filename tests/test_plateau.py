"""Tests for plateau scoring, the edge check, and the degeneracy check.

EVERY EXPECTED NUMBER HERE IS HAND-DERIVED FROM THE THREE RULES BELOW, which are
stated once so a reader checking the arithmetic never has to open `src/`. The
neighbourhood tables are written out cell by cell rather than computed, because a
table computed the same way as the implementation would agree with a wrong
implementation.

**Rule 1 - what a neighbourhood is.** Each parameter's axis is its ascending
distinct values. A cell's POSITION on an axis is its index in that list, counting
from 0. Two cells are neighbours when their positions differ by at most `radius`
on EVERY axis at once. A cell is its own neighbour, because the distance from a
cell to itself is 0 and 0 <= radius always. So on one axis with radius 1 an
interior cell has three neighbours, not two, and an end cell has two.

Positions, not values: with an axis of 1, 2, 100 the cells 2 and 100 are adjacent
(positions 1 and 2) even though 98 apart in bars. That is what "radius in GRID
STEPS" means.

**Rule 2 - what the score is.** The median of the neighbourhood's metric values.
For an even count the median is the mean of the two middle values, so [0, 1]
medians to 0.5. A NaN metric contributes to no median and is not counted;
`neighbour_count` reports how many cells actually contributed, so [1, NaN, 3]
over three cells gives median 2 and count 2.

Where a test does not pass `radius`, the default is 1.

**Rule 3 - what degenerate means.** At least 0.8 of a neighbourhood carries the
same `trade_fingerprint` as the cell being judged. The cell counts itself. So 4
of 5 is degenerate (0.8 >= 0.8) and 3 of 5 is not (0.6 < 0.8).

WHY THE SPIKE-VERSUS-PLATEAU TEST IS THE ONE THAT MATTERS. `plateau_score` exists
to make an isolated best cell lose to a broad mediocre region. The test that
carries that claim builds both on one axis and asserts the ordering flips: the
spike's own metric is the highest in the grid and its plateau score is the
lowest. Everything else here is a property of the arithmetic; that one is the
property the project's rules ask for.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from strategylab.search.plateau import (
    DEGENERATE_SHARE,
    is_degenerate,
    is_edge_winner,
    plateau_score,
)


def one_axis(values: list[int], sharpe: list[float]) -> pd.DataFrame:
    """Build a one-parameter results frame with the given metric per cell."""
    return pd.DataFrame({"p": values, "sharpe": sharpe})


def fingerprinted(values: list[int], fingerprints: list[str]) -> pd.DataFrame:
    """Build a one-parameter results frame carrying trade fingerprints."""
    return pd.DataFrame(
        {"p": values, "sharpe": [0.0] * len(values), "trade_fingerprint": fingerprints}
    )


# --------------------------------------------------------------------------
# plateau_score
# --------------------------------------------------------------------------


def test_an_isolated_spike_loses_to_a_broad_region() -> None:
    # THE CLAIM THE MODULE EXISTS FOR. Two grids on p = 1..5, radius 1.
    #
    #   spike   sharpe [0, 0, 9, 0, 0]      plateau sharpe [0, 1, 1, 1, 0]
    #   p=3 neighbourhood {2,3,4} = [0,9,0] p=3 neighbourhood {2,3,4} = [1,1,1]
    #   median 0                            median 1
    #
    # The spike's own metric is 9, the highest anywhere in either grid; the
    # broad region's best own metric is 1. After scoring, 0 < 1: the ordering
    # flips, which is the whole point.
    spike = plateau_score(one_axis([1, 2, 3, 4, 5], [0.0, 0.0, 9.0, 0.0, 0.0]))
    broad = plateau_score(one_axis([1, 2, 3, 4, 5], [0.0, 1.0, 1.0, 1.0, 0.0]))

    # The two lines below are preconditions rather than coverage: they let a
    # reader see the spike really is the higher peak before the scores are
    # compared. Saying they "cannot fail" was too strong - they read the
    # RETURNED frame, so they would catch a `plateau_score` that dropped or
    # altered the metric column. Narrow, but not nothing.
    assert spike["sharpe"].max() == 9.0
    assert broad["sharpe"].max() == 1.0
    assert spike.loc[spike["p"] == 3, "plateau_score"].item() == 0.0
    assert broad.loc[broad["p"] == 3, "plateau_score"].item() == 1.0
    assert spike["plateau_score"].max() < broad["plateau_score"].max()


def test_each_cell_scores_the_median_of_its_neighbourhood() -> None:
    # p = 1..5, sharpe [0, 0, 9, 0, 0], radius 1. Neighbourhoods and medians:
    #   p=1 {1,2}   -> [0,0]   -> 0
    #   p=2 {1,2,3} -> [0,0,9] -> 0
    #   p=3 {2,3,4} -> [0,9,0] -> 0
    #   p=4 {3,4,5} -> [9,0,0] -> 0
    #   p=5 {4,5}   -> [0,0]   -> 0
    scored = plateau_score(one_axis([1, 2, 3, 4, 5], [0.0, 0.0, 9.0, 0.0, 0.0]))
    assert scored["plateau_score"].tolist() == [0.0, 0.0, 0.0, 0.0, 0.0]


def test_a_boundary_cell_is_scored_over_the_neighbours_it_has() -> None:
    # p = 1..5, sharpe [0, 1, 1, 1, 0], radius 1.
    #   p=1 {1,2}   -> [0,1]   -> median 0.5, count 2
    #   p=2 {1,2,3} -> [0,1,1] -> median 1,   count 3
    #   p=3 {2,3,4} -> [1,1,1] -> median 1,   count 3
    #   p=4 {3,4,5} -> [1,1,0] -> median 1,   count 3
    #   p=5 {4,5}   -> [1,0]   -> median 0.5, count 2
    # The two 0.5s are the even-count case: the mean of the two middle values.
    scored = plateau_score(one_axis([1, 2, 3, 4, 5], [0.0, 1.0, 1.0, 1.0, 0.0]))
    assert scored["plateau_score"].tolist() == [0.5, 1.0, 1.0, 1.0, 0.5]
    assert scored["neighbour_count"].tolist() == [2, 3, 3, 3, 2]


def test_the_score_is_a_median_and_not_a_mean() -> None:
    # p = 1..5, sharpe [0, 0, 0, 0, 10], radius 1.
    #   p=4 {3,4,5} -> [0, 0, 10] -> median 0, mean 10/3 = 3.333...
    # A mean would let the single outlier carry the neighbourhood.
    scored = plateau_score(one_axis([1, 2, 3, 4, 5], [0.0, 0.0, 0.0, 0.0, 10.0]))
    assert scored.loc[scored["p"] == 4, "plateau_score"].item() == 0.0


def test_a_radius_of_zero_scores_every_cell_by_itself() -> None:
    # Positions differ by 0 only from themselves, so each neighbourhood is the
    # one cell: the median of a single value is that value, and the count is 1.
    scored = plateau_score(one_axis([1, 2, 3], [4.0, 5.0, 6.0]), radius=0)
    assert scored["plateau_score"].tolist() == [4.0, 5.0, 6.0]
    assert scored["neighbour_count"].tolist() == [1, 1, 1]


def test_a_radius_wider_than_the_grid_makes_every_cell_one_neighbourhood() -> None:
    # p = 1..3 with radius 5: every position differs by at most 2, so all three
    # cells neighbour each other. Median of [1, 2, 6] is 2, count 3, for all.
    scored = plateau_score(one_axis([1, 2, 3], [1.0, 2.0, 6.0]), radius=5)
    assert scored["plateau_score"].tolist() == [2.0, 2.0, 2.0]
    assert scored["neighbour_count"].tolist() == [3, 3, 3]


def test_the_radius_counts_grid_steps_and_not_parameter_units() -> None:
    # Axis 1, 2, 100 - positions 0, 1, 2. Radius 1, sharpe [1, 2, 3]:
    #   p=1   {1,2}     -> [1,2]   -> 1.5
    #   p=2   {1,2,100} -> [1,2,3] -> 2
    #   p=100 {2,100}   -> [2,3]   -> 2.5
    # By VALUE, 2 and 100 are 98 apart and would never be neighbours. By
    # position they are adjacent, and that is what the contract promises.
    scored = plateau_score(one_axis([1, 2, 100], [1.0, 2.0, 3.0]))
    assert scored["plateau_score"].tolist() == [1.5, 2.0, 2.5]
    # Counts 2, 3, 2 - the two end cells have one neighbour each besides
    # themselves, the middle one has two. Position 2 is an END, not an interior
    # cell, however far away in bars it sits.
    assert scored["neighbour_count"].tolist() == [2, 3, 2]


def test_a_failed_cell_is_excluded_from_medians_but_visible_in_the_count() -> None:
    # p = 1..3, sharpe [1, NaN, 3], radius 1.
    #   p=1 {1,2}   -> finite [1]   -> median 1, count 1
    #   p=2 {1,2,3} -> finite [1,3] -> median 2, count 2
    #   p=3 {2,3}   -> finite [3]   -> median 3, count 1
    # The NaN cell still scores, from its two finite neighbours.
    scored = plateau_score(one_axis([1, 2, 3], [1.0, math.nan, 3.0]))
    assert scored["plateau_score"].tolist() == [1.0, 2.0, 3.0]
    assert scored["neighbour_count"].tolist() == [1, 2, 1]


def test_a_neighbourhood_with_no_finite_metric_scores_nan_rather_than_raising() -> None:
    # Both cells failed, so there is nothing to take a median of. One
    # unscoreable region is not a reason to refuse the whole surface.
    scored = plateau_score(one_axis([1, 2], [math.nan, math.nan]))
    assert all(math.isnan(value) for value in scored["plateau_score"])
    assert scored["neighbour_count"].tolist() == [0, 0]


def test_a_two_dimensional_neighbourhood_is_bounded_on_every_axis_at_once() -> None:
    # a in {1,2,3}, b in {10,20,30} - a 3x3 grid, radius 1. A neighbourhood is
    # the cells within one position on BOTH axes, so it is a square block:
    #   corner (1,10)   -> a in {1,2},   b in {10,20}    -> 2*2 = 4
    #   edge   (2,10)   -> a in {1,2,3}, b in {10,20}    -> 3*2 = 6
    #   centre (2,20)   -> a in {1,2,3}, b in {10,20,30} -> 3*3 = 9
    frame = pd.DataFrame(
        {
            "a": [1, 1, 1, 2, 2, 2, 3, 3, 3],
            "b": [10, 20, 30, 10, 20, 30, 10, 20, 30],
            "sharpe": [1.0] * 9,
        }
    )
    scored = plateau_score(frame)
    counts = {(row.a, row.b): row.neighbour_count for row in scored.itertuples()}
    assert counts[(1, 10)] == 4
    assert counts[(2, 10)] == 6
    assert counts[(2, 20)] == 9


def test_the_axis_is_sorted_rather_than_taken_in_row_order() -> None:
    # Every other frame in this file is supplied already ascending, so an
    # implementation that used row order instead of sorting would stay green.
    # Here the rows arrive 3, 1, 2 with sharpe 30, 10, 20 - so row order and
    # ascending order disagree. By ascending value the neighbourhoods are:
    #   p=1 {1,2}   -> [10,20]    -> 15
    #   p=2 {1,2,3} -> [10,20,30] -> 20
    #   p=3 {2,3}   -> [20,30]    -> 25
    # and the frame keeps its original row order, so the scores come back in
    # the order 3, 1, 2 -> 25, 15, 20. Row-order positions would instead make
    # p=3 and p=1 adjacent, giving p=3 a neighbourhood of [30,10] -> 20.
    frame = pd.DataFrame({"p": [3, 1, 2], "sharpe": [30.0, 10.0, 20.0]})
    scored = plateau_score(frame)
    assert scored["plateau_score"].tolist() == [25.0, 15.0, 20.0]
    assert scored["neighbour_count"].tolist() == [2, 2, 3]


def test_the_input_frame_is_not_modified() -> None:
    frame = one_axis([1, 2, 3], [1.0, 2.0, 3.0])
    plateau_score(frame)
    assert list(frame.columns) == ["p", "sharpe"]


def test_the_added_columns_are_not_mistaken_for_parameters_on_a_second_pass() -> None:
    # Scoring an already-scored frame must treat `plateau_score` and
    # `neighbour_count` as output, not as two more axes. If they became axes,
    # the frame would look like a 3-axis grid holding 3 rows where its own
    # values span 3 x 3 x 3 = 27 cells, and the completeness check would refuse
    # it - so the failure is a raised ValueError, not a quietly wrong count.
    once = plateau_score(one_axis([1, 2, 3], [1.0, 2.0, 3.0]))
    twice = plateau_score(once)
    assert twice["neighbour_count"].tolist() == [2, 3, 2]


def test_a_frame_carrying_the_other_result_columns_still_finds_its_parameter() -> None:
    # `max_drawdown_pct`, `trade_count` and `trade_fingerprint` are result
    # columns, not axes. With only `p` as a parameter the counts are 2, 3, 2.
    frame = pd.DataFrame(
        {
            "p": [1, 2, 3],
            "sharpe": [1.0, 2.0, 3.0],
            "max_drawdown_pct": [5.0, 6.0, 7.0],
            "trade_count": [2, 3, 4],
            "trade_fingerprint": ["a", "b", "c"],
        }
    )
    assert plateau_score(frame)["neighbour_count"].tolist() == [2, 3, 2]


@pytest.mark.parametrize("radius", [-1, -5])
def test_a_negative_radius_is_refused(radius: int) -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        plateau_score(one_axis([1, 2], [1.0, 2.0]), radius=radius)


def test_an_empty_frame_is_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        plateau_score(pd.DataFrame({"p": [], "sharpe": []}))


def test_a_frame_without_the_metric_column_is_refused() -> None:
    with pytest.raises(ValueError, match="lacks the 'sharpe' column"):
        plateau_score(pd.DataFrame({"p": [1, 2], "sortino": [1.0, 2.0]}))


def test_a_frame_with_no_parameter_column_is_refused() -> None:
    # Every column is a known result column, so there is no axis to walk.
    with pytest.raises(ValueError, match="no parameter column"):
        plateau_score(pd.DataFrame({"sharpe": [1.0, 2.0], "trade_count": [1, 2]}))


def test_two_rows_for_one_cell_are_refused() -> None:
    with pytest.raises(ValueError, match="more than one row"):
        plateau_score(one_axis([1, 2, 2], [1.0, 2.0, 3.0]))


def test_a_grid_with_a_row_missing_is_refused() -> None:
    # a in {1,2} and b in {10,20} span four cells; three rows are given. A
    # dropped row would make the cells around it neighbours across the hole.
    frame = pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 10], "sharpe": [1.0, 2.0, 3.0]})
    with pytest.raises(ValueError, match="complete grid of 4"):
        plateau_score(frame)


def test_a_whole_missing_level_is_accepted_which_is_the_documented_limit() -> None:
    # NOT a passing behaviour to rely on - it is the hole the contract admits.
    # Removing every row at one value of `a` leaves a complete smaller grid,
    # indistinguishable from a search that only declared two values of `a`.
    # Detecting it needs the declared search_spec, which this function is not
    # given. The test exists so the limit is visible rather than discovered.
    frame = pd.DataFrame({"a": [1, 1, 3, 3], "b": [10, 20, 10, 20], "sharpe": [1.0] * 4})
    assert plateau_score(frame)["neighbour_count"].tolist() == [4, 4, 4, 4]


# --------------------------------------------------------------------------
# is_edge_winner
# --------------------------------------------------------------------------

SPEC: dict[str, dict[str, float]] = {"fast": {"min": 5, "max": 30, "step": 5}}
# The axis this generates: 5, 10, 15, 20, 25, 30.


@pytest.mark.parametrize("value", [5, 30])
def test_a_cell_at_either_end_of_its_range_is_an_edge_winner(value: int) -> None:
    assert is_edge_winner({"fast": value}, SPEC) is True


@pytest.mark.parametrize("value", [10, 15, 20, 25])
def test_an_interior_cell_is_not_an_edge_winner(value: int) -> None:
    assert is_edge_winner({"fast": value}, SPEC) is False


def test_the_edge_is_the_highest_value_the_grid_reaches_not_the_declared_max() -> None:
    # {min 1, max 10, step 4} generates 1, 5, 9 - it never reaches 10, because
    # 10 is not on a step boundary from 1. So 9 is the top edge. Comparing
    # against the declared max would report this grid as having no upper edge,
    # and a wall would be traded as an optimum.
    spec: dict[str, dict[str, float]] = {"p": {"min": 1, "max": 10, "step": 4}}
    assert is_edge_winner({"p": 9}, spec) is True
    assert is_edge_winner({"p": 5}, spec) is False


def test_a_single_valued_range_is_both_ends_at_once() -> None:
    # min == max generates one cell, which is simultaneously the lowest and
    # highest value the axis reaches. A parameter that was not really searched
    # cannot testify that its winner is interior.
    spec: dict[str, dict[str, float]] = {"p": {"min": 7, "max": 7, "step": 1}}
    assert is_edge_winner({"p": 7}, spec) is True


def test_a_cell_on_the_edge_of_any_one_axis_is_an_edge_winner() -> None:
    # Interior on `fast`, at the bottom of `slow`. One wall is enough.
    spec: dict[str, dict[str, float]] = {
        "fast": {"min": 5, "max": 30, "step": 5},
        "slow": {"min": 40, "max": 120, "step": 20},
    }
    assert is_edge_winner({"fast": 15, "slow": 40}, spec) is True
    assert is_edge_winner({"fast": 15, "slow": 80}, spec) is False


def test_a_cell_naming_an_undeclared_parameter_is_refused() -> None:
    with pytest.raises(ValueError, match="do not declare"):
        is_edge_winner({"fast": 10, "trend": 100}, SPEC)


def test_a_cell_omitting_a_declared_parameter_is_refused() -> None:
    spec: dict[str, dict[str, float]] = {
        "fast": {"min": 5, "max": 30, "step": 5},
        "slow": {"min": 40, "max": 120, "step": 20},
    }
    with pytest.raises(ValueError, match="omits"):
        is_edge_winner({"fast": 10}, spec)


@pytest.mark.parametrize("value", [7, 0, 35])
def test_a_value_that_is_not_on_the_axis_is_refused(value: int) -> None:
    # 7 lies between two cells, 0 below the range, 35 above it. "Is this at the
    # edge" has no answer for a value that is not on the grid at all.
    with pytest.raises(ValueError, match="not a value that range generates"):
        is_edge_winner({"fast": value}, SPEC)


def test_an_off_axis_value_is_refused_even_when_an_earlier_parameter_is_on_an_edge() -> None:
    # THE ORDER-DEPENDENCE BUG. `fast=5` sits on its lower edge, so an
    # implementation that reports an edge as soon as it finds one never looks
    # at `slow` and returns True for a cell carrying a value no range
    # generates. The same malformed cell then raises or does not depending on
    # which parameter happens to come first, which is not a contract at all.
    spec: dict[str, dict[str, float]] = {
        "fast": {"min": 5, "max": 30, "step": 5},
        "slow": {"min": 40, "max": 120, "step": 20},
    }
    with pytest.raises(ValueError, match="not a value that range generates"):
        is_edge_winner({"fast": 5, "slow": 999}, spec)
    # And with the parameters the other way round, which already worked.
    with pytest.raises(ValueError, match="not a value that range generates"):
        is_edge_winner({"fast": 10, "slow": 999}, spec)


def test_a_malformed_range_propagates_the_grid_refusal_unchanged() -> None:
    # The axis is built by calling expand_grid, so its refusals are this
    # function's too.
    spec: dict[str, dict[str, float]] = {"p": {"min": 1, "max": 10, "step": 0}}
    with pytest.raises(ValueError, match="at least 1 bar"):
        is_edge_winner({"p": 1}, spec)


# --------------------------------------------------------------------------
# is_degenerate
# --------------------------------------------------------------------------


def test_a_neighbourhood_trading_identically_is_degenerate() -> None:
    # p = 1..5, every cell fingerprint "X", radius 1. p=3's neighbourhood is
    # {2,3,4}: 3 of 3 match, 3/3 = 1.0 >= 0.8.
    frame = fingerprinted([1, 2, 3, 4, 5], ["X", "X", "X", "X", "X"])
    assert is_degenerate(frame, {"p": 3}) is True


def test_a_neighbourhood_trading_differently_is_not_degenerate() -> None:
    # p=3's neighbourhood {2,3,4} carries B, C, D. Only C matches C, so
    # 1/3 = 0.333... < 0.8.
    frame = fingerprinted([1, 2, 3, 4, 5], ["A", "B", "C", "D", "E"])
    assert is_degenerate(frame, {"p": 3}) is False


def test_the_share_threshold_is_inclusive_at_four_fifths() -> None:
    # radius 2 makes p=3's neighbourhood all five cells (positions 0..4 all
    # within 2 of position 2). Fingerprints X,X,X,X,Y: 4 of 5 match X, and
    # 4/5 = 0.8, which is >= 0.8.
    frame = fingerprinted([1, 2, 3, 4, 5], ["X", "X", "X", "X", "Y"])
    assert is_degenerate(frame, {"p": 3}, radius=2) is True
    assert DEGENERATE_SHARE == 0.8


def test_three_of_five_is_below_the_threshold() -> None:
    # Same neighbourhood of five. X,X,X,Y,Z: 3 of 5 match, 3/5 = 0.6 < 0.8.
    frame = fingerprinted([1, 2, 3, 4, 5], ["X", "X", "X", "Y", "Z"])
    assert is_degenerate(frame, {"p": 3}, radius=2) is False


def test_three_of_four_is_below_the_threshold() -> None:
    # Brackets the threshold from above more tightly than 3-of-5 does. A 4-cell
    # axis at radius 3 is one neighbourhood of all four; X,X,X,Y gives 3/4 =
    # 0.75, which must NOT be degenerate. Together with the 4-of-5 case at
    # exactly 0.8 this pins the threshold to (0.75, 0.8] rather than (0.6, 0.8].
    frame = fingerprinted([1, 2, 3, 4], ["X", "X", "X", "Y"])
    assert is_degenerate(frame, {"p": 2}, radius=3) is False


def test_a_neighbourhood_of_cells_that_never_traded_is_degenerate() -> None:
    # Every cell shares the empty-trade-set fingerprint. Parameters producing
    # no trades at all are doing nothing, which is what this flags.
    frame = fingerprinted([1, 2, 3], ["", "", ""])
    assert is_degenerate(frame, {"p": 2}) is True


def test_a_radius_of_zero_makes_a_cell_trivially_degenerate() -> None:
    # The neighbourhood is the cell alone: 1 of 1 matches, 1.0 >= 0.8. Honest
    # rather than useful - a single cell is no evidence that anything varies.
    frame = fingerprinted([1, 2, 3], ["A", "B", "C"])
    assert is_degenerate(frame, {"p": 2}, radius=0) is True


def test_a_non_string_fingerprint_is_refused() -> None:
    # A NaN fingerprint is equal to nothing, itself included. Without this
    # check the cell matched NONE of its own neighbourhood, so a neighbourhood
    # of one reported NOT degenerate - the exact inverse of what the contract
    # says a single cell does.
    frame = pd.DataFrame({"p": [1], "sharpe": [1.0], "trade_fingerprint": [math.nan]})
    with pytest.raises(ValueError, match="must be a string"):
        is_degenerate(frame, {"p": 1}, radius=0)


def test_a_frame_without_fingerprints_is_refused() -> None:
    # A trade COUNT would call two different sets of the same size identical,
    # which is the comparison this check exists to avoid.
    frame = pd.DataFrame({"p": [1, 2], "sharpe": [1.0, 2.0], "trade_count": [3, 3]})
    with pytest.raises(ValueError, match="trade_fingerprint"):
        is_degenerate(frame, {"p": 1})


def test_a_cell_absent_from_the_frame_is_refused() -> None:
    frame = fingerprinted([1, 2, 3], ["A", "B", "C"])
    with pytest.raises(ValueError, match="not present in results"):
        is_degenerate(frame, {"p": 9})


def test_a_cell_naming_the_wrong_parameters_is_refused() -> None:
    frame = fingerprinted([1, 2, 3], ["A", "B", "C"])
    with pytest.raises(ValueError, match="a value for every one of them"):
        is_degenerate(frame, {"q": 1})


def test_a_negative_radius_is_refused_by_the_degeneracy_check() -> None:
    frame = fingerprinted([1, 2, 3], ["A", "B", "C"])
    with pytest.raises(ValueError, match="cannot be negative"):
        is_degenerate(frame, {"p": 1}, radius=-1)


def test_an_incomplete_grid_is_refused_by_the_degeneracy_check() -> None:
    frame = pd.DataFrame(
        {
            "a": [1, 1, 2],
            "b": [10, 20, 10],
            "sharpe": [1.0, 2.0, 3.0],
            "trade_fingerprint": ["A", "B", "C"],
        }
    )
    with pytest.raises(ValueError, match="complete grid of 4"):
        is_degenerate(frame, {"a": 1, "b": 10})

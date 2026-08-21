"""Tests for the performance metrics.

Every expected value here is derived by hand in the comment beside it, from the
formula in the module's own docstring. None was taken from what the
implementation printed — a Sharpe ratio is always plausible, so a test that
records the implementation's output tests nothing but its stability.

The cases are chosen to come out as exact closed forms wherever possible, so the
arithmetic can be checked by reading rather than by running.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from strategylab.backtest.metrics import TRADING_DAYS_PER_YEAR, max_drawdown, sharpe


def series(values: list[float]) -> pd.Series:
    """Build a float series indexed by consecutive business days.

    Args:
        values: The values, in order.

    Returns:
        The series, indexed so that it looks like the daily data the metrics
        will really see.
    """
    index = pd.bdate_range("2020-01-01", periods=len(values), name="date")
    return pd.Series(values, index=index, dtype="float64")


# --------------------------------------------------------------------------
# Sharpe. The headline case is chosen to be an exact square root.
# --------------------------------------------------------------------------


def test_sharpe_of_a_two_point_series_is_the_hand_computed_value() -> None:
    # returns = [0.01, 0.03]
    #   mean            = (0.01 + 0.03) / 2                       = 0.02
    #   sample variance = ((0.01-0.02)^2 + (0.03-0.02)^2) / (2-1) = 0.0002
    #   sample sd       = sqrt(0.0002)                            = 0.01 * sqrt(2)
    #   mean / sd       = 0.02 / (0.01 * sqrt(2)) = 2 / sqrt(2)   = sqrt(2)
    # Annualising at 1 period per year multiplies by sqrt(1) = 1, so the whole
    # answer is exactly sqrt(2) and no decimal is being copied from anywhere.
    assert sharpe(series([0.01, 0.03]), periods_per_year=1) == pytest.approx(
        math.sqrt(2), rel=1e-12
    )


def test_sharpe_annualises_by_the_square_root_of_the_period_count() -> None:
    # Same series, so the unannualised ratio is sqrt(2). At 252 periods the
    # answer is sqrt(2) * sqrt(252) = sqrt(504) = 22.449944320643652.
    # The annualisation must be sqrt(n), not n: those differ by a factor of
    # sqrt(252) ~ 15.87, the gap between a Sharpe of 1.4 and one of 22, and only
    # one of them looks wrong at a glance. Pinning the value to sqrt(504)
    # already excludes sqrt(2)*252 = 356.4, so no second assertion is needed and
    # one would not be able to fail independently anyway.
    assert sharpe(series([0.01, 0.03]), periods_per_year=252) == pytest.approx(
        math.sqrt(504), rel=1e-12
    )


def test_the_default_period_count_is_252() -> None:
    # Pinned as a literal, not as TRADING_DAYS_PER_YEAR, so that changing the
    # constant cannot move both sides of the comparison together.
    assert TRADING_DAYS_PER_YEAR == 252
    # And the default must BE that, asserted against a hand-derived value rather
    # than against sharpe(..., periods_per_year=252). Comparing the default call
    # to the explicit one only checks the default is wired to the constant; it
    # holds whatever the constant is, so it cannot fail on its own.
    assert sharpe(series([0.01, 0.03])) == pytest.approx(math.sqrt(504), rel=1e-12)


def test_sharpe_uses_the_sample_standard_deviation_not_the_population_one() -> None:
    # This is the one that silently inflates every result. For n = 2:
    #   sample sd     divides by n-1 = 1  -> sqrt(0.0002)  = 0.014142...
    #   population sd divides by n   = 2  -> sqrt(0.0001)  = 0.01
    # So the population form gives 0.02 / 0.01 = 2.0, against sqrt(2) = 1.414.
    # A wrong-by-41% Sharpe is entirely plausible-looking on its own.
    assert sharpe(series([0.01, 0.03]), periods_per_year=1) == pytest.approx(
        math.sqrt(2), rel=1e-12
    )


def test_the_divisor_is_n_minus_one_and_not_a_hardcoded_one() -> None:
    # Every other Sharpe case in this file uses two observations, and at n = 2
    # the divisor n-1 IS 1 - so "divide by n-1" and "divide by the literal 1"
    # are the same operation and the whole file passes either. An auditor found
    # that; mutation testing could not, because both forms are the same code
    # path at n = 2.
    #
    # Three observations separate all three candidates. returns = [0.01, 0.02,
    # 0.03], mean 0.02, deviations -0.01 / 0.00 / +0.01, sum of squares 0.0002:
    #
    #   divisor n-1 = 2  ->  sd = sqrt(0.0001)     = 0.01       ->  0.02/sd = 2.0
    #   divisor 1        ->  sd = sqrt(0.0002)     = 0.014142   ->  0.02/sd = sqrt(2)
    #   divisor n   = 3  ->  sd = sqrt(0.00006667) = 0.0081650  ->  0.02/sd = sqrt(6)
    #
    # Exactly 2.0, and the two wrong answers are named so the exclusion is
    # readable rather than implied.
    value = sharpe(series([0.01, 0.02, 0.03]), periods_per_year=1)
    assert value == pytest.approx(2.0, rel=1e-12)
    assert value != pytest.approx(math.sqrt(2), rel=1e-9)  # divisor hardcoded to 1
    assert value != pytest.approx(math.sqrt(6), rel=1e-9)  # population divisor


def test_a_zero_mean_series_has_a_sharpe_of_zero() -> None:
    # mean of [0.01, -0.01] is exactly 0, and 0 / anything is 0.
    assert sharpe(series([0.01, -0.01]), periods_per_year=1) == 0.0


def test_a_constant_series_returns_zero_rather_than_dividing_by_zero() -> None:
    # Zero variance. The contract says 0.0: a constant series has no
    # risk-adjusted return to speak of rather than an infinite one.
    assert sharpe(series([0.01, 0.01, 0.01])) == 0.0
    # Including a constant series of zeros, where the numerator is zero too.
    assert sharpe(series([0.0, 0.0])) == 0.0


def test_a_negative_mean_gives_a_negative_sharpe() -> None:
    # Sign must survive. returns = [-0.01, -0.03] is the headline case negated,
    # so the answer is exactly -sqrt(2).
    assert sharpe(series([-0.01, -0.03]), periods_per_year=1) == pytest.approx(
        -math.sqrt(2), rel=1e-12
    )


def test_sharpe_is_scale_invariant_in_the_returns() -> None:
    # Doubling every return doubles both the mean and the standard deviation,
    # so the ratio is unchanged.
    #
    # What this does NOT establish, despite an earlier comment here claiming it:
    # it does not exclude a halved or squared denominator. Both sides go through
    # the same function, so any error that scales with the input cancels on both
    # and the equality still holds. It is a property test for homogeneity and
    # nothing more - the hand-derived cases above are what pin the value.
    base = sharpe(series([0.01, 0.03, -0.02, 0.005]))
    doubled = sharpe(series([0.02, 0.06, -0.04, 0.010]))
    assert base == pytest.approx(doubled, rel=1e-12)


@pytest.mark.parametrize("periods", [0, -1, -252])
def test_a_non_positive_period_count_is_refused(periods: int) -> None:
    with pytest.raises(ValueError, match="periods_per_year must be positive"):
        sharpe(series([0.01, 0.03]), periods_per_year=periods)


@pytest.mark.parametrize("values", [[], [0.01]])
def test_fewer_than_two_observations_is_refused(values: list[float]) -> None:
    # One observation has no sample standard deviation at all: dividing by
    # n - 1 = 0. Returning something anyway would mean inventing a number.
    with pytest.raises(ValueError, match="at least two observations"):
        sharpe(series(values))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_return_is_refused(bad: float) -> None:
    # A NaN Sharpe compares False against the pass bar, so the candidate fails
    # validation for a data reason while looking as though it failed on merit.
    with pytest.raises(ValueError, match="non-finite"):
        sharpe(series([0.01, bad, 0.02]))


# --------------------------------------------------------------------------
# Maximum drawdown.
# --------------------------------------------------------------------------


def test_max_drawdown_is_measured_against_the_running_peak() -> None:
    # equity        100    120    90     150
    # running peak  100    120    120    150
    # drawdown %      0      0    25       0    -> 25.0
    # Measured from 120, the peak at the time, not from the 100 it started at.
    assert max_drawdown(series([100.0, 120.0, 90.0, 150.0])) == pytest.approx(25.0, rel=1e-12)


def test_the_documented_example_holds() -> None:
    # The docstring says a fall from 100 to 75 returns 25.0. (100-75)/100 = 25%.
    assert max_drawdown(series([100.0, 75.0])) == pytest.approx(25.0, rel=1e-12)


def test_a_curve_that_never_declines_has_no_drawdown() -> None:
    assert max_drawdown(series([100.0, 110.0, 120.0])) == 0.0
    # A flat curve has not declined either.
    assert max_drawdown(series([100.0, 100.0, 100.0])) == 0.0


def test_a_round_trip_still_reports_the_trough() -> None:
    # Doubling and then halving ends where it began, and lost half of what it
    # had on the way. Reporting 0.0 because start equals end is the flattering
    # error this is here to exclude.
    assert max_drawdown(series([100.0, 200.0, 100.0])) == pytest.approx(50.0, rel=1e-12)


def test_the_deepest_of_several_drawdowns_wins() -> None:
    # equity        100   80    100   60    100
    # running peak  100   100   100   100   100
    # drawdown %      0   20      0   40      0   -> 40.0
    assert max_drawdown(series([100.0, 80.0, 100.0, 60.0, 100.0])) == pytest.approx(40.0, rel=1e-12)


def test_the_result_is_a_percentage_not_a_fraction() -> None:
    # A hundredfold error that does not crash. 0.25 and 25.0 are both entirely
    # plausible readings of "a quarter", and protocol.yaml compares this against
    # max_drawdown_ceiling_pct = 25.0.
    value = max_drawdown(series([100.0, 75.0]))
    assert value == pytest.approx(25.0, rel=1e-12)
    assert value != pytest.approx(0.25, rel=1e-6)


def test_an_empty_curve_is_refused() -> None:
    with pytest.raises(ValueError, match="equity is empty"):
        max_drawdown(series([]))


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_non_positive_equity_value_is_refused(bad: float) -> None:
    # The drawdown is a fraction of the running peak, and a peak of zero has no
    # fraction. An account that reached zero is a different event from a
    # drawdown and should not be reported as one.
    with pytest.raises(ValueError, match="not positive"):
        max_drawdown(series([100.0, bad, 50.0]))


def test_a_nan_equity_value_is_refused() -> None:
    # pandas skips NaN in cummax and in max, so a gap in the curve would
    # silently produce a drawdown computed over the bars that happened to be
    # present.
    with pytest.raises(ValueError, match="NaN"):
        max_drawdown(series([100.0, float("nan"), 50.0]))


def test_an_infinite_equity_value_is_refused() -> None:
    with pytest.raises(ValueError, match="infinite"):
        max_drawdown(series([100.0, float("inf"), 50.0]))

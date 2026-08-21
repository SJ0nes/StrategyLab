"""Tests for the cost model.

Every expected number in this file was calculated by hand from the rates and
the mid, and the working is shown beside it. None of it was produced by running
the implementation and pasting the output, which would only assert that the code
does what it already does.

The invariant tests matter more than the worked examples. At a mid of 100.00 a
rate expressed as a percentage and the same rate applied to the mid are
numerically identical, so an implementation that never multiplies by the mid
reproduces every figure of the canonical example exactly. The parameterised
mids and the round-trip invariant are what catch that.
"""

from __future__ import annotations

import pytest

from strategylab.backtest.costs import CostModel, apply_costs
from strategylab.core.types import Side

# The protocol's rates, as fractions - which is what CostModel carries.
# protocol.yaml stores 0.05 / 0.03 / 0.05 as percentages; load_protocol divides
# by 100 once. tests/test_protocol.py asserts that conversion.
REALISTIC = CostModel(
    spread_frac=0.0003,  # 0.03%
    slippage_frac=0.0005,  # 0.05%
    commission_frac=0.0005,  # 0.05%
)

FREE = CostModel(spread_frac=0.0, slippage_frac=0.0, commission_frac=0.0)


# --------------------------------------------------------------------------
# The canonical worked example, from .claude/skills/cost-arithmetic.
#
#   mid 100.00, commission 0.05%, spread 0.03%, slippage 0.05%, quantity 1
#
#   half-spread = 100.00 * 0.0003 / 2 = 0.030 / 2 = 0.015
#   slippage    = 100.00 * 0.0005     = 0.050
#   impact/side = 0.015 + 0.050       = 0.065
#
#   BUY  executed = 100.00 + 0.065 = 100.065
#   SELL executed = 100.00 - 0.065 =  99.935
#
#   commission BUY  = 0.0005 * 100.065 = 0.0500325
#   commission SELL = 0.0005 *  99.935 = 0.0499675
#
# Note the per-leg commissions are NOT equal. The skill prints both as 0.0500
# because it rounds to 4dp. Asserting 0.0500 to 4dp would pass against an
# implementation charging commission on the MID notional, which the contract
# forbids - so these assert the exact values instead.
# --------------------------------------------------------------------------


def test_buy_executed_price() -> None:
    fill = apply_costs(100.00, Side.BUY, REALISTIC)
    assert fill.executed_price == pytest.approx(100.065, rel=1e-12)


def test_sell_executed_price() -> None:
    fill = apply_costs(100.00, Side.SELL, REALISTIC)
    assert fill.executed_price == pytest.approx(99.935, rel=1e-12)


def test_buy_breakdown_is_itemised() -> None:
    fill = apply_costs(100.00, Side.BUY, REALISTIC)
    assert fill.costs.spread == pytest.approx(0.015, rel=1e-12)
    assert fill.costs.slippage == pytest.approx(0.050, rel=1e-12)
    assert fill.costs.commission == pytest.approx(0.0500325, rel=1e-12)
    assert fill.costs.fx == 0.0


def test_sell_breakdown_is_itemised() -> None:
    fill = apply_costs(100.00, Side.SELL, REALISTIC)
    assert fill.costs.spread == pytest.approx(0.015, rel=1e-12)
    assert fill.costs.slippage == pytest.approx(0.050, rel=1e-12)
    assert fill.costs.commission == pytest.approx(0.0499675, rel=1e-12)


def test_commission_is_charged_on_executed_not_mid_notional() -> None:
    # The discriminating test. Commission on the MID would give exactly 0.0500
    # for both legs; on the EXECUTED price the legs differ by 0.000065. A 4dp
    # assertion cannot tell these apart, so assert the difference directly.
    buy = apply_costs(100.00, Side.BUY, REALISTIC)
    sell = apply_costs(100.00, Side.SELL, REALISTIC)
    assert buy.costs.commission != sell.costs.commission
    assert buy.costs.commission - sell.costs.commission == pytest.approx(0.000065, rel=1e-9)
    # And neither is the mid-notional answer.
    assert buy.costs.commission != pytest.approx(0.05, rel=1e-12)


def test_round_trip_totals() -> None:
    # price impact  100.065 - 99.935           = 0.130
    # commission    0.0500325 + 0.0499675      = 0.100  (exactly)
    # total                                      0.230  = 0.23% of 100.00
    buy = apply_costs(100.00, Side.BUY, REALISTIC)
    sell = apply_costs(100.00, Side.SELL, REALISTIC)

    price_impact = buy.executed_price - sell.executed_price
    commission = buy.costs.commission + sell.costs.commission

    assert price_impact == pytest.approx(0.130, rel=1e-12)
    assert commission == pytest.approx(0.100, rel=1e-12)
    assert price_impact + commission == pytest.approx(0.230, rel=1e-12)


def test_total_executed_notional_is_exactly_twice_the_mid() -> None:
    # 100.065 + 99.935 = 200.000 exactly IN THE REALS, structurally, by the
    # symmetry of the price-impact term - which is what makes the round-trip
    # commission exact rather than a rounding coincidence. It is NOT exact in
    # IEEE-754: the two roundings are independent and need not cancel, so a
    # bare == here would be flaky. approx is the correct operator.
    buy = apply_costs(100.00, Side.BUY, REALISTIC)
    sell = apply_costs(100.00, Side.SELL, REALISTIC)
    assert buy.executed_price + sell.executed_price == pytest.approx(200.000, rel=1e-12)


# --------------------------------------------------------------------------
# A second worked example at a non-round mid, where percent and price units
# are no longer numerically identical.
#
#   mid 250.00, same rates, quantity 1
#
#   half-spread = 250.00 * 0.0003 / 2 = 0.075 / 2 = 0.0375
#   slippage    = 250.00 * 0.0005     = 0.1250
#   impact/side = 0.0375 + 0.1250     = 0.1625
#
#   BUY  executed = 250.00 + 0.1625 = 250.1625
#   SELL executed = 250.00 - 0.1625 = 249.8375
#
#   commission BUY  = 0.0005 * 250.1625 = 0.12508125
#   commission SELL = 0.0005 * 249.8375 = 0.12491875
#
# Here the per-leg commissions differ in the 4th decimal (0.1251 vs 0.1249),
# so this example alone would catch the mid-notional bug even at 4dp.
# --------------------------------------------------------------------------


def test_buy_at_non_round_mid() -> None:
    fill = apply_costs(250.00, Side.BUY, REALISTIC)
    assert fill.executed_price == pytest.approx(250.1625, rel=1e-12)
    assert fill.costs.spread == pytest.approx(0.0375, rel=1e-12)
    assert fill.costs.slippage == pytest.approx(0.1250, rel=1e-12)
    assert fill.costs.commission == pytest.approx(0.12508125, rel=1e-12)


def test_sell_at_non_round_mid() -> None:
    fill = apply_costs(250.00, Side.SELL, REALISTIC)
    assert fill.executed_price == pytest.approx(249.8375, rel=1e-12)
    assert fill.costs.commission == pytest.approx(0.12491875, rel=1e-12)


# --------------------------------------------------------------------------
# Invariants. These hold for every input and are the real protection.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mid", [100.00, 250.00, 37.42, 1.05, 4321.99])
def test_round_trip_is_always_0_23_percent_of_notional(mid: float) -> None:
    # Closed form, independent of the implementation:
    #   total% = spread% + 2*slippage% + 2*commission%
    #          = 0.03 + 2(0.05) + 2(0.05) = 0.23%
    # Scale-invariant in the mid. A units error, a missing multiply by the mid,
    # or a full spread charged where a half belongs all break this at once.
    buy = apply_costs(mid, Side.BUY, REALISTIC)
    sell = apply_costs(mid, Side.SELL, REALISTIC)
    total = (buy.executed_price - sell.executed_price) + (
        buy.costs.commission + sell.costs.commission
    )
    assert total / mid == pytest.approx(0.0023, rel=1e-12)


@pytest.mark.parametrize("mid", [100.00, 250.00, 37.42, 1.05, 4321.99])
def test_symmetry_about_the_mid(mid: float) -> None:
    # Guards the "commission is never folded into the executed price" rule.
    # Folding it in shifts the mean off the mid by about 3.25e-5 at mid 100,
    # so this must be a tight comparison - at 4dp it would not detect it.
    buy = apply_costs(mid, Side.BUY, REALISTIC)
    sell = apply_costs(mid, Side.SELL, REALISTIC)
    assert (buy.executed_price + sell.executed_price) / 2 == pytest.approx(mid, rel=1e-12)


@pytest.mark.parametrize("mid", [100.00, 250.00, 37.42, 1.05, 4321.99])
def test_direction_is_never_reversed(mid: float) -> None:
    # A sign error here produces a profitable-looking backtest out of nothing.
    assert apply_costs(mid, Side.BUY, REALISTIC).executed_price > mid
    assert apply_costs(mid, Side.SELL, REALISTIC).executed_price < mid


def test_a_buyer_crosses_half_the_spread_not_all_of_it() -> None:
    # Full-spread crossing would give 0.030 here rather than 0.015, and would
    # quietly halve every Sharpe in the study.
    fill = apply_costs(100.00, Side.BUY, REALISTIC)
    full_spread = 100.00 * REALISTIC.spread_frac
    assert fill.costs.spread == pytest.approx(full_spread / 2, rel=1e-12)
    assert fill.costs.spread == pytest.approx(0.015, rel=1e-12)


# 1.0 is excluded: it makes the assertion `one == one * 1.0`, a tautology.
@pytest.mark.parametrize("quantity", [2.0, 10.0, 137.5])
def test_cash_costs_scale_linearly_with_quantity(quantity: float) -> None:
    one = apply_costs(100.00, Side.BUY, REALISTIC, quantity=1.0)
    many = apply_costs(100.00, Side.BUY, REALISTIC, quantity=quantity)
    assert many.costs.commission == pytest.approx(one.costs.commission * quantity, rel=1e-12)


# 1.0 excluded for the same reason: it compares two identical calls.
@pytest.mark.parametrize("quantity", [2.0, 10.0, 137.5])
def test_executed_price_does_not_change_with_size(quantity: float) -> None:
    # The other half of linearity. Asserting only the cash half would pass
    # against an implementation that added a spurious size-dependent price
    # term. This model has no market impact, which is a stated limitation.
    one = apply_costs(100.00, Side.BUY, REALISTIC, quantity=1.0)
    many = apply_costs(100.00, Side.BUY, REALISTIC, quantity=quantity)
    assert many.executed_price == one.executed_price


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
@pytest.mark.parametrize("mid", [100.00, 250.00, 37.42])
def test_zero_cost_model_is_an_exact_identity(side: Side, mid: float) -> None:
    # "Not approximately - exactly." Bare == is correct here: with all rates
    # at zero the arithmetic is mid + 0, which is exact in floating point.
    fill = apply_costs(mid, side, FREE, quantity=3.0)
    assert fill.executed_price == mid
    assert fill.costs.spread == 0.0
    assert fill.costs.slippage == 0.0
    assert fill.costs.commission == 0.0
    assert fill.costs.fx == 0.0


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
@pytest.mark.parametrize("mid", [100.00, 250.00, 37.42, 1.05])
def test_no_breakdown_component_is_ever_negative(side: Side, mid: float) -> None:
    fill = apply_costs(mid, side, REALISTIC, quantity=2.0)
    assert fill.costs.spread >= 0
    assert fill.costs.slippage >= 0
    assert fill.costs.commission >= 0
    # fx is deliberately NOT asserted here: REALISTIC sets no fx rate, so fx
    # is structurally 0.0 and `fx >= 0` could not fail. It is covered against
    # a non-zero rate in test_fx_is_charged_on_executed_notional_and_kept_separate.


def test_commission_never_appears_in_the_executed_price() -> None:
    # Two models identical but for commission must produce the same executed
    # price. This is the direct statement of the rule.
    dear = CostModel(spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.01)
    cheap = CostModel(spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0)
    assert apply_costs(100.00, Side.BUY, dear).executed_price == (
        apply_costs(100.00, Side.BUY, cheap).executed_price
    )
    assert apply_costs(100.00, Side.BUY, dear).costs.commission > 0


# --------------------------------------------------------------------------
# The flat minimum and the FX charge.
# --------------------------------------------------------------------------


def test_flat_minimum_binds_on_a_small_position() -> None:
    # 1 unit at ~100 -> percentage commission 0.0500325, below the 3.00 floor,
    # so the floor is charged.
    model = CostModel(
        spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005, commission_min_flat=3.0
    )
    fill = apply_costs(100.00, Side.BUY, model, quantity=1.0)
    assert fill.costs.commission == 3.0


def test_percentage_commission_wins_on_a_large_position() -> None:
    # 1000 units at 100.065 -> notional 100065.00, commission 50.0325, which is
    # above the 3.00 floor.
    model = CostModel(
        spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005, commission_min_flat=3.0
    )
    fill = apply_costs(100.00, Side.BUY, model, quantity=1000.0)
    assert fill.costs.commission == pytest.approx(50.0325, rel=1e-12)


def test_flat_minimum_breaks_linearity_deliberately() -> None:
    # Documented non-linearity: below the floor, doubling size does not double
    # the cash cost. This is the whole reason the field exists.
    model = CostModel(
        spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005, commission_min_flat=3.0
    )
    one = apply_costs(100.00, Side.BUY, model, quantity=1.0)
    two = apply_costs(100.00, Side.BUY, model, quantity=2.0)
    assert two.costs.commission == one.costs.commission  # both floored at 3.00


def test_fx_is_charged_on_executed_notional_and_kept_separate() -> None:
    model = CostModel(
        spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005, fx_conversion_frac=0.0015
    )
    fill = apply_costs(100.00, Side.BUY, model, quantity=2.0)
    # notional = 100.065 * 2 = 200.130 ; fx = 0.0015 * 200.130 = 0.300195
    assert fill.costs.fx == pytest.approx(0.300195, rel=1e-12)
    # and it has not moved the price
    assert fill.executed_price == pytest.approx(100.065, rel=1e-12)


def test_zero_quantity_costs_nothing_even_with_a_flat_minimum() -> None:
    # A trade of nothing is not a trade. Charging the floor here would invent
    # a cost for an execution that never happened.
    model = CostModel(
        spread_frac=0.0003, slippage_frac=0.0005, commission_frac=0.0005, commission_min_flat=3.0
    )
    fill = apply_costs(100.00, Side.BUY, model, quantity=0.0)
    assert fill.costs.commission == 0.0
    assert fill.costs.fx == 0.0


# --------------------------------------------------------------------------
# Rejected inputs.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad_price", [0.0, -1.0, -100.0])
def test_non_positive_price_is_rejected(bad_price: float) -> None:
    with pytest.raises(ValueError, match="price must be positive"):
        apply_costs(bad_price, Side.BUY, REALISTIC)


def test_negative_quantity_is_rejected() -> None:
    with pytest.raises(ValueError, match="quantity must be non-negative"):
        apply_costs(100.00, Side.BUY, REALISTIC, quantity=-1.0)


def test_fill_records_the_mid_it_started_from() -> None:
    # The breakdown is only interpretable against the price it moved away from.
    fill = apply_costs(100.00, Side.BUY, REALISTIC, quantity=5.0)
    assert fill.mid_price == 100.00
    assert fill.quantity == 5.0
    assert fill.side is Side.BUY

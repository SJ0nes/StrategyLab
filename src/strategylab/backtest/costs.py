"""The transaction cost model.

The fill *formula* is market structure - a buyer crosses half the spread under
every broker on every venue - so it lives in code. The *rates* are broker
parameters and live in ``protocol.yaml``, so changing broker is a config edit
rather than a code change. See ``docs/decisions/0002-cost-model-shape.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategylab.core.types import Side


@dataclass(frozen=True)
class CostModel:
    """Broker parameters, in fractions, as converted from ``protocol.yaml``.

    Never chosen at analysis time - costs are the most effective dial for
    turning a losing strategy into a winning one, which is exactly why they are
    fixed in advance. Switching broker means editing config, never code.

    UNITS: every rate here is a FRACTION, not a percentage. ``0.0005`` means
    0.05%, which is five basis points. ``protocol.yaml`` stores the same rates
    as percentages under ``_pct`` keys;
    :func:`strategylab.protocol.load_protocol` divides by 100 exactly once, at
    the file boundary, so nothing downstream of it ever handles a percentage.
    The field names carry ``_frac`` for that reason: a rate whose name says
    percent and whose value is a fraction is a hundred-fold error waiting to
    happen, and it would not crash - it would just print wrong costs forever.
    ``tests/test_protocol.py`` asserts the conversion.

    Attributes:
        spread_frac: Full quoted bid-ask spread as a fraction of the mid price.
            A single execution crosses HALF of this.
        slippage_frac: Adverse price movement between decision and execution,
            as a fraction of the mid price. Crossed in full, not halved.
        commission_frac: Broker commission as a fraction of executed notional.
            Charged as cash, never folded into the executed price.
        commission_min_flat: Flat minimum commission per trade, in account
            currency units - a cash amount, not a rate, which is why it carries
            no fraction suffix. Dominates small positions, which is why a
            strategy that looks fine on percentage costs alone can be
            unprofitable in practice.
        fx_conversion_frac: Currency conversion charge as a fraction of
            executed notional, charged when the instrument's currency differs
            from the account's. Cash, like commission.
    """

    spread_frac: float
    slippage_frac: float
    commission_frac: float
    commission_min_flat: float = 0.0
    fx_conversion_frac: float = 0.0


@dataclass(frozen=True)
class CostBreakdown:
    """The itemised cost of one execution.

    Costs are itemised rather than netted so they stay inspectable. A single
    "cost" number hides which component dominates, and the component that
    dominates is usually the one that decides whether a strategy survives.

    Attributes:
        spread: Per-unit price impact from crossing half the spread, in price
            units. Always non-negative; the sign of its effect is carried by
            the side, not by this number.
        slippage: Per-unit price impact from slippage, in price units. Always
            non-negative, for the same reason.
        commission: Cash charge on the executed notional, in account currency
            units.
        fx: Cash currency-conversion charge on the executed notional, in
            account currency units.
    """

    spread: float
    slippage: float
    commission: float
    fx: float


@dataclass(frozen=True)
class Fill:
    """One execution, with the price actually paid and why it differs from mid.

    Attributes:
        side: Direction of the execution.
        mid_price: The reference price before any cost was applied, in price
            units.
        executed_price: The price actually transacted at, in price units. It
            includes the spread and slippage components of ``costs`` and
            excludes the cash ones.
        quantity: Number of units transacted. Non-negative; direction is
            carried by ``side``.
        costs: The itemised breakdown behind ``executed_price`` and the cash
            charges alongside it.
    """

    side: Side
    mid_price: float
    executed_price: float
    quantity: float
    costs: CostBreakdown


def apply_costs(price: float, side: Side, costs: CostModel, quantity: float = 1.0) -> Fill:
    """Compute the executed price and the itemised cost of one execution.

    Apply spread and slippage to the executed price; compute commission and FX
    as separate cash charges on the executed notional. Neither cash charge is
    EVER folded into the executed price, so costs stay inspectable rather than
    baked into a net number.

    A buyer crosses HALF the spread, not all of it: the executed price moves
    against the trader by ``price x (spread_frac / 2 + slippage_frac)``, upward
    for a ``BUY`` and downward for a ``SELL``. Buy and sell executed prices are
    therefore symmetric about ``price``.

    Note that the offset scales with ``price``. That multiplication is easy to
    omit and impossible to notice at a mid of 100.00, where a rate expressed as
    a percentage and the same rate applied to the mid are numerically equal.
    The tests parameterise over several mids for exactly that reason.

    Commission is ``max(commission_frac x executed notional,
    commission_min_flat)``.

    Args:
        price: The mid price before costs, in price units. Must be positive.
        side: Direction of the execution.
        costs: Broker parameters, with every rate a FRACTION, not a percentage.
        quantity: Number of units transacted. Must be non-negative.

    Returns:
        The fill, carrying the executed price and the itemised breakdown.

    Raises:
        ValueError: If ``price`` is not positive or ``quantity`` is negative.
    """
    raise NotImplementedError

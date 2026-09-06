---
name: cost-arithmetic
description: Use whenever a fill price, transaction cost, commission, spread, or slippage is computed, tested, or asserted - implementing the cost model, writing or checking a test with a hardcoded expected price, or deciding whether a rate belongs in code or in protocol.yaml. This is the authoritative statement of the arithmetic.
---

# Cost arithmetic

This is authoritative. Where anything else in the repository disagrees with this page, this page
is right and the other thing is a bug.

## The formula

**The executed price moves against you by half the spread plus slippage.**

```
executed = mid + direction * (spread/2 + slippage)      direction: BUY = +1, SELL = -1
```

Half the spread, not all of it. A buyer lifts the offer, which sits half a spread above the mid;
they do not pay the full bid-ask width. Charging the full spread on both sides double-counts
the cost of a round trip, which quietly halves every Sharpe ratio in the study.

**Commission is a cash charge on the executed notional. It is not a price adjustment.**

```
commission_cash = commission_rate * executed_price * quantity
```

It appears as its own line in the itemised breakdown and is **never folded into the executed
price**. Two reasons. First, it is a different kind of thing: spread and slippage are price
impact, commission is a fee, and a breakdown that merges them cannot answer "what would a
cheaper broker be worth". Second, folding it in makes buy and sell prices asymmetric about the
mid, which destroys the invariant below and makes every test harder to reason about.

**Two cash charges sit alongside it, on the same executed notional.**

```
commission_cash = max(commission_rate * executed_price * quantity, commission_min_flat)
fx_cash         = fx_conversion_rate * executed_price * quantity
```

`commission_min_flat` is a per-trade floor in account currency, not a rate, and it is what makes
small positions uneconomic. `fx_conversion_rate` applies when the instrument's currency differs
from the account's. Both are cash; neither ever touches the executed price.

**At `quantity == 0` every cash charge is zero.** A trade of nothing is not a trade, and
`max(0, commission_min_flat)` would otherwise invent a fee for an execution that never happened.
This is the one exception to the `max` above, and it is deliberate.

## Worked example

Mid `100.00`, commission `0.05%`, spread `0.03%`, slippage `0.05%`.

Half-spread is `0.03% / 2 = 0.015%` -> `0.015` on a mid of 100.00.
Slippage is `0.05%` -> `0.05`.
Price impact is `0.015 + 0.05 = 0.065` per side.

| Side | Executed price | Commission (charged separately) |
|------|----------------|---------------------------------|
| BUY  | **100.065**    | **0.0500325**                   |
| SELL | **99.935**     | **0.0499675**                   |

The two commissions are **not equal**, and the difference is the whole point.
Commission is charged on the *executed* notional, and the two legs execute at
different prices: `0.0005 x 100.065` against `0.0005 x 99.935`. They differ by
`0.000065`.

Rounded to four decimal places both read `0.0500`, which is also exactly what a
wrong implementation charging on the **mid** notional produces. **Never assert
these to 4dp.** A test that does passes against the bug this page forbids.

Round trip on 100.00 of notional:

```
price impact   0.065 + 0.065 = 0.130
commission     0.050 + 0.050 = 0.100
total                          0.230   = 0.23%
```

**0.23 total, being 0.13 of price impact and 0.10 of commission.**

## Invariants

These hold for every input, and each one is worth a test.

- **Symmetry.** Buy and sell executed prices are equidistant from the mid:
  `(buy + sell) / 2 == mid`. Above: `(100.065 + 99.935) / 2 == 100.000`.
- **Direction.** A buy always executes at or above the mid; a sell always at or below. Never the
  reverse, for any input. Getting this backwards produces a profitable-looking backtest that is
  pure sign error, and it is the most common mistake in this domain.
- **Linearity.** Costs scale linearly with notional. Double the quantity, double the cash cost;
  the executed *price* does not change with size, because this model has no market impact term.
  That is a limitation and it belongs in LIMITATIONS.md. **Holds only while
  `commission_min_flat` is zero** — `max(...)` is not linear, and below the floor doubling the
  size does not double the cost. That non-linearity is the entire reason the field exists, so a
  linearity test must pin the flat minimum at zero to mean anything.
- **Both halves of linearity, or neither.** Assert that the cash cost doubles AND that the
  executed price is unchanged. Asserting only the first passes against an implementation that
  added a spurious size-dependent price term.
- **Zero-cost identity.** With all rates at zero, the executed price is the input price
  unchanged and the breakdown is all zeros. Not approximately - exactly.
- **Non-negativity.** With non-negative rates, no component of the breakdown is ever negative.

## Where the numbers live

**The formula is market structure. It belongs in code**, with tests, and it does not vary by
broker or by run.

**The rates are broker parameters. They belong in `protocol.yaml`** - commission, spread, and
slippage as explicit named values.

**Rates are fixed before any search runs and are never chosen at analysis time.** A slippage
assumption adjusted after seeing results is not an assumption, it is a free parameter, and every
statistic downstream of it becomes meaningless. Set them once, from the broker's published
schedule or a defensible estimate, record where they came from, and leave them alone.

## Units

Rates cross a unit boundary exactly once, and the boundary is `load_protocol`.

**This is a rule about the `costs:` block.** A rate is a number something multiplies a price by.
`protocol.yaml` also carries percentages under `validation:` that are not rates and are not
converted — see the exemptions below. Read "every `_pct` key is divided by 100" as the rule and
a 25% drawdown ceiling becomes 0.25, which rejects every candidate ever tested.

- **In `protocol.yaml` they are PERCENTAGES**, under `_pct` keys: `commission_pct: 0.05` means
  0.05%. The file says percent because that is what a broker's published schedule says, and a
  key named `_pct` holding a fraction would be a lie in the name.
- **Everywhere downstream they are FRACTIONS**, under `_frac` names: `commission_frac` is
  `0.0005`. `CostModel` carries fractions; `apply_costs` never sees a percentage.
- **`load_protocol` divides by 100, once**, for the rates under `costs:`. That is the only
  conversion in the system.
- **Two exemptions.** `commission_min_flat` is a cash amount, not a rate.
  `validation.max_drawdown_ceiling_pct` is a percentage that STAYS a percentage, because it is
  compared against `max_drawdown`, which returns one. It keeps its `_pct` suffix downstream, and
  that suffix is the signal that it was not converted — a `_frac` name would mean it had been.

Say which of the two a number is in every docstring that touches a rate. A rate read as `0.05`
when `0.0005` was meant is a hundred-fold error that still produces plausible-looking output,
and no test catches it unless the units are stated — so `tests/test_protocol.py` asserts the
conversion directly rather than trusting the comment.

Beware the worked example above: at a mid of `100.00` a rate expressed as a percentage and the
same rate applied to the mid are numerically identical, so an implementation that never
multiplies by the mid reproduces every figure in it. Test at several mids. The round-trip cost
is `0.23%` of notional at EVERY mid — verified at `100.00`, `250.00`, and `37.42` — and that
invariant breaks immediately under a units error, a missing multiply by the mid, or a full
spread charged where a half belongs.

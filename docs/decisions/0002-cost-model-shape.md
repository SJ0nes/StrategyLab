# 0002 — Where the cost model's formula and rates each live

## Context

A fill has to be priced, and a transaction cost has to be charged. Both the
arithmetic and the numbers it consumes have to live somewhere, and putting them
in the same place is the default that causes the damage.

Transaction costs are the single most effective dial for turning a losing
strategy into a winning one. If costs can be adjusted while looking at results,
they will be — not dishonestly, but by the ordinary process of "that slippage
assumption seems too harsh" occurring precisely when the results are
disappointing.

## Decision

**The formula lives in code. The rates live in `protocol.yaml`. They cross at
exactly one boundary.**

The fill formula is market structure. It does not vary by broker, venue, or run:

- The executed price moves against the trader by **half the spread plus
  slippage** — a buyer lifts the offer, which sits half a spread above the mid.
  Charging the full spread on both sides double-counts it.
- **Commission is a cash charge on the executed notional and is never folded
  into the executed price.** It appears as its own line in the itemised
  breakdown.
- Commission is `max(commission_frac × executed notional, commission_min_flat)`.

The rates are broker parameters: `commission_pct`, `spread_pct`, `slippage_pct`,
and optionally `commission_min_flat` and `fx_conversion_pct`. Changing broker is
a config edit, never a code change.

The model deliberately carries **a flat commission minimum and an FX conversion
charge**, because real brokers are not purely percentage-based. A flat minimum
dominates small positions, which is how a strategy that looks fine on percentage
costs alone turns out to be unprofitable in practice. An FX charge applies
whenever the instrument's currency differs from the account's — which, for a GBP
account trading a fund that tracks a USD index, is always.

> **Corrected by [0004](0004-two-cost-components-decided-at-zero.md).** That last
> clause is wrong. It conflates currency *exposure* with a conversion *fee*.
> `VUAG.L` is GBP-denominated, so a GBP account buying it converts nothing and
> the broker charges nothing; the fund's USD holdings are exposure, internal to
> the fund, and not a charge at the point of trade. The paragraph is left as
> written because these records are historical, and the error is corrected here
> rather than edited away. Both rates are now set explicitly to zero in
> `protocol.yaml`, with the reasoning in 0004.

**The model carries both fields; the pre-registration switches both off.**
`protocol.yaml` sets neither `commission_min_flat` nor `fx_conversion_pct`, so
both default to zero and no FX charge is currently modelled — for exactly the
GBP/USD case named above. That is a disclosed gap, recorded in `LIMITATIONS.md`,
not a claim that FX is being charged.

## Consequences

**Rates are fixed before any search runs and are never chosen at analysis time.**
Set once, from a defensible estimate, and left alone. Changing them after results
exist requires a decision record and invalidates prior comparisons.

**Costs stay itemised rather than netted.** A single "cost" number hides which
component dominates, and the dominant component is usually what decides whether
a strategy survives. The breakdown reports spread, slippage, commission, and FX
separately, so each is inspectable.

**Units cross exactly one boundary, and it is tested.** `protocol.yaml` stores
percentages under `_pct` keys, because that is what a broker's published schedule
quotes and a key named `_pct` holding a fraction would be a lie in its own name.
`load_protocol` divides by 100 once. Everything downstream carries fractions
under `_frac` names, and `apply_costs` never sees a percentage.
`commission_min_flat` is a cash amount and is never converted. This is asserted
in `tests/test_protocol.py`, not merely documented — a hundred-fold rate error
does not crash, it prints plausible wrong costs indefinitely. See
`docs/decisions/0003-protocol-completed-before-first-search.md`.

**The flat minimum breaks strict linearity, deliberately.** Costs otherwise scale
linearly with notional; `max(...)` does not. That non-linearity is the whole
point of the field, and any test of the linearity invariant must hold
`commission_min_flat` at zero to be meaningful.

**Cost tests must not be written only at a mid of 100.00.** At that mid a rate
expressed as a percentage and the same rate applied to the mid are numerically
identical, so an implementation that never multiplies by the mid reproduces every
figure of the worked example exactly. Round-trip cost is 0.23% of notional at
every mid, and tests parameterise over five — 100.00, 250.00, 37.42, 1.05, and
4321.99 — so that a units error, a missing multiplication, or a full spread
charged where a half belongs all break the invariant immediately. The last two
sit near the bottom of their binades, where relative floating-point error is
largest.

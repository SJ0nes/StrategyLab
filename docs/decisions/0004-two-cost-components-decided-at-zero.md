# 0004 — Two cost components decided at zero, and one that is not modelled at all

## Context

*This section describes the repository as it stood when the decision was taken,
which is what a decision record is for. `run_backtest` was a stub then and is
built now; the sentence below is dated, not stale.*

`protocol.yaml` set three rates and left two unset. `commission_min_flat` and
`fx_conversion_pct` therefore defaulted to `0.0` in `load_protocol`, which is the
same number a deliberate decision would produce and carries none of the meaning.

That gap was disclosed in `LIMITATIONS.md` as a limitation rather than a
decision, and ADR 0002 had already named both as the terms "that kill
otherwise-viable strategies". A reviewer reading those two documents together
would reasonably conclude the model was quietly optimistic.

This is being recorded now because **no result exists yet**. `protocol.yaml` says
editing it after results exist invalidates every comparison made before the edit;
`access_log.walk_forward_touches` is `0`, no backtest has ever run, and
`run_backtest` is still a stub. This is the last moment at which these numbers
can be set without costing anything, and the first at which anyone has actually
been asked what they are.

> **Update, 2026-08-22.** The sentence above is left as written, because a
> decision record states what was true when the decision was made and editing
> that would misrepresent it. Two of its facts have since changed and one has
> not. `run_backtest` is no longer a stub — it was built, and has since been run
> over the recorded SPY fixture in `tests/test_grid.py`. A backtest has therefore
> run, though only in a test and only on a one-year fixture. What has NOT changed
> is the part the decision rests on: no *result* exists, nothing has been scored,
> and `access_log.walk_forward_touches` is still `0`. The two rates recorded here
> are still costless to have set.

## Decision

**Both are zero, deliberately, for the account and instrument this project
targets.**

**`commission_min_flat: 0.0`.** The execution account is a Trading 212 stocks and
shares ISA, which charges no commission on stock and ETF trades and applies no
per-trade minimum. There is no flat floor to model.

**`fx_conversion_pct: 0.0`.** The execution instrument is `VUAG.L`, a
GBP-denominated line listed on the LSE. A GBP account buying it performs no
currency conversion, so the broker charges no conversion fee.

**This corrects ADR 0002**, which stated that an FX charge applies "for a GBP
account trading a fund that tracks a USD index — always". That conflates currency
exposure with a conversion fee. The fund's holdings are USD-denominated and the
resulting exposure is real, but it is internal to the fund and is not a charge
levied at the point of trade. ADR 0002 is left in place as the historical record
and carries a pointer here.

**The zero is a property of the ACCOUNT-AND-INSTRUMENT PAIRING, not of the
broker.** Executing `SPY` directly from a GBP account would convert currency on
every trade and this number would be wrong. Anything that changes
`instruments.execution` must revisit this file.

## What this decision does NOT make zero

Recording the two zeroes without recording what stays non-zero would be the
optimism this ADR exists to prevent.

- **The spread is still charged, at `spread_pct: 0.03`.** A commission-free
  broker is not a cost-free one; the spread is where the cost went. Half of it is
  applied to the executed price on every fill.
- **Slippage is still charged, at `slippage_pct: 0.05`.**
- **The fund's own ongoing charge is not modelled anywhere, by anything.**
  `VUAG.L` levies an annual management charge against the fund's assets. It is
  not a transaction cost, so `CostModel` has no term for it — `apply_costs`
  charges per fill and nothing in this project charges per unit of time held.
  A trend strategy that holds for months pays it and the backtest will not show
  it. Adding it is a code change with a test, not a config edit, and it is
  recorded in `LIMITATIONS.md` rather than quietly assumed away.
- **Nothing here models taxes, interest on uninvested cash, or borrow costs.**
  Unchanged by this decision and still listed in `LIMITATIONS.md`.

## Consequences

`protocol.yaml` now states five rates explicitly rather than three, so a reader
can see that every term was considered. Any future change to either number, or to
`instruments.execution`, is a protocol edit under the usual rule: record why, and
reset the access counter.

`LIMITATIONS.md`'s cost section changes from "two real components are switched
off" to "two are zero because the account does not charge them, and one real cost
is not modelled at all". The second sentence is the one that should worry a
reader, and it was not being said before.

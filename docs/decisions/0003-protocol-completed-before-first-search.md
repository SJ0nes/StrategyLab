# 0003 — Completing protocol.yaml before the first search

## Context

`protocol.yaml` was pre-registered at Gate 1 of the first build session, before
any code existed. Reviewing the module contracts at Gate 2 — with four read-only
auditors reading the same files independently — surfaced three defects in the
pre-registration itself. None of them was a preference; each was a way for the
protocol to fail to constrain the thing it exists to constrain.

**1. The pass criterion was not pre-registered.** The contract for
`validate.walk_forward` required the walk-forward Sharpe to clear `min_sharpe`
by `pass_margin`. `pass_margin` was in the file; `min_sharpe` was in no file at
all. The threshold that decides pass or fail was therefore a run-time argument,
settable *after* seeing the result — which is the exact failure the
pre-registration exists to prevent, sitting on the single most consequential
number in the project.

**2. One trade floor was doing two incompatible jobs.** `min_trades_per_fold`
was the only trade threshold, while the same contract specified that fold
results are POOLED for the pass decision. A per-fold-only floor contradicts a
pooled decision: read per-fold it says nothing about whether the pooled figure
rests on enough trades, and read pooled the field name is a lie.

**3. Cost rates had no stated units.** `commission_pct: 0.05` is a percentage;
`.claude/skills/cost-arithmetic/SKILL.md` stated that rates are stored as
fractions, where the same rate is `0.0005`. Both statements were in the
repository and they differ by a factor of one hundred. A rate read under the
wrong convention does not crash — it prints wrong costs indefinitely.

## Decision

Amend `protocol.yaml` now, before the first search has run, and record the
amendment here.

- **Add `validation.min_sharpe: 1.0`.** The Sharpe floor is pre-registered like
  every other criterion. `walk_forward` no longer takes it as an argument; it
  reads the protocol, so there is no run-time channel through which the bar can
  move.
- **State that `pass_margin` is ADDITIVE**, in the file's own comment, in the
  `Validation` docstring, and as a single derived value
  `Validation.effective_sharpe_bar`. The effective bar is
  `min_sharpe + pass_margin` = **1.20**. Callers never have to remember whether
  the margin is additive or multiplicative, because they never apply it.
- **Split the trade floor in two.** `min_trades_per_fold: 10` governs whether a
  single FOLD reports `insufficient_data`; `min_trades_pooled: 30` governs
  whether the WHOLE VALIDATION does. Each name now means what it says, and the
  pooled decision has a floor of its own. The loader rejects a pooled floor
  below the per-fold floor, which would be incoherent.
- **Fix the unit boundary in one place.** `protocol.yaml` keeps percentages
  under `_pct` keys, because that is what a broker's published schedule quotes
  and a `_pct` key holding a fraction would be a lie in the name.
  `load_protocol` divides by 100 exactly once. Everything downstream — including
  every `CostModel` field, now suffixed `_frac` — carries fractions, and
  `apply_costs` never sees a percentage. `commission_min_flat` is a cash amount,
  not a rate, and is not converted.

The units fix is enforced by assertion, not by comment:
`tests/test_protocol.py::test_costs_convert_percent_to_fraction` asserts that
`commission_pct: 0.05` in the file becomes `commission_frac == 0.0005` in the
model, and `test_conversion_is_exactly_one_hundredth` pins the direction of the
division. Both fail if the conversion is removed — verified by mutation.

## Consequences

**This amendment invalidates nothing, and that is the entire reason it happens
now.** No search has run, no backtest has been executed, no result exists to
compare against, and `access_log.walk_forward_touches` is `0`. The held-back
window has never been read. There is no prior number for the edit to have been
chosen to flatter, which is what distinguishes this from the kind of edit the
protocol forbids. The same three changes made after the first search would be
indefensible, and would require resetting the access counter.

Two of the three changes are unambiguous tightenings: `min_sharpe` adds a
criterion that did not previously exist, and removing the run-time argument
closes the channel through which a threshold could have been chosen after seeing
a result.

**The trade-floor change is NOT a uniform tightening, and it would be dishonest
to describe it as one.** `min_trades_per_fold` moves from 30 to 10. Under the old
value a fold with twelve trades returned `insufficient_data`; under the new one
it returns a real per-fold verdict, which may be a pass. Something that
previously could not pass now can.

What can be said is narrower: the POOLED decision — the one that actually
determines the outcome — is harder to satisfy, because it now has both a Sharpe
floor and a trade floor where before it had neither. Even that is asserted
rather than demonstrated: if the old `min_trades_per_fold: 30` was read as a
pooled threshold, which was one of the two live readings this record exists to
resolve, then `min_trades_pooled: 30` is numerically identical to it and nothing
tightened at all. No run exists on either version to compare, because
`walk_forward` is still a stub.

Expect `insufficient_data` to be a common per-fold outcome regardless. A moving
average crossover on daily bars with a fast period of 5–30 does not generate ten
round trips in every twelve-month fold. **If folds routinely return
`insufficient_data`, that is a finding about whether this validation design can
test this strategy at all, and it belongs in `LIMITATIONS.md`. It is never a
reason to lower a threshold after seeing the counts.** Any future change to
these numbers requires its own decision record and a reset of the access
counter.

Renaming the `CostModel` fields from `_pct` to `_frac` is a deliberate deviation
from the signature given in the session prompt. The prompt's own principle —
that a unit must be stated wherever a rate appears — is better served by a name
that cannot be misread than by a docstring correcting a name that can.

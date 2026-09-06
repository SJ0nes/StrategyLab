# 0009 — How the held-back window is run: one continuous backtest, sliced into folds

## Context

`validate/walkforward.py` was a signature and a docstring contract. The contract
fixed a great deal — three outcomes, an additive pass margin, two distinct trade
floors, pooling over per-fold voting, a touch on every run — and left four
questions open that it never mentions and that each change the number the verdict
rests on.

None of these is visible from the outside. A validator making the opposite choice
on any of them returns a Sharpe that looks exactly as plausible, which is the
condition under which this project writes a decision record rather than a
comment.

## Decision

**1. The engine runs ONCE over the held-back window. Folds are date slices of
that one run.**

The alternative is a backtest per fold, each starting flat with its own equity.
It was rejected because the folds are a measurement device, not a trading rule.
Six separate runs would force the position flat at every boundary — inventing six
exits nobody would have taken, and six re-entries — and would reset the equity
curve six times, so the pooled drawdown could never see a decline that spanned a
boundary. The one figure the drawdown ceiling exists to catch is exactly the
multi-year one.

A consequence worth stating: `pooled_sharpe`, `pooled_max_drawdown_pct` and
`total_trades` are the run's own figures, not an aggregation of six sets of
per-fold figures. They cannot disagree with the folds, because they were never
computed from them.

Two rules follow from that and are written here rather than left to the code,
because an implementation and its own test agreeing is not a specification
(ADR 0005).

**A trade belongs to the fold that CLOSED it.** A position opened in one fold
and closed in the next is counted once, in the later one, so the folds' counts
sum to `total_trades` exactly. The corollary reads oddly in a table and is worth
knowing: a fold showing `0` trades is not necessarily a fold in which nothing
happened — 2021 in the committed window took no round trip while holding a
position opened in 2020 throughout.

**A fold is judged on the SAME Sharpe bar as the pooled decision** — the
effective bar, `min_sharpe + pass_margin`. Only the trade floor differs between
the two, `min_trades_per_fold` against `min_trades_pooled`. The alternative —
judging an advisory figure against the unmargined floor — is defensible, which
is exactly why the choice needs stating somewhere a reader can find it.

**2. The warm-up is drawn from the in-sample window, and exactly `warmup - 1`
bars of it.**

The committed candidate carries a 150-bar trend filter. Handing the engine the
held-back window alone would leave its first seven months unevaluable, and a
validation that silently discards the first seven months of every window is
measuring something other than what it claims.

The direction of travel is what makes this safe. The rule is that the *search*
must never see the *future*. Warming up a held-back evaluation on the bars
immediately before it is what a live trader does on their first morning. It is
read through `windows.in_sample_bars`, which is free and repeatable, and it is
capped at `warmup - 1` bars so the equity curve begins on the window's first bar
and covers the window and nothing else.

The cost, stated rather than hidden: **the position starts flat.** A strategy
that would already have been long entering 2019 is credited with entering later.
Carrying a position across the boundary is the alternative, and it would mean an
entry decided on in-sample data showing up as a held-back result.

**3. A trailing remainder is absorbed into the final fold.**

`fold_months` is a length, and a window need not divide by it. Three options: a
short final fold, a dropped remainder, or a long final fold.

A short fold was rejected because its Sharpe is not comparable to the others and
it would be read as though it were. A dropped remainder was rejected because the
touch has already been charged for the whole window — discarding held-back data
that has already cost what held-back data costs is the worst of the three. So the
last fold runs long, and every fold carries its real dates so a long one is
visible rather than implied.

With the committed protocol — 2019-01-01 to 2024-12-31, `fold_months: 12` — there
is no remainder and this changes nothing. It is decided now because it is
undecidable later without looking at a result.

**4. A pooled Sharpe that is not a finite number is `insufficient_data`, never a
pass.**

`nan < 1.20` is `False`. A validator that tests `pooled_sharpe < bar` and calls
everything else a pass therefore PASSES a candidate whose Sharpe could not be
computed. This is the only failure mode in the module capable of producing a
false positive, so the finiteness check runs before the thresholds rather than
being left to the comparison.

`tests/test_walkforward.py::test_a_non_finite_pooled_sharpe_is_never_a_pass`
asserts the guarded behaviour. It does not delete the guard — no test in this
repository mutates the code it covers. The mutation required by `CLAUDE.md`
("break the code it covers and confirm the test goes red") was performed by hand
during the session that wrote this module: the finiteness check was disabled,
the test went red, and the outcome it then returned was `PASS`. **No mutation
harness is committed**, so that result is not reproducible from this repository.
An earlier draft of this record said the test performed the mutation, which was
untrue and is the same defect `LIMITATIONS.md` retracts an earlier figure for.

## The signature changed

The stub was `walk_forward(definition, params, protocol)`. It is now
`walk_forward(definition, params, bars, protocol, *, protocol_path, starting_equity)`.

Two of the three additions are not conveniences. The third, `starting_equity`,
defaults to `search.loop.STARTING_EQUITY` — 10,000 account currency units — so a
validation runs at the same scale as the search that nominated the candidate. It
is imported rather than redeclared, and it is not protocol data for the reason
that constant gives: both metrics the verdict rests on are scale-free while
`commission_min_flat` is zero, and if that ever stops being zero the number
belongs in `protocol.yaml` instead.

**`bars`** — every other component in this project is handed its bars rather than
reading the cache itself, and the window it may see is decided from the protocol
inside the function. A validator that opened the parquet file itself would be the
only component with two ways to be wrong about which bars it read.

**`protocol_path`** — without it the counter defaults to the repository's own
`protocol.yaml`, and there is then no way to run a test, or to develop against
the module at all, without permanently polluting the one number this repository
asks to be trusted. `windows.walk_forward_bars` already takes this argument for
exactly this reason; the validator inherits it rather than inventing an escape
hatch. There is still no dry run and there must never be one.

## Consequences

**A development run returned `insufficient_data`, and that is a finding about
the validation design rather than about the strategy.** The committed candidate
took 8 round-trip trades across six years against a pooled floor of 30. The
floors were pre-registered before anything ran and are not being changed;
`LIMITATIONS.md` records what they mean for this strategy. Lowering a threshold
after seeing a count is the exact failure the pre-registration exists to prevent.

That run was made against a COPY of `protocol.yaml`. The repository's own
counter still reads `0`, and the first run against it is the operator's to
make — so "first" above means the first this session performed, not the first
this repository has recorded.

**A ninth position was still open when the window ended.** Nine entries, eight
exits: the position opened 2024-08-20 was held through 2024-12-31, so it paid no
exit cost, is absent from the `8` above, and its mark-to-market is nonetheless
inside the pooled Sharpe and drawdown. Nothing liquidates at the window edge and
nothing here changes that — forcing one would invent an exit nobody took, and it
would change what `run_backtest` means for every caller. `LIMITATIONS.md` records
it as a limitation of the measurement.

**A verdict is persisted.** `write_validation_result` writes
`state/validation-<stamp>.json`. The search loop had to argue for its artifact;
this one does not. Re-deriving a verdict costs a permanent touch, so a verdict
that lived only in a terminal scrollback would make the counter's arithmetic
dishonest.

**The strategy config is still not hashed.** A verdict names the config it ran
from and compares it to nothing, while the price snapshot is compared by content
hash. A config edited between the search and the validation would load silently.
Recorded in `LIMITATIONS.md`; it is a real hole and not a decision.

# 0012 — The noise ceiling becomes a gate, and who decided that

## Context

`CLAUDE.md` says a winning Sharpe means nothing until it is compared against the
best the same grid achieves on surrogate series. Until now that comparison was
printed and nothing acted on it. `select_candidate` never saw the ceiling, the
graveyard carried a `below_noise_ceiling` reason no code wrote, and `validate`
would spend a permanent read of the held-back window on a candidate that had
never been benchmarked, or that sat inside the distribution. `LIMITATIONS.md`
and the README both listed this as the gap between what the project argues and
what it enforces.

## Who made this decision

**The assistant made it, not the operator.** On 2026-11-10 the operator told the
assistant to finish the project and to resolve open questions itself rather
than ask. The README says earlier design decisions were ratified by a human at
explicit gates. This one was not, and it is recorded here so the difference is
visible. The operator can reverse it by reverting one commit.

## What had been seen when the rule was fixed

A threshold chosen after looking at the answer is not a threshold, so this is
stated exactly.

- One plain search of the committed grid, from 2026-08-22, with no ceiling.
- One smoke run on 2026-11-10 with a single surrogate, made to check that
  `--trials` works end to end. The candidate's Sharpe was 0.640 and the grid's
  best cell on that one surrogate scored 0.949.

No ceiling of a hundred trials had been computed, in this repository or
anywhere else, when the rule below was written. But the assistant did know,
from that one surrogate, which way the answer was likely to point: the
committed candidate will probably fail. The rule was not tuned to produce that
outcome, and a reader is entitled to know it was foreseeable.

## Decision

**1. The rule.** A candidate clears the gate when its own Sharpe is strictly
above the best-in-grid Sharpe of at least 95 percent of the scoreable
surrogates, and there are at least 100 of those. It is a comparison of counts:
95 of 100, 96 of 101, 190 of 200. A tie is not a win.

Ninety-five and a hundred are not new numbers. The search has printed the 95th
percentile since the ceiling was built, and `RELIABLE_TRIALS = 100` has been the
stated point at which that tail can be read. This decision makes them binding
rather than choosing them.

**2. Fewer than a hundred scoreable trials is no verdict, in either direction.**
A candidate that beats nineteen of twenty has not passed and one that beats
three of twenty has not failed. With few trials the 95th percentile is close to
the maximum of a small sample, which makes a gate on it too easy to pass.

**3. The candidate's own Sharpe is what is compared, not its plateau score.**
The surrogates record the best single cell. The candidate was chosen for its
neighbourhood rather than for being the best cell, so holding it to the best
cell's standard is the harder comparison, and that is the direction to err in.

**4. The gate judges the search, once.** It is applied to the cell selection
chose. When it fails, nothing walks on to the next-best cell: that would pick a
cell for its own Sharpe, which is what ranking by plateau exists to avoid.

**5. A failed candidate stays the winner, and `validate` refuses it.** The
verdict is recorded in the artifact as `noise_gate`. The cell is written to the
graveyard under `below_noise_ceiling` and is not added to `rejections`, because
those partition the grid together with the winner and it would be counted
twice. `read_candidate` refuses anything short of a pass before a touch is
charged, and recounts the numbers rather than trusting the word `passed`.

**6. `run` and `diff` waive the gate, by name.** Paper trading tests the
simulator, not the strategy, so a winner the gate refused is as useful to it as
one that passed. The waiver is printed every time it is used.

**7. The numbers live in code, not in `protocol.yaml`.** The protocol is written
once, and editing it after results exist invalidates prior comparisons. Adding
two keys to it now would be exactly that edit. The constants are
`GATE_PERCENTILE` and `RELIABLE_TRIALS` in `search/noise.py`; every artifact
records the two values it was judged under, and a reader refuses one judged
under different values.

## Consequences

**A plain search can no longer be validated.** `validate` on a search run
without `--trials 100` or more is refused. That makes the default search a
look at the surface and nothing more, which is what its own output already said.

**The gate only ever makes validation harder to reach.** No pre-registered
threshold moves. A candidate that would have passed validation and failed the
gate is now never validated, and one that fails validation is unaffected.

**An unscoreable surrogate can cost a verdict.** A hundred trials with one that
scored nothing leaves ninety-nine and no verdict. Run a few more than a hundred.

**"Noise" is still not noise.** The surrogates are block-resampled real history,
as `docs/decisions/0008` records. The gate inherits that: it asks whether the
candidate beats reorderings of this market's own regimes.

**What is not gated.** The ceiling is one price series and one grid. Nothing
adjusts for the number of searches run over time, and no deflated Sharpe is
computed anywhere.

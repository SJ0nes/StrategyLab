# 0008 — The surrogate block length is the grid's longest lookback

## Context

The noise ceiling re-runs the entire grid against surrogate series with no edge
in them, and compares the winner on real data against the best achieved on
noise. `CLAUDE.md` fixes the resampling method but not its one free parameter:

> Use BLOCK RESAMPLING, not an i.i.d. shuffle: shuffling destroys the
> autocorrelation that a trend strategy trades, which makes the surrogate an
> unfairly weak opponent and sets the bar far too low.

That rules out a block of one. It does not say how long a block should be, and
the length is not a detail — it decides how hard the ceiling is to clear, which
decides whether any result this project ever produces means anything.

The failure the rule names has a quieter version. A block shorter than the
strategy's lookback destroys the same structure a shuffle does. A two-hundred-bar
trend cannot exist inside a twenty-bar block: every block boundary cuts it. The
strategy then finds nothing on the surrogate, the ceiling comes out low, and a
mediocre real result clears it. Nothing errors, and the number looks fine.

The conventional choice from the moving-block bootstrap literature is roughly
`n ** (1/3)` — about fifteen bars for a three-and-a-half-thousand-bar window.

## Decision

**The block length is the longest lookback the grid actually reaches**, taken
from `expand_grid` rather than from the declared `max`. A range is inclusive of
`max` only on a step boundary, so `{min: 1, max: 10, step: 4}` tops out at 9 and
a block sized from the declared 10 would exceed anything the grid can use. This
is the same rule `plateau.is_edge_winner` applies, read from the same place, so
the two cannot disagree about what a range means. For
`configs/ma_crossover.yaml` the answer is `trend: 200`, which the grid does
reach, so blocks are two hundred bars.

Floored at two, because a block of one *is* the shuffle the rules forbid.

## Consequences

**The surrogate can express what the strategy trades.** A full two-hundred-bar
trend fits inside one block, so a trend-following strategy has the same kind of
structure available to it on noise as on real data. That is what makes the
comparison fair, and it is the whole reason for rejecting the conventional
length.

**Few blocks, so less diverse surrogates.** Two hundred bars over about three
and a half thousand is roughly eighteen blocks. Surrogates therefore resemble
one another, and the real series, more than a conventional bootstrap would. The
ceiling explores less of the space of possible histories than it might.

**That bias runs towards a HARDER bar, which is the right direction.** Less
diverse surrogates retain more of the real series' structure, so the best
in-grid result on them is higher than a more thoroughly scrambled surrogate
would give. A ceiling biased upward rejects some genuine edges; a ceiling biased
downward endorses noise. This project would rather discard a real finding than
publish a false one, and every other choice in it leans the same way.

**It is the better experiment and the worse statistic**, and that trade is made
knowingly. Standard bootstrap theory prefers `n ** (1/3)` for good reasons about
the estimator's properties. Those reasons are about estimating a distribution's
parameters accurately; this is about building a fair opponent. The two goals
disagree here, and the second is the one the ceiling exists to serve.

**The length is derived, not configured.** It comes from `search_spec`, so it
cannot drift from the grid it is testing, and widening a range automatically
lengthens the blocks. It is not in `protocol.yaml`: the protocol carries what
decides a pass, and the ceiling is reported rather than enforced — nothing in
`validate` reads it. That also means it is *not* protected by the write-once
rule, which is a real gap and is recorded in `LIMITATIONS.md`.

**One pleasant consequence, worth knowing.** `block_resample` needs
`block <= len - 1`, so any window a surrogate can be built from holds at least
`L + 1` bars, where `L` is the longest lookback. The largest cell's warm-up is
exactly `L + 1`. So if a surrogate can be built at all, every cell in the grid
can warm up on it — a whole class of "the trial produced nothing" failure cannot
arise from warm-up. `tests/test_noise.py` pins the reasoning.

## What was rejected

**`n ** (1/3)`, the conventional moving-block length.** Better statistical
properties, wrong experiment: it destroys the structure under test and sets the
bar too low. This is the same failure as shuffling, arriving more quietly.

**Reporting both lengths side by side.** Honest about the sensitivity, and the
gap between them would itself be informative. Rejected on cost: the ceiling
already runs the whole grid once per surrogate, and doubling that for a
diagnostic nobody has yet asked for is the wrong first version. If a result ever
turns on which length was used, this is the first thing to revisit.

## A divergence from a non-negotiable rule, recorded rather than hidden

`CLAUDE.md` lists among the rules that may not be broken:

> **Every search reports its noise ceiling.** A winning Sharpe means nothing
> until compared against the best Sharpe the same grid achieves on surrogate
> series with no edge in them.

**The implementation does not honour that literally.** `--trials` defaults to
zero, so `strategylab search` with no flags reports no ceiling. This section
exists because a rule described as non-negotiable should not be quietly
softened by a default.

**Why.** One trial re-runs the entire grid. The committed search takes about
four minutes, so honouring the rule literally makes every search a multi-hour
command — and a hundred trials, the point at which the upper tail is stable
enough to quote, is most of a day. A tool whose only mode costs a day does not
get run; it gets worked around, and the workaround is somebody computing a
Sharpe in a notebook with no ceiling at all. Enforcing the rule in the code
would produce less compliance with its intent, not more.

**What is done instead.** The rule's purpose is that nobody reads a winning
Sharpe as evidence. That is enforced, on every path:

- a search run without `--trials` prints, to standard error, that no ceiling
  was computed and that nothing it printed is evidence of an edge;
- the artifact records `"noise_ceiling": null` with a note saying the same, so
  the file cannot be read later without the caveat;
- the HTML report states the absence where the band would be;
- `status` repeats it.

**What is NOT done, and is the residual risk.** The cheap path is still the
unbenchmarked one. A reader who runs `search`, ignores four warnings and quotes
the winner has a plausible-looking number and nothing stopping them. The
mitigations are all disclosure, and disclosure is weaker than refusal.

**What would close it.** Making `--trials` a required option with no default,
so skipping the ceiling is an explicit act rather than an omission. That is a
small change and it is deliberately not made yet: it should be made when
somebody other than the author first uses this, because that is when the
default's pull can actually be observed rather than guessed at.

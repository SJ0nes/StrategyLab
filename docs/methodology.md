# Methodology

> **Status: the whole research half of this document is built and running. The
> live half is not, and two of the corrections described below inform a human
> rather than gating anything.**
>
> The split is enforced by `windows.py`, which both components that read bars go
> through. `load_protocol` refuses a protocol whose two windows overlap *as
> declared*; `in_sample_bars` cuts from `protocol.windows.in_sample` and from
> nothing else; `walk_forward_bars` cannot return a held-back bar without
> incrementing the access counter, and there is no dry run. `fetch` still caches
> both windows in a single file, so the guarantee lives in that accessor rather
> than in the data on disk — but there is now one accessor rather than a private
> helper per component.
>
> Grid expansion, plateau scoring, the edge check, the degeneracy check, the
> graveyard, the loop that joins them, the noise ceiling, the surface report and
> **walk-forward validation** are built, tested, and reachable as `strategylab
> search`, `report` and `validate`. The noise ceiling now gates validation:
> a candidate that has not cleared it is refused before the held-back window is
> read ([decisions/0012](decisions/0012-the-noise-ceiling-becomes-a-gate.md)).
> What is NOT built: anything computing buy-and-hold, so a candidate can still
> pass validation while losing to simply owning the index. Sentences below about live trading describe a design rather than a
> running system. See [architecture.md](architecture.md) for what exists and
> [../LIMITATIONS.md](../LIMITATIONS.md) for what that costs.

Why this project is arranged the way it is. Every rule below exists because the
obvious alternative produces a number that looks good and means nothing.

## The problem this design is solving

Given enough parameter combinations and one price series, something will look
profitable. That is not a risk to be managed carefully — it is a certainty. With
90 combinations and fourteen years of daily bars, the best-performing settings
will show a healthy Sharpe ratio on data with no exploitable structure in it at
all.

So the question is never "did this strategy make money in the backtest". It is
"did it make more money than the same search would have found in noise, on data
it was not allowed to see, after costs". Everything below serves that question.

## The in-sample / walk-forward split

The price history is cut in two, once, before any code ran.

- **In sample: 2005-01-01 to 2018-12-31.** The search may look at this and
  nothing else. Every parameter choice is made here.
- **Walk forward: 2019-01-01 to 2024-12-31.** Held back. The search never sees
  it. It exists to test a candidate that has *already* been chosen.

The windows do not overlap, and `load_protocol` refuses to load a protocol where
they do.

The split is not a formality. A parameter set chosen on data and then evaluated
on the same data is being graded on its own answer sheet. The in-sample result
tells you the best a search can do; the walk-forward result is the only estimate
of what the strategy might do next.

The held-back window is rolled through in **consecutive folds of twelve months
each** — six folds. `fold_months` is a fold *length*, not a fold count. Results
are **pooled** across folds for the pass decision, because a per-fold vote on six
observations is a noisier statistic than one computed on all the trades
together. Per-fold consistency is reported, and it matters: it shows whether a
pass came from one lucky year or from behaviour that persisted.

The engine runs **once** over the held-back window and the folds are date slices
of that single run, so a position is never forced flat at a boundary and the
pooled drawdown can see a decline spanning two folds. It is warmed up on the
last `warmup - 1` in-sample bars, so the equity curve starts on the window's
first day rather than seven months into it — a held-back evaluation reading its
own past, which is the opposite direction from the leak this split exists to
prevent. Both choices, and the two others the contract left open, are argued out
in [decisions/0009](decisions/0009-how-the-held-back-window-is-run.md).

### The access counter

Every walk-forward run increments `access_log.walk_forward_touches`. This is
recorded because the split degrades with use. The first candidate tested against
2019–2024 gets a genuinely clean test. The twentieth does not — by then, choices
about which candidates were worth testing have been informed by what the earlier
tests returned, and the held-back window has quietly become part of the search.

The counter does not prevent this and does not correct for it. It makes it
visible, which is the most an honest system can do without inventing a
correction it cannot justify.

## Three outcomes, not two

Validation returns `pass`, `fail`, or `insufficient_data`. The third is not a
soft failure and must never be reported as one.

- **`fail`** means the strategy was tested and was not good enough.
- **`insufficient_data`** means it was not really tested. Get more history or
  trade more often.

Conflating them destroys information in both directions: a candidate that was
never tested gets discarded as refuted, or one already refuted gets another look
because "the sample was small". The thresholds are separate and both
pre-registered — `min_trades_per_fold: 10` governs whether a single fold reports
a verdict, `min_trades_pooled: 30` governs whether the validation as a whole
does.

A trend strategy on daily bars trades rarely. `insufficient_data` is expected to
be common. If it is the *only* answer this design ever returns, that is a finding
about the design, recorded in `LIMITATIONS.md` — not a reason to lower a
threshold after seeing the counts.

**That is what happened.** The committed candidate took 8 round-trip trades
across the whole held-back window against a pooled floor of 30, so this design
as pre-registered cannot test this strategy. A ninth position was still open
when the window ended, paying no exit cost and counted in no fold while still
sitting inside the pooled Sharpe. `LIMITATIONS.md` records both figures and the
three honest routes out, none of which is moving the floor.

## Plateaus over peaks

The best single cell in a parameter grid is usually the luckiest one, not the
best one.

Candidates are therefore scored by the **median of their parameter
neighbourhood**, not by their own result. The median rather than the mean, so one
adjacent outlier cannot carry a neighbourhood. A setting worth trading is
surrounded by settings that also work — because nobody can know the "right"
lookback in advance, and an edge that survives only at exactly 17 bars is an
artefact of this particular history.

Two rejections follow from the same reasoning:

- **Edge winners are rejected.** A winner sitting on the boundary of the searched
  range is a wall, not an optimum: the surface was still improving when the range
  ran out. Widen the range and search again.
- **Degenerate plateaus are flagged.** If neighbouring cells produce
  near-identical *trade sets*, the surface is flat because the parameters are not
  doing anything — not because the edge is stable. Flatness from insensitivity is
  not robustness and must not be reported as a plateau.

Rejections are appended to a graveyard and never deleted. The rejection count is
the denominator that makes a survivor mean something.

## The noise ceiling

This is the correction most backtests omit, and the reason most published
backtests do not replicate.

**The expected maximum of many draws from noise is well above zero.** If you run
90 parameter combinations against a series with no exploitable structure, the
best of the 90 will still post a positive Sharpe — not because anything worked,
but because you took the maximum of 90 random draws. Report only that winner and
you have reported a selection artefact as a finding.

So the search can run the **entire grid** against surrogate series `n_trials`
times, recording the best-in-grid Sharpe achieved on each. That distribution is
the ceiling: what this search, on this data, at this grid size, achieves without
the real ordering of history to help it.

Two qualifications, both load-bearing. It runs only when asked — `--trials N`,
because each trial costs a full grid — so a plain search reports no ceiling and
says so. And the surrogates are **not** series with no edge in them: blocks are
as long as the grid's longest lookback, so each is a verbatim stretch of real
history and what the ceiling really measures is a reordering of this market's
own regimes. That is a harder test than noise and a narrower one.
[decisions/0008](decisions/0008-surrogate-block-length.md) argues out why no
block length avoids the trade.

A winning Sharpe is not evidence until it is compared against that distribution.
If it sits inside it, the search found the shape of its own multiplicity.

Two details matter:

- **The surrogate grid must be the same size as the real grid.** The ceiling is a
  property of how many combinations were tried. Searching fewer on noise
  understates it.
- **Block resampling, not an i.i.d. shuffle.** Shuffling returns destroys
  volatility clustering and autocorrelation — which is precisely what a trend
  strategy trades. A shuffled surrogate is an unfairly weak opponent and sets the
  bar far too low. Blocks preserve the local structure, so the surrogate is a
  strategy-relevant null rather than a trivially beatable one.

The noise ceiling is reported in every search result. It does not currently enter
the walk-forward pass decision — a limitation, stated in `LIMITATIONS.md`.

## Costs are fixed before the search

Transaction costs are the most effective dial for turning a losing strategy into
a winning one. A slippage assumption revised after seeing results is not an
assumption, it is a free parameter, and every statistic downstream of it becomes
meaningless.

The rates therefore live in `protocol.yaml`, set once from a defensible estimate
before anything ran. The *formula* lives in code, because it is market structure
and does not vary by broker: a buyer crosses **half** the spread, and commission
is a cash charge on the executed notional that is never folded into the executed
price. See `docs/decisions/0002-cost-model-shape.md`.

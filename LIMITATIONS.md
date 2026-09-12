# Limitations

Written before any result existed, which is the only time this is easy to write
honestly. Everything here is a reason to distrust a number this project
produces. Nothing here is a footnote.

## Most of the system is not implemented

Twenty-one modules have working bodies: `protocol.py`, `windows.py`,
`data/source.py`, `backtest/costs.py`, `backtest/metrics.py`,
`backtest/engine.py`, `core/config.py`, `core/strategy.py`, `search/grid.py`,
`search/plateau.py`, `search/graveyard.py`, `search/loop.py`,
`search/surrogate.py`, `search/noise.py`, `report/render.py`,
`report/surface.py`, `report/palette.py`, `validate/walkforward.py`,
`validate/candidate.py`, `logging/decisions.py`, `risk/limits.py`, and the `status`, `fetch`,
`search`, `report` and `validate` commands in `cli.py`. Eight `NotImplementedError` bodies
remain, across 2 files. Two further modules are mostly types rather than behaviour:
`core/types.py` holds the shared value types, and `execution/venue.py` holds the
venue interface and its data classes. `venue.py` has no tests; the one guard in
`types.py` is covered by `tests/test_strategy_config.py`.

**What is left is the live half.** `execution/t212.py` and the `run` and `diff`
commands - plus the wiring that would make anything emit the decision schema or
pass an intent through the risk gate. Both of those now exist and neither has a
caller, which is a different and smaller gap than a missing component, and worth
distinguishing: what is unproven about them is the integration, not the unit.
The research pipeline — fetch, search, report, validate — runs end to end.

**A strategy config can now be read, expanded, backtested and scored — by the
product, not only by a test.**
`core/config.py` parses and validates `configs/ma_crossover.yaml` into a
`StrategyDef`, and the "fixed vocabulary" the declarative-config rule depends on
is now a closed set enforced at load time rather than an example in a docstring.
`core.evaluate` now implements that vocabulary, so the loader is no longer the
only thing that knows what `crosses_above` means. `search/loop.py` joins the
whole chain and `strategylab search` runs it.
The vocabulary is still small — two indicators (`close`, `sma`) and three operators (`above`,
`crosses_above`, `crosses_below`), which is what the one committed config uses
and no more. `$placeholder` periods are still not resolved *into* a
`StrategyDef`, and deliberately so: `expand_grid` produces the `params` mapping
that `evaluate` looks each placeholder up in, which is what lets one parsed
definition serve all ninety cells. A reader expecting a definition with numbers
in it will not find one.

**A search now runs, and no figure from one appears in this repository.**
`strategylab search configs/ma_crossover.yaml` walks all ninety cells over
2005-2018, scores the surface by neighbourhood median, rejects edge winners and
drawdown breaches into the graveyard, and writes every cell to
`state/search-<stamp>.json`. No number from it is quoted here or in the README.

**Validation now runs, and the repository's own held-back window is still
unread.** `strategylab validate state/search-<stamp>.json` walks a search's
candidate through 2019-2024 and writes a verdict. `access_log.walk_forward_touches`
reads **0**, and no validation artifact is committed, because `state/` is
gitignored and nothing has been validated against the repository's own
`protocol.yaml`. The command was exercised end to end against a COPY of the
protocol and a copy of the data — which is what `walk_forward`'s `protocol_path`
argument exists for — so the counter that copy carries is the only one that
moved. The first real run is the operator's to make, and it is permanent.

**A verdict does not check the config it validates.** `read_candidate` compares
the search artifact's schema version, protocol version and price-snapshot
content hash against the ones in force, and refuses on any mismatch before a
touch is charged. It does NOT hash the strategy config: it loads
`configs/ma_crossover.yaml` from the path the artifact names, and a config edited
between the search and the validation would load silently. The verdict would then
describe a strategy no search ever nominated, and nothing in either file would
say so.

**A validated candidate starts flat.** The held-back run is warmed up on
`warmup - 1` in-sample bars but enters the window with no position, so a
strategy that would already have been long on 2019-01-02 is credited with
entering later. Carrying a position across the boundary is the alternative and
it is worse — an entry decided on in-sample data would appear as a held-back
result — but the approximation is real and it costs the first trade of the
window. See [docs/decisions/0009](docs/decisions/0009-how-the-held-back-window-is-run.md).

**The noise ceiling is off by default, which diverges from a rule `CLAUDE.md`
calls non-negotiable.** That rule reads "Every search reports its noise
ceiling"; `--trials` defaults to zero, so a plain `strategylab search` does not.
The reasoning, the four places the absence is disclosed, and the residual risk
that disclosure is weaker than refusal are recorded in
[docs/decisions/0008](docs/decisions/0008-surrogate-block-length.md). It is
named here rather than left to be discovered because a non-negotiable rule
softened by a default is exactly the kind of thing this document exists to
surface.

**The surrogates are not noise, and the word matters.** Blocks are as long as
the grid's longest lookback, so each is a verbatim stretch of real history at a
rescaled price level — about seventeen of them across the in-sample window.
Only the seams are cut. What the ceiling compares a candidate against is
therefore a REORDERING OF THIS MARKET'S OWN REGIMES, not a series with no edge
in it, which is what `CLAUDE.md` describes and what an earlier version of this
file implied. The bias runs towards a harder bar, and it has no principled
bound: at one block the surrogate would be the real series and the ceiling would
measure the strategy against itself. `block_resample` refuses fewer than eight
blocks to put a floor under that, which limits the degeneracy without making the
bias small.

The conflict is real and no block length resolves it — a fair opponent for a
200-bar trend strategy must contain 200-bar structure, and that structure is the
thing being tested.
[docs/decisions/0008](docs/decisions/0008-surrogate-block-length.md) argues it
out. **Read a cleared ceiling as "beat a reshuffling of this market's own
history", which is a strong claim, and read a ceiling that is not cleared as
close to fatal.**

**How the ceiling behaves when it IS asked for.** `--trials N` re-runs
the whole grid against `N` block-resampled surrogates and reports where the
candidate falls in that distribution. Without it the artifact records
`"noise_ceiling": null` and every output says nothing printed is evidence of an
edge. The default is zero because one trial costs a full grid — about four
minutes — so a hundred trials is most of a day. That default is a real hazard
worth naming: the cheap path is the unbenchmarked one, and a reader who runs
`search` without reading its output will get a plausible Sharpe and no warning
they sought out.

**The ceiling informs; it does not gate.** `select_candidate` never receives it,
so a candidate can be nominated, written to the artifact and drawn on a report
while sitting inside the noise distribution. Only a human reading the comparison
will notice. This is the same gap the validation section records for
`walk_forward`, now present one stage earlier.

**Fewer than ~100 trials cannot support a threshold.** The ceiling is read by
its upper percentiles, and with twenty trials the 95th percentile is essentially
the maximum of twenty draws — it moves a long way between seeds, so the
comparison measures the resampler's luck. `trials_note` says so on every run and
in every artifact. Nothing refuses a short run, because a short run is a
legitimate way to see the machinery work and refusing it would push people
towards not running it at all.

`state/` is gitignored, so no search output is committed and none is
reproducible from this repository alone — regenerating one needs a `fetch`
against a vendor that revises history. What IS reproducible is the run against a
given cached snapshot: the artifact records that snapshot's content hash.

**There is a validation outcome and still no benchmark.** `validate/` is built
and `strategylab validate` runs, but nothing anywhere computes buy-and-hold — so
no result, in-sample or held-back, can be compared against simply owning the
index, which a long-only trend strategy on the S&P 500 must beat to be worth
anything. A `pass` from this validator means "cleared a pre-registered Sharpe
bar", not "beat the index".

**There are still two decision-log shapes, and now one of them is built and
unused.** `docs/architecture.md` states the schema is defined once, in
`logging/decisions.py`, because a backtest log and a live log have to be
diffable - that is what makes `strategylab diff` possible at all. That module is
now built, tested and reachable: `DecisionRecord` round-trips through
`write_decisions`/`read_decisions` exactly. **Nothing emits it.** `run_backtest`
still writes its ad-hoc seven-column frame, and the live worker does not exist.

So the "defined once" rule is no longer aspirational in the sense that the one
definition is missing - it is aspirational in the sense that nobody has adopted
it. That is a smaller gap and a different one, and it is worth being precise
about which: the risk was that two shapes would drift apart, and it is now that
one shape sits unused beside another that is in use.

**What blocked the backtester adopting it used to be two things, then one, and
is now a decision nobody has made.**

The first blocker is GONE. `Intent` now carries `indicator_values` and
`thresholds` as numbers rather than only as rendered strings, so `core.evaluate`
supplies what a record needs. That was a change to the shared brain and it is
its own commit.

The second blocker is gone in the sense that mattered and open in a way that did
not exist before. `risk/limits.py` is built and tested, so `risk_verdict`,
`risk_reason` and `approved_size_pct` CAN now be filled with something true. What
nobody has decided is what a BACKTEST should put there, and both available
answers cost something:

- **Run the gate in the backtest too.** Then the two logs are produced the same
  way and `diff` measures the simulator, which is its whole purpose. But the
  backtest stops being a measurement of the strategy: a kill switch, a daily-loss
  halt and an exposure cap would suppress trades, and every Sharpe this project
  reports would be the Sharpe of a strategy-plus-risk-layer. The searches already
  recorded did not do that, so adopting it invalidates comparison with them.
- **Do not run it, and record that it did not run.** Then the backtest keeps
  measuring the strategy, but a live log and a backtest log of the same period
  differ in those three fields BY CONSTRUCTION, and `diff` will report harness
  drift as though it were simulator drift unless it is taught to exclude them.

Neither is chosen here, and the unchosen decision is the reason nothing emits a
`DecisionRecord` yet. What is no longer true is the previous claim that the only
honest thing available was "the layer is not built".

**The keying of `indicator_values` IS now settled: by reference.**
`sma($fast)` and `sma($slow)`, not `sma` twice - so `close above sma(150)` and
`close above sma(50)` in one condition list no longer collide, which was the
case this entry used to describe as unresolved. The searched period is left as
its placeholder because the parsed definition is never rewritten, and the
`params` mapping that resolves it travels in the same record. `logging/` still
does not enforce the convention - it serialises whatever mapping it is handed -
but `core/` now produces one, and `Intent`'s docstring is where it is written
down.

**A literal comparand is a threshold, not an indicator value**, and two literals
that render identically at six significant figures but differ in value are
REFUSED rather than silently merged. That refusal is reachable: the config
loader accepts a bare number as a condition's `target` today, so `close above
400` is expressible now.

The refusal lives in TWO PLACES on purpose. `load_strategy` refuses the pair
when a config file is read, which is where validation is declared complete;
`core.evaluate` refuses it again for a `StrategyDef` built in code. The loader
is the one that matters, because the evaluator inspects the entry list only on
a bar where no exit condition holds - so a collision there could otherwise sit
undetected through every exiting bar of a run.

**The engine's fill convention is a decision, not a detail.** A signal computed
from bar `t`'s close is filled at bar `t+1`'s OPEN. Filling at the same close
would transact at a price only known once that bar had closed, which is the
error that flatters most amateur backtests. The cost: `apply_costs` prices
against a mid and an open is a traded price, so using it as the mid is an
approximation — a smaller error than the one it avoids, and stated rather than
hidden. The last bar's signal is recorded and never filled.

1420 tests pass (`uv run pytest`). They cover the protocol loader, the window
accessors, the data layer, the cost model, the strategy-config loader, strategy
evaluation, the performance metrics, the backtest engine, grid expansion,
plateau scoring, the graveyard, the search loop, the noise ceiling, the reports,
walk-forward validation, the candidate reader, the decision log and the risk
layer — eighteen areas, one per test file. 486 of the 1420 are the risk layer's,
and that is a statement about parameterisation rather than about importance. 316
of those 486 are assertions repeated at four account equities, because at an
equity of 100 a cash exposure and its percentage of equity are the same number.
`test_the_committed_daily_loss_ceiling_binds` runs at one account size instead,
because bracketing the shipped 3% ceiling wants round cash amounts rather than the
same percentage at four; it asserts a verdict rather than a number, so nothing
there needs to be exact in binary. The other 170 items exercise the loader, the
type's range check and the gate itself, and no clean partition divides them
because several tests use two of the three. 109 assert an exception and 61 a
returned value, 24 of those a verdict from `check`, 23 of which carry a
rejection.

Five invariants are asserted over forty cases built from twenty distinct
account states. Every number in this paragraph has been wrong at least once: forty
cases were written as forty states; all non-fan-out items were called
refusals; the 106/61 split was called 106/58 with the wrong label on each half;
and then 61 was called a count of verdicts when only 24 of them are. Four audit
rounds went into one paragraph of counting. These figures are re-derived from the
test collector rather than from the previous draft, and the lesson
`docs/decisions/0006` records — that this project's prose about its own numbers
fails more often than its arithmetic does — is now evidenced here as well as
there.

What has no tests is not only the stub bodies, and an earlier version of this
paragraph said it was. Three FINISHED areas are untested: **the CLI**, all five
of `status`, `fetch`, `search`, `report` and `validate`; the venue interface in
`execution/venue.py`; and `YFinanceSource`'s fetch path, which by policy no test
may call. The CLI is the one that keeps growing — `search`, `report` and
`validate` together are several hundred lines of argument handling, error paths
and output formatting, and every failure branch in them is unexercised: a
missing config, an unparseable one, an uncached instrument, an unreadable
snapshot, an artifact that will not render, a candidate from the wrong snapshot.
The functions beneath are covered; the commands wrapping them are not.

**`validate` is the one where that gap costs something.** Its failure branches
are the only ones in this project that can be re-entered only at the price of a
permanent touch, and they are the branches nothing exercises. The refusals that
happen BEFORE the read are covered — `tests/test_candidate.py` drives every one
of them through `read_candidate` — but the wiring that calls it, and the error
handling around the two calls that can fail after the touch is charged, is not.

Untested too is every type definition the remaining stub module carries beside
its `NotImplementedError` bodies — `T212Error`, the two base URLs, and the two
rate-limit constants in `execution/t212.py` — along with `execution/venue.py`'s
`Order`, `Position`, `Balance`, `ReconcileReport` and `OrderType`. Those are real
code, some of it load-bearing for a later component's schema, and nothing
exercises any of it. That list keeps shrinking as the components arrive:
`FoldResult`, `ValidationResult` and `Outcome` left it when
`validate/walkforward.py` was built, `DecisionRecord`'s sixteen fields left it
with `tests/test_decisions.py`, and `RiskLimits`, `PortfolioState`, `RiskVerdict`
and the `Verdict` enum have left it now — `tests/test_risk.py` exercises all
four. The default path's VALUE is pinned there too, but nothing calls
`load_limits()` with no argument and nothing should: the default is
working-directory-relative, so a test resting on it would be a test about where
pytest was started from.

**The lookahead test exists, is green, and CANNOT FAIL.** `core.evaluate` is
handed a window whose last row IS the bar being evaluated, so it cannot read the
future in the ordinary sense — there is no future in the frame it receives. The
test the project prompt specifies, corrupting every bar after `t` and asserting
the intent at `t` is unchanged, slices the corrupted rows away before the call:
both invocations receive identical data, and no implementation could fail it. An
earlier version of this entry called it "weaker than its name", which was too
generous. It is kept because it pins the signature, and it asserts its own
vacuity so the suite cannot be misread as covering lookahead.

The test that carries real weight is the other one: the intent at a bar must not
change with how much history precedes it. That is what fails when a rolling
window is quietly an expanding one — a bug that does not read the future, reads
too much of the past, and produces the same symptom, a backtest that cannot be
reproduced live. Both are in `tests/test_strategy.py`.

**The determinism guarantee now has its test.** `run_backtest` consults no clock
and no random source, and `tests/test_engine.py` asserts that the same inputs
produce an identical equity curve, decision log, fill list and metrics.

**And the lookahead guarantee finally has a test that can fail.** The engine holds
the whole frame and chooses what to hand `evaluate`; that choice is the entire
guarantee. `test_corrupting_the_future_does_not_change_any_earlier_decision`
corrupts every bar after `k` and passes the frame in INTACT — nothing is sliced
away before the call — so an engine that peeked by a single bar produces
different earlier decisions and the test says so. That is the first test in this
project to carry the claim the whole design rests on.

An earlier version of this entry added "widening the slice by one bar turns seven
tests red". That figure is gone for two reasons, and both are worth stating. It
was WRONG — seven was the count within `tests/test_engine.py` alone and the
sentence did not say so, and the figure across the whole suite is larger. No
replacement number is quoted here, because a replacement would have the same
problem as the original. And it was not
reproducible: the mutation was run from a scratch directory and no harness is
committed, which is the same defect this file labels on the content-hash
measurement below and did not label here.

## What this project's own verification is worst at

Worth stating in the document whose purpose is distrust, because it is a
limitation of the evidence rather than of the code.

**The documentation here was written by the same process that wrote the code,
and it is the documentation that keeps turning out wrong.** Four read-only
auditors run against every component this repository has built. The record of
what they found is in `git log`: each round is followed by a `fix:` commit whose
message names the defect. Read those.

Independent recomputation of the hand-derived numbers in the tests has not yet
found a wrong value — that has held through every component, including the three
in `search/`. Independent reading of the prose has found something untrue every
single round: a test described as covering more than it could, a paragraph left
standing beside the paragraph that superseded it, a figure quoted from a harness
that was never committed.

An earlier version of this paragraph went further and said "the errors are not in
the arithmetic", meaning the audits only ever caught prose. That is no longer
true, and `docs/decisions/0006` records the counter-evidence: building `search/`
produced silently expanding lookbacks of zero and minus ten, two `TypeError`s
escaping a contract admitting only `ValueError`, and a graveyard refusal leaving
a zero-byte file that reads as a search with no rejections in it. The audits
catch code defects too. What is still true is the DIRECTION — every finding,
prose or code, has been an overstatement of how good the verification is.

So: **trust the numbers in this repository more than the sentences around them**,
and trust neither more than the command that regenerates them. Where a figure
appears without a command beside it, that is the shape the mistakes have taken
here before.

`docs/decisions/0006` records what the audits catch and what they structurally
cannot.

## The instrument

**Research and execution are different funds.** The search runs on `SPY`, a
USD-denominated, NYSE-listed, distributing ETF. Anything actually traded would
be `VUAG.L`, a GBP-denominated, LSE-listed, accumulating ETF. They track the same
index; they are not the same security, and the gap matters:

- **VUAG.L did not exist for most of the in-sample window.** It launched in 2019.
  There is *no* VUAG.L history across 2005–2018. Every in-sample result is a
  statement about SPY and an assumption about VUAG.L.
- **Different exchange hours.** A signal computed on a SPY close cannot be
  executed at an LSE close on the same day. The backtest ignores this.
- **Currency.** A GBP account holding a GBP fund tracking a USD index carries FX
  exposure the backtest does not model.
- **Tracking error and fees.** The two funds have different expense ratios and
  different tracking error against the index.
- **Distributing versus accumulating.** SPY pays dividends out; VUAG.L reinvests
  internally. Total-return construction differs between them.

**One instrument, few distinct regimes.** Ninety parameter combinations are
tested against a handful of macro conditions. A candidate that clears the noise
ceiling on one instrument has cleared a low bar. It is a reason to keep looking,
not a finding.

## The cost model

**Two cost components are zero, and that is now a decision rather than an
omission.** `commission_min_flat` and `fx_conversion_pct` are set explicitly in
`protocol.yaml`, both to `0.0`: the execution account is a Trading 212 stocks and
shares ISA, which charges no commission and no per-trade minimum, and the
execution instrument `VUAG.L` is GBP-denominated on the LSE, so a GBP account
converts nothing to buy it. Reasoning and its limits in
[docs/decisions/0004](docs/decisions/0004-two-cost-components-decided-at-zero.md).
Read that before reusing these numbers: the FX zero is a property of that
account-and-instrument pairing, not of the broker, and executing `SPY` directly
would make it wrong.

**A commission-free broker is not a cost-free one.** The spread is still charged
at `0.03` percent and slippage at `0.05`, on every fill. That is where the cost
of commission-free execution actually sits.

**The fund's own ongoing charge is not modelled anywhere.** `VUAG.L` levies an
annual management charge against the fund's assets. It is not a transaction cost,
so `CostModel` has no term for it: `apply_costs` charges per fill, and nothing in
this project charges anything per unit of time held. A trend strategy holding for
months pays that charge and the backtest will not show it. This is the cost
omission most likely to matter to a result, and it is a code change to fix, not a
config edit.

**Missing entirely:** taxes of any kind, interest earned on uninvested cash, and
borrow costs. UK stamp duty is not charged on this instrument, so its absence
from the model is correct rather than a gap.

**Paper fills are optimistic.** No market impact modelling — the executed price
does not move with size. No partial fills. Fractional shares are assumed
throughout; there is no lot size or minimum order size.

## The validation design

**The pass bar is a point estimate against a fixed number.** Walk-forward
compares pooled Sharpe to `min_sharpe + pass_margin` (1.20). No standard error,
no confidence interval, no p-value, and no deflated Sharpe is computed anywhere.

**The noise ceiling does not enter the pass decision.** It is reported by the
search when asked for, so it can inform which candidate a human chooses.
`walk_forward` never receives it, and neither does `select_candidate`. A
candidate can therefore be nominated by the search AND pass validation while
sitting inside the noise distribution, and nothing in the code will stop that at
either stage. The ceiling is a number a person must read and act on. What the
validator does do is carry the search's own caveat forward verbatim — a verdict
on a candidate never benchmarked against noise prints and records the sentence
saying so — which is disclosure, not a gate.

**Nothing computes a benchmark.** There is no buy-and-hold comparison anywhere.
A long-only trend strategy on the S&P 500 over 2005–2024 must beat simply
holding the index to be worth anything, and no module computes that number.

**Folds contain far too few trades, and this is now measured rather than
feared.** `min_trades_per_fold` is 10 and `min_trades_pooled` is 30. The
committed candidate — `fast=10, slow=80, trend=150` — took **8 round-trip trades
across the whole of 2019-2024**, between 0 and 3 per twelve-month fold. Every
fold reports `insufficient_data` on its own count, and so does the pooled
decision, at 8 against a floor of 30.

Read those per-fold counts carefully: a fold's count is trades CLOSED in it, so
a `0` does not mean a year of inactivity. 2021 shows zero while holding a
position opened on 2021-10-20 straight through into 2022.

**The held-back run ends holding a position, and that position is in the
figures.** Nine entries, eight exits: the trade opened 2024-08-20 was still open
on 2024-12-31, 19.61 units held for the last 93 bars — 6.2% of the window.
Nothing liquidates at the window edge. So that ninth position **pays no exit
cost** — no half-spread, no slippage, no commission on the way out — it is
**absent from the `8` above**, because `total_trades` counts round trips
completed, and its **mark-to-market is nevertheless inside the pooled Sharpe and
the pooled drawdown** the verdict rests on, because the engine marks equity to
every close.

The direction of the bias is not knowable in advance: an open position that
happened to be up flatters the Sharpe, one that happened to be down depresses
it, and either way the exit cost is missing. It is not corrected because the
alternatives are worse — forcing a liquidation invents an exit nobody took and
changes what `run_backtest` means for every caller, and truncating the curve at
the last exit discards real bars. It is stated instead. Any future window will
have the same property at its right edge.

That figure comes from a development run against a COPY of `protocol.yaml`; the
repository's counter still reads 0 and no artifact from it is committed. It is
recorded here anyway, because it is a finding about the validation design and
whoever spends the real touch should know it before they spend it.

**This design, as pre-registered, cannot test this strategy.** A 150-bar trend
filter on daily bars produces one or two round trips a year; six years of
held-back data cannot reach thirty. The floors are not being changed — a
threshold lowered after seeing the count is not a threshold, and that is the
exact failure the pre-registration exists to prevent. The honest routes out are
a strategy that trades more often, a longer held-back window, or a
pre-registered decision recorded in `docs/decisions/` with the access counter
reset. All three are the operator's call, not the code's.

**Six folds is not six independent regimes.** The held-back window is 2019–2024,
which contains one COVID crash and one 2022 drawdown, on a single market path.

**Repeated validation erodes independence.** Every run against the held-back
window is recorded in `access_log.walk_forward_touches`. After enough runs that
window is no longer held back in any meaningful sense. The counter reports the
erosion; it does not correct for it, and no statistical adjustment is applied.

## The data

**Every bar is a back-adjusted price, not a traded price.** `YFinanceSource`
requests `auto_adjust=True`, so each historical close is the raw close scaled
by every split and dividend that has happened *since*. No trade ever occurred
at any of these numbers — SPY really closed near $120 on 2005-01-03, and the
cached bar reads about $81. Two consequences worth stating: a backtest
denominated in these numbers measures total return rather than price return,
which is usually what you want but is not what a naive reading assumes; and
the entire history shifts every time a dividend is paid, so the numbers in a
cached snapshot are only the numbers this vendor reported on the day it was
fetched.

That is NOT established as the cause of the content-hash instability described
below. The drift measured there was between two fetches ninety minutes apart,
with no dividend in between, and it is far smaller than a dividend would
produce. The mechanism is unexplained. Recording it as measured rather than
attributing it to a cause that has not been demonstrated.

**Vendor history is revised.** Splits and dividend adjustments change past
values, so "same code, same dates" does not mean the same numbers six months
from now. Results are reproducible against the cached snapshot and its recorded
content hash, not against a fresh download. The hash detects that a revision
happened — it does not prevent one from having already changed a result.

**One vendor, never exercised by a test.** Yahoo Finance via `yfinance` is the
only source, and by design no test calls it. The fetch path is tested against
recorded fixtures only.

**Daily bars only.** No intraday, tick, or minute data. Execution timing within a
day is therefore unmodelled.

## Capability boundaries

- **Long only.** The strategy layer emits `ENTER_LONG`, `EXIT_LONG`, `HOLD`.
  There is no short side.
- **Market orders only.** A limit order that does not fill would change realised
  behaviour in a way the backtester does not model.
- **One strategy family, one config.** `configs/` holds a single moving-average
  crossover. Nothing has been compared across strategy types.
- **`is_degenerate` is now usable, and its fingerprint has one soft edge.**
  `search/loop.py` builds the `trade_fingerprint` the check reads, and
  [docs/decisions/0007](docs/decisions/0007-what-goes-in-a-trade-fingerprint.md)
  records what goes in it and why the two cases `plateau.py` flagged get
  opposite treatments: cells that took no trades share one value, because they
  genuinely did the same nothing and a region of them *should* flag; cells whose
  backtest raised each get a distinct value, because nothing ran and a region of
  failures must not read as a region of agreement. The soft edge is that the
  fingerprint hashes the executed FILLS rather than the trade dates, so two
  cells that traded on different days whose opens are bit-identical floats, in
  an otherwise identical sequence, would collide. Vanishingly unlikely on price
  data, not impossible, and nothing detects it. A related trap: because it
  hashes prices, a fingerprint is only comparable within one search — change the
  cost model or the fill convention and every fingerprint in the grid changes.
  Nothing stops someone comparing two runs' fingerprints.
- **Plateau scoring cannot detect a missing parameter level.** It refuses a
  results frame with rows missing, because a hole makes cells two grid steps
  apart look adjacent. But drop EVERY row at one value of a parameter and what
  remains is a complete smaller grid, indistinguishable from a search that
  declared fewer values — and the neighbourhood walk silently spans the gap.
  Detecting it needs the declared `search_spec`, which `plateau_score` does not
  receive. It is the caller's responsibility and nothing enforces it.
- **The neighbourhood radius is a call-time default, not pre-registered.** It
  defaults to 1 and is not in `protocol.yaml`, so it is a parameter of the
  scoring that a caller could vary after seeing results. `DEGENERATE_SHARE` is
  the same: a module constant at 0.8. Both are reporting choices rather than
  pass criteria — nothing in `validation` reads them — but neither is protected
  by the write-once rule that protects the pass bar.
- **Three searched parameters, 90 grid cells.** The config's own comment notes
  that this much data supports three or four parameters at most. `expand_grid`
  confirms the count is 90; nothing yet decides which of the 90 to believe.
- **Integer parameters only.** Lookbacks are whole bars, and the loader refuses a
  config where they are not — every bound of a searched range, not merely its
  start, because a fractional step puts some of the cells it generates off the
  whole numbers. (Not *all* of them: `step: 2.5` from `min: 5` gives 5, 7.5, 10,
  12.5, so every other cell is whole. An earlier version of this line claimed
  every cell after the first was fractional, which is true of `step: 0.3` and
  false of `step: 2.5`.) `expand_grid` enforces the same rule again, independently
  of the loader, and adds the floor the loader also applies: no bound below one
  bar. That second check exists for a spec built by hand rather than read from a
  config, which is the only way to reach the grid without passing the loader
  first.
- **A wiped-out account raises instead of reporting a total loss.** `max_drawdown`
  refuses a non-positive equity, because a drawdown is a fraction of a peak, so an
  account driven to zero or below propagates a `ValueError` out of `run_backtest`
  rather than reporting a hundred percent drawdown. The engine's own docstring
  calls this a real limitation and not a considered decision, and it matters for
  the search: a cell that blows up does not score badly, it fails. What to do
  with one is now decided, though nothing executes the decision yet:
  `search/plateau.py` requires the row to be kept with a `NaN` metric, so the
  grid stays complete and the failure is visible in `neighbour_count`, and
  `search/graveyard.py` carries a `backtest_failed` reason to record it under.
- **Zero risk-free rate, and not configurable.** Sharpe is computed against zero.
  The 2005–2024 window spans rates from 5% to 0% and back to 5%, so Sharpe
  figures are not comparable across it.
- **Config expressiveness is deliberately capped** — flat AND-only condition
  lists, no nesting, no OR, no expressions.

## Live trading

**Demo only.** Trading 212's demo environment, by construction. No live API key
exists for this project and creating one is a separate, later decision. No
live-money result exists or will.

**Live trading tests the simulator, not the strategy.** There will never be
enough live trades to judge a strategy. The purpose of the paper worker is to
measure drift between predicted and actual behaviour.

**The T212 order endpoint is not idempotent.** A repeated request can create a
duplicate order, so no retry is ever automatic. Rate limits are one account
summary per five seconds and six historical requests per minute.

## Gaps in the tooling itself

These are limitations of the code as it stands, not of the method.

**The ingest gate is all-or-nothing, and can refuse correct history.**
`validate_bars` raises on the first violation and `fetch` then caches nothing —
so a single bad or unusual bar anywhere in twenty years refuses the entire
download. Two rules are strict enough to hit real data: more than three absent
weekday sessions (a genuine multi-day exchange closure longer than the 2012
Hurricane Sandy shutdown would trip it), and any bar reporting zero volume (a
half-day session, or a vendor quirk). This is deliberate — the alternative is
silently repairing data — but it means the tool can decline to ingest history
that is perfectly real.

**The held-back window is cached in the same file as the in-sample window, and
exactly one consumer is guarded.**
`fetch` downloads 2005-01-01 through 2024-12-31 in one request and writes one
parquet file. Nothing partitions it, and the whole 5,033-bar frame is what
`run_search` is handed. The split is this project's central claim, and it is now
enforced for the search by `windows.in_sample_bars`, which cuts the frame from
`protocol.windows.in_sample` and from nothing else, with a test that corrupts
every bar after the window's end and asserts no cell's result moves.

**The guard leaked once, through its own input checks, and that is worth
recording.** Validating the bars is not the same as reading them for a result,
and it is easy to feel exempt. It is not: a version of the loop's price checks
ran over the frame as handed in rather than over the slice, so a single bad bar
anywhere in 2019–2024 refused a search of 2005–2018. No held-back value reached
a result, but whether a result existed at all depended on them. Fixed by
splitting the checks — only what is needed to CUT the window runs before the
cut, and everything that reads a value runs after it — with a test asserting
that a corrupted held-back bar produces a byte-identical surface. The general
lesson is the one this file keeps relearning: the leak arrived in code written
to make the system safer.

That WAS one consumer and not a chokepoint, while the slicer was a private
helper inside the search loop. **That is now fixed rather than pending:**
`windows.py` owns both
accessors, the loop's private copy is deleted, and any future component asks the
same function. Reading the held-back window additionally increments the
protocol's counter and cannot be made not to — there is no dry run, because a
free look is what the counter exists to prevent.

One hole in that has since been closed, and it is worth recording because it
was invisible: `in_sample_bars` trusts the protocol it is handed, and a
`Protocol` built IN CODE with overlapping windows made it return held-back bars
for free — measured at 129 of them, with the counter untouched. `load_protocol`
refuses an overlapping pair read from a file; nothing refused one from a
constructor. It is now an invariant of `Windows` itself, so there is no
overlapping pair to hand anybody. Two of this repository's own tests turned out
to be relying on constructing one.

What remains is that nothing *enforces* the use of that module. A component
could still slice a cached frame with plain pandas and nobody would notice. The
chokepoint is a convention backed by one obvious place to go, not a mechanism.

**`increment_walk_forward_touches` now has a product caller, and has still never
been called in anger.** The chain is complete: `strategylab validate` →
`validate.walk_forward` → `windows.walk_forward_bars` → the counter, incremented
before a single held-back bar is returned. The repository's counter reads **0**
because nobody has run that command against the repository's own
`protocol.yaml` — not because nothing could. That is a weaker guarantee than the
one this entry used to record, and it is the correct one from here on: the
number is now evidence of a choice rather than of an absence.

**Two earlier entries here are now historical.** They said no production code
path invoked the counter, because the only thing that should — walk-forward
validation — did not exist. It exists. What survives from them is the reason the
distinction mattered: a counter at zero means something quite different when a
reader exists, and this document should not let that shift pass silently.

**The fetch path itself has no test.** `YFinanceSource.fetch` and the
vendor-frame normalisation behind it are the one part of the data layer that
touches the network, and by policy no test may call them. What is tested against
the recorded fixture is the cache path. The vendor adapter is exercised only when
a human runs `strategylab fetch`.

**The CLI cannot fetch the execution instrument.** `fetch` downloads
`instruments.research` and only that. There is no command that will obtain
`VUAG.L`, so the execution-side price history this project would need to model
real fills does not exist anywhere in the repository.

**The content hash changes on almost every re-fetch, so it cannot do the job it
was designed for.** It was intended as an alarm for vendor revisions. Measured:
two fetches of the same ticker over the same span, ninety minutes apart on the
same machine with the same code, produced 5,033 rows each with an identical
index — and **4,161 of those rows differed**, by up to 2.1e-4. A second,
independent pair of snapshots taken eight minutes apart showed 210 of 253
overlapping bars differing bidirectionally at 7.7e-7 relative — neither the
shape nor the magnitude a dividend produces.

Why the values move on this timescale is **not established**. It is recorded
here as a measurement, not as an explanation.

That measurement came from a paired comparison run during an audit — two fetches
separated in time, then compared row by row. It is not reproducible from a
single command, and nothing in this repository re-runs it.

The consequence is that the hash distinguishes "this is not the snapshot the
result was computed against" — which is real and useful, and is what
`cache_read` enforces — but it CANNOT distinguish "the vendor revised history
in a way that changes a conclusion" from "someone re-ran fetch". Do not read a
changed hash as evidence of a meaningful revision.

Related and smaller: the hash normalises to ten decimal places, so two frames
differing below that threshold hash identically. Given the above, that bound is
about a million times finer than the noise it sits in.

**`fetched_at` is caller-supplied and unverified.** The provenance record is only
as honest as whatever called `cache_write`.

**The cost rates have no recorded source.** `protocol.yaml` fixes commission,
spread, and slippage before any search runs, which is the important property —
but it does not record where those three numbers came from, and the project's own
cost-arithmetic standard asks that it should.

**The search's window test needed two assertions, and the first version had
one.** `test_bars_outside_the_window_cannot_change_any_cell` corrupts every bar
after the window's end and asserts no cell's result moves. On its own that is
blind to the smallest and most plausible off-by-one. A signal from bar `t` fills
at bar `t+1`'s open, so the last bar of a window never produces a fill; one
leaked bar therefore reaches only a mark-to-market, and where every cell is flat
at the window's end it changes nothing in either run. Measured on the committed
fixture: extending the slice by one row leaves the results frames
bit-identical, by two rows turns them red.

The test now also asserts the bar COUNT, which fails on a one-bar leak, and both
assertions sit in the test the documents nominate. This is recorded rather than
quietly fixed because it is the second time in this repository that a test named
in a rule turned out weaker than its name — `tests/test_strategy.py`'s lookahead
test is the first, and it is documented above. The pattern is worth more than
the individual fix: a corruption test only detects a leak that reaches something
the metric depends on.

**A bad input could look like a research finding, and the first fix for it was
not enough.** `run_backtest` raises `ValueError` both for things that are
properties of a CELL — a warm-up longer than the window, an account driven to
zero — and for things that are properties of the DEFINITION or the DATA: an
empty condition list, a placeholder nothing supplies, an indicator the engine
does not implement, a non-finite bar, a non-positive price, a non-numeric
column. The loop catches `ValueError` per cell and records a failure, so the
second kind returned an ordinary-looking result saying "90 of 90 cells failed",
which this project's own documentation called a legitimate finding, while
appending ninety permanent records to an append-only graveyard.

The first attempt at closing this checked the frame-level conditions up front
and claimed what remained was "cell-specific by elimination". An audit named six
causes it did not see. That is worth recording as a pattern and not just a bug:
a list of checks mirroring another module's internals is always one edit behind
it.

What closes it is structural rather than enumerative. If EVERY cell fails there
is no surface, and that is a `SearchError` whatever caused it, carrying the
first failure's own message so the fault is named. The graveyard is written once
at the end, so a run that produces no result records nothing.
`loop._check_inputs` remains, for the diagnosis rather than the guarantee.

**The structural guarantee has a hole, and it is the shape of fault it cannot
see.** "Every cell failed" catches a uniformly fatal problem. A fault that fails
MOST cells and not all slips straight through it, arriving as a scattering of
failed cells across the surface — which is indistinguishable from a legitimate
result where some parameter combinations simply could not warm up. Three such
faults are known and now checked up front: a non-positive or non-finite price
(fails only the cells that read that bar), a missing or out-of-range
`fixed.position_size_pct` (fails only the cells that reach an entry), and an
indicator whose arity is wrong (which escaped as an `AssertionError`, a type the
contract did not admit at all, and under `python -O` would have silently used a
nonsense rolling window). What is NOT covered is any future fault of the same
shape that nothing anticipates. The backstop cannot see it and the input gate
only looks for what it has been told to look for.

The two kinds of check in that gate rot differently. The frame checks mirror the
engine's private validation and CAN fall behind it — when they do, the
structural guarantee still fires and only the error message degrades. The
definition checks read the config vocabulary. An earlier version of this
paragraph said they therefore "cannot drift from the engine without the engine
having already drifted from the loader", which overstates it: what holds is that
a NAME-level divergence between check and engine implies a name-level divergence
between loader and engine — something nothing in this repository detects and no
test covers. It does not extend beyond names, which is exactly why arity and
position size needed their own checks rather than following from the vocabulary.

**The report meets two of the five interactions its own standard requires.**
`report-design` and `render`'s contract both ask for five things. Hovering a
cell for its metrics works, and every figure carries its protocol version,
snapshot hash and originating command. The other three cannot exist yet and the
gap is structural rather than unfinished work:

- **No equity curve on click.** The search artifact stores per-cell metrics and
  no curves. Ninety curves over 3,523 bars is tens of megabytes of JSON against
  a 38 KB file, so this needs a decision — downsample, store only the top few,
  or recompute on demand — and nobody has taken it.
- **No noise ceiling on the axis.** The standard is emphatic that the ceiling
  belongs on the SAME axis as the result, so a winner inside the noise
  distribution is *visible* rather than computed. A ceiling now exists, but the
  page reports it as a sentence and a percentile — the reader has to do the
  comparison the figure was supposed to make for them. Worse, the placement is
  computed from the artifact's stored percentiles rather than the raw trials, so
  it is coarse: it can say "above the 95th" or "around the 50th" and nothing
  between. The band is roadmap session 8.
- **No fold panels and no two-run comparison.** `validate/` now produces folds
  and writes them to `state/validation-<stamp>.json`, but `report/` reads only
  `search-*.json` and nothing yet loads two result sets. A verdict is therefore
  readable as JSON and invisible in every figure. These are roadmap session 8.

Reading the figure while three of its five affordances are missing is the risk
worth naming: it looks finished, and a heatmap of unbenchmarked Sharpes is
exactly the kind of thing that persuades by being well drawn.

**The colour scale is a sampled approximation of viridis, not viridis.** Ten
anchor stops with linear interpolation between them, hand-coded so the page
needs no plotting dependency. The property that matters — lightness rising
monotonically, no manufactured edges at a colour transition — survives the
sampling and is asserted by a test. The exact 256-entry table is not
reproduced, and a reader matching these colours against a matplotlib figure
will find small differences.

**A full search takes about four minutes, and the noise ceiling multiplies it
by the trial count.** Measured: `time uv run strategylab search
configs/ma_crossover.yaml` took 4m02s for ninety cells over 3,523 bars on the
machine it was run on. `--trials N` costs `N + 1` grids, so a hundred trials is
on the order of seven hours. The choice was to keep the ceiling exact and make
the cost explicit — the command prints the estimate before starting — rather
than shrink the surrogate grid, which would understate the ceiling, since the
ceiling is a property of how many combinations were tried.

The engine's cost was measured rather than assumed, and an earlier version of
this entry got the reason wrong. It said the engine "re-derives the moving
averages on every bar of every cell", implying quadratic behaviour. Timed across
400, 800, 1,600 and 3,200 bars the scaling is roughly LINEAR — 2.1x for 2x bars
— so the cost is per-call overhead, not the rolling recompute. There is no
cheap win hiding there: making the ceiling affordable means restructuring how
`evaluate` is called, which is an engine change and a separate piece of work.

**A stub command prints your home directory.** The two unbuilt CLI commands —
`run` and `diff` — raise `NotImplementedError`,
and the resulting traceback contains the absolute path of the file that raised
it, which on most machines includes your username. Nothing is written anywhere
and nothing leaves the machine, but if you paste such a traceback into a bug
report or an issue, that path goes with it. `status` prints no ABSOLUTE paths
for that reason — it names `configs/ma_crossover.yaml`, which is a repository
path and carries nothing about the machine it ran on.

`search` follows the same rule and extends it to a file rather than only to a
terminal. It catches its own errors and prints a message instead of a traceback,
prints only repository-relative paths, and reduces the config path to a relative
one — falling back to the bare filename — before recording it in the artifact,
because a search result is a file this project's documentation invites people to
open. What it does NOT do is scrub anything else: if you pass a config from
outside the repository the artifact records its filename, and a filename can
carry information too.

**The risk layer is built, and nothing has ever asked it anything.** All three
parts `CLAUDE.md` demands now exist: its own module, its own config file
(`configs/risk_limits.yaml`), and its own tests (`tests/test_risk.py`, 486 of
them). What does not exist is a caller. `strategylab run` is a stub, the
backtester deliberately does not gate, and so every verdict this layer has ever
produced was produced by a test.
[docs/decisions/0010](docs/decisions/0010-what-the-risk-gate-refuses.md) argues the
two decisions the stub's contract left open. These consequences are worth stating
plainly,
and they are what a reader should take from this section rather than the fact that
the tests pass.

- **Nothing constructs a `PortfolioState`.** The gate needs equity, start-of-day
  equity, open exposure and realised profit and loss for the session, and the only
  thing that could supply them is a venue reconciliation that does not exist yet.
  The layer's arithmetic is tested against states a test builds; whether those
  states resemble what Trading 212 actually reports is untested and untestable
  until the adapter is written.
- **"The session" is not defined anywhere.** The daily-loss limit is expressed
  per session and compares against start-of-day equity, but no component decides
  when a day begins, resets the figure, or persists it across a restart. A worker
  restarted mid-session with a fresh start-of-day equity would have its daily loss
  silently forgiven. That is a bug waiting in the component that has not been
  written, and the limit is only as good as the state handed to it. `check` does
  not read `PortfolioState.as_of` at all — an audit confirmed the field has no
  reader — so the layer cannot tell a stale state from a current one, and nothing
  ties the "today" of a realised figure to any date.
- **The kill switch refuses an exit, by design, and that is a real exposure.**
  While it is set, a position already open cannot be closed through this gate: the
  switch stops the wire rather than flattening the account, so unwinding is a
  manual act at the venue. That is the intended meaning of a human throwing a
  switch, and it is stated here because the alternative reading — that a kill
  switch protects you from a position you already hold — is the one a reader will
  assume. A second cost of the same choice: while the switch is set, `risk_verdict`
  stops distinguishing "refused" from "nothing was asked for", because a `HOLD`
  records as a rejection too. A log reader separates them by the `action` field on
  the same record.
- **What the other limits exempt is a SIZE, not an exit.** The gate reads
  `Intent.size_pct`, so what passes a blown daily-loss limit is an intent asking
  for no new exposure. A well-formed exit is exactly that, and a malformed one
  carrying a size is refused like any other request. The distinction matters
  because `Intent`'s own docstring states the zero as a property of the type and
  nothing enforces it, so the two modules disagreed about whether it was a fact
  until an audit made both of them say the same thing.
- **Two of the three limits barely diverge yet.** They are set to the same
  number and, with one instrument, they are not the same test: the exposure limit
  subtracts what is already open first, so it is never looser than
  `max_position_pct` and is strictly tighter the moment anything is on the book.
  With an empty book neither binds a request of 95. It is a separate limit because
  it guards a portfolio total, and a second instrument would separate them
  properly; today it is untested in the only sense that matters, which is against
  a real portfolio.
- **The position cap bounds a REQUEST, not a position, and the shipped file
  hides it.** `max_position_pct` is compared against the size an intent asks for
  and never against what is already open; bounding the resulting position is the
  exposure ceiling's job, because it subtracts current exposure first. Measured:
  with `max_position_pct` at 95.0, an exposure ceiling of 100.0 and half the
  account already invested, a request for 95% is allowed at 50% and the position
  ends at 100% of equity. The committed file sets both to 95.0, so the exposure
  ceiling masks it entirely — which is exactly why the entry above about the two
  limits barely diverging did not catch it. Anyone loosening the exposure ceiling
  without loosening the position cap should know the cap will not hold the line.
- **A malformed entry asking for nothing is ALLOWED, not refused.** The
  size-keyed gate is documented in one direction — a malformed exit carrying a
  size is refused — and this is the mirror. An `ENTER_LONG` with `size_pct` of
  zero returns `allow` at a permitted size of zero, deliberately and with a test
  pinning it. So the first component to consume a verdict must not read `allow`
  as "there is an order to send"; it has to read the size beside it.
- **Three of the four limits can refuse outright, not just reduce.** The kill
  switch and the daily-loss limit always do. The exposure limit reduces a request
  to the headroom left and refuses when that headroom is zero or negative — which
  is easy to miss in a summary that contrasts "reduces" with "refuses", and was
  missing from this document until an audit said so.
- **Nothing enforces that the gate is asked with the committed limits.** `check`
  takes a `RiskLimits` as an argument and never calls `load_limits`, and the range
  check on the type admits 100.0 for all three fields — so a caller can hand the
  gate a maximal set of limits built in code and have a full-size entry approved
  on an account already down 80% for the day. File separation stops a search
  editing a limit; it does not stop a caller bypassing the file. This is the same
  shape as the window guard above: a chokepoint that is a convention backed by one
  obvious place to go, not a mechanism. The first caller ever written is the moment
  it starts to matter.
- **Nothing re-reads the limits file.** `load_limits` is a one-shot read, nothing
  watches the file, and no component reloads it. The kill switch is the one control
  here whose value is time-sensitive — set by a human, cleared by a human — and a
  human flipping it mid-session changes nothing until whatever holds the
  `RiskLimits` object reads the file again. There is no such component yet and no
  documented obligation on the one that comes.
- **For one input the gate raises instead of answering.** A
  `start_of_day_equity` that is not positive and finite produces a `ValueError`,
  and that check precedes the kill switch, so it fires even with the switch set.
  It is deliberate — the field is the denominator of the daily-loss limit, and
  nothing can tell whether such an account is down three percent or thirty — but
  it is a crash path out of the component whose other answer to an unreadable
  account is a refusal. It is also the field most likely to arrive wrong or absent
  from the first venue reconciliation ever written, which is the same component
  that would have to catch it. The entry above about a restarted worker covers the
  field being WRONG; this covers it being missing.

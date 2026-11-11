# Limitations

Written before any result existed, which is the only time this is easy to write
honestly. Everything here is a reason to distrust a number this project
produces. Nothing here is a footnote.

## What is implemented, and what has never run

Thirty-four modules have working bodies: `protocol.py`, `windows.py`,
`data/source.py`, `backtest/costs.py`, `backtest/metrics.py`,
`backtest/engine.py`, `core/config.py`, `core/strategy.py`, `search/grid.py`,
`search/plateau.py`, `search/graveyard.py`, `search/loop.py`,
`search/surrogate.py`, `search/noise.py`, `report/render.py`,
`report/surface.py`, `report/palette.py`, `report/verdict.py`,
`validate/walkforward.py`,
`validate/candidate.py`, `logging/decisions.py`, `risk/limits.py`,
`execution/t212.py`, `live/worker.py`, `api/reading.py`, `api/registry.py`, `api/history.py`, `api/jobs.py`,
`api/job_runner.py`, `api/rules.py`, `api/drafts.py`,
`api/overview.py`, `api/app.py`, `api/server.py`, and all eight commands in `cli.py`. No
`NotImplementedError` body remains. Two further modules are types rather than
behaviour: `core/types.py` holds the shared value types, and `execution/venue.py`
the venue interface and its data classes, exercised through the adapter's and the
worker's tests.

**What is left is not a component but a first run.** The research pipeline —
fetch, search, report, validate — has run end to end. The live half — the
Trading 212 adapter, the worker, `run` and `diff` — has been built and tested
against a mock transport and literals, and has never sent a request to the venue.
The section "The live half has never run" below lists what that leaves unproven,
and it is the section a reader of the README's "built" table should go to next.

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

**One search result is reported, and it is a failure.**
`strategylab search configs/ma_crossover.yaml` walks all ninety cells over
2005-2018, scores the surface by neighbourhood median, rejects edge winners and
drawdown breaches into the graveyard, and writes every cell to
`state/search-<stamp>.json`. Run once with `--trials 100`, its candidate - fast
10, slow 80, trend 150, Sharpe 0.640 - beat 25 of 100 surrogates and needed 95.
The README's Results section carries the figures, the command, the commit and
the hashes. Three limits on reading it: it is ONE run, with one seed and one
block length, so the ceiling's own sampling error is unmeasured; the run was
made at commit `e510a28`, and nothing in the artifact records that - the commit
is known only because the operator's assistant wrote it down; and the 7.5 hours
it took were on a machine doing other work, so that is not a timing.

**Validation now runs, and the repository's own held-back window is still
unread.** `strategylab validate state/search-<stamp>.json` walks a search's
candidate through 2019-2024 and writes a verdict. `access_log.walk_forward_touches`
reads **0**, and no validation artifact is committed, because `state/` is
gitignored and nothing has been validated against the repository's own
`protocol.yaml`. The command was exercised end to end against a COPY of the
protocol and a copy of the data — which is what `walk_forward`'s `protocol_path`
argument exists for — so the counter that copy carries is the only one that
moved. The first real run is the operator's to make, and it is permanent.

**A verdict checks the config it validates, and not the code.** `read_candidate`
compares the search artifact's schema version, protocol version, price-snapshot
content hash and strategy-config content hash against the ones in force, and
refuses on any mismatch before a touch is charged. The config hash is over what
the file MEANS — the parsed document with its keys sorted — so a comment, a
reordering or a different line ending on another machine is not an edit, and a
changed bound is. What nothing records is the CODE. A search run under one
commit of this repository and validated under another compares a winner chosen
by one engine against a verdict produced by a different one, and neither
artifact names the commit it ran under.

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
minutes — so a hundred trials is on the order of seven hours. That default is a real hazard
worth naming: the cheap path is the unbenchmarked one, and a reader who runs
`search` without reading its output will get a plausible Sharpe and no warning
they sought out.

**The ceiling gates validation, not selection.** `select_candidate` still never
receives it, so a candidate inside the noise distribution is still nominated,
written to the artifact as the winner and drawn on a report. What changed is
what happens next: the search records a `noise_gate` verdict beside it, writes
a failure to the graveyard, and `validate` refuses anything short of a pass.
`run` and `diff` waive the gate and say so, because paper trading tests the
simulator. The rule - 95 in every 100 scoreable surrogates, at least 100 of
them - was fixed by the assistant under a standing instruction and has not been
ratified by the operator; `docs/decisions/0012` says what had been seen by then.

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

**There is a benchmark, and it decides nothing.** A search and a validation
each record what holding the instrument did over exactly the bars their own
figures cover, so a result can now be read against simply owning the index,
which a long-only trend strategy on the S&P 500 must beat to be worth anything.
But the comparison is left to the reader. A `pass` from this validator still
means "cleared the noise gate and a pre-registered Sharpe bar", not "beat the
index", and a candidate can pass while losing to it. The benchmark pays no
costs and is always fully invested, which sets the bar slightly too high, and
on back-adjusted closes it is a total-return figure.

**The backtester emits the decision-log schema, and `diff` does not read it.**
`docs/architecture.md` states the schema is defined once, in
`logging/decisions.py`, because a backtest log and a live log have to be
diffable. The live worker, the replay and now the engine all emit it:
`backtest.engine.decision_records` renders a finished backtest as
`DecisionRecord`s, one per evaluated bar, and `tests/test_worker.py` asserts
that log and the replay's agree on every compared field. What has not changed
is which one `strategylab diff` uses - still the replay - and nothing writes
the engine's log to disk.

`run_backtest` also still returns its seven-column frame, and that is no longer
a second shape waiting to converge. The frame carries the position, the cash
and the equity beside each decision, which are account state and belong in no
decision record; the records carry the indicator values the frame leaves out.
They are two views of the same bars and the engine keeps both.

**What blocked the backtester adopting it used to be two things, then one, then
a decision, and the decision is made.**

The first blocker is GONE. `Intent` now carries `indicator_values` and
`thresholds` as numbers rather than only as rendered strings, so `core.evaluate`
supplies what a record needs. That was a change to the shared brain and it is
its own commit.

The second blocker went when `risk/limits.py` was built and tested, so that
`risk_verdict`, `risk_reason` and `approved_size_pct` COULD be filled with
something true. That left the question of what a BACKTEST should put there, and
both available answers cost something:

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

The second was chosen, in the replay's favour, when `diff` was built: the
replay does not run the gate, and `compare` excludes the three risk fields
rather than reporting them. The cost named above is paid knowingly and is
recorded again in the section on the live half. The backtester made the same
choice when it began emitting the schema: its records carry an allowance, a
reason saying the gate was not consulted, and an approved size equal to the
requested one.

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

2304 tests pass (`uv run pytest`). They cover the protocol loader, the window
accessors, the data layer, the cost model, the strategy-config loader, strategy
evaluation, the performance metrics, the backtest engine, grid expansion,
plateau scoring, the graveyard, the search loop, the noise ceiling, the reports,
walk-forward validation, the candidate reader, the decision log, the risk layer,
the Trading 212 adapter, the live worker, the command-line wiring, the app's
server and what it reports, and the commit-time credential scan — twenty-seven
areas, one per test file.
520 of the 2304 are the risk layer's,
and that is a statement about parameterisation rather than about importance.
Thirty-four more arrived with the daily-loss latch: six assertions at those four equities, four asserting an exception and six a returned value, one of them a rejection from `check`. The figures that follow describe the 486 before
that. 316 of those 486 are assertions repeated at four account equities, because at an
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

Two things are untested because no test may reach them: what Yahoo Finance
actually returns, and the vendor's side of the Trading 212 contract. This side
of the fetch is covered - `tests/test_data.py` replaces the vendor library with
a stand-in and checks what `YFinanceSource.fetch` asks for and what it makes of
the answer - but a stand-in answers the way its author expects. **The CLI is tested in part.** `tests/test_cli.py` runs all seven
commands in a throwaway workspace and covers what each writes, what `status`
names as the next step, and the refusals a person can reach: a missing config,
an unparseable one, an uncached instrument, an artifact that will not render,
a candidate the noise gate has not passed, a config edited since its search.
It found one defect on its first run - `search --trials` on a window too short
to resample ended in a stack trace rather than a message.

**What those tests stop short of is exactly what cannot be run for free.**
`fetch` is tested only where it finds a cache already there. `run` and `diff`
are tested only up to their refusals, because past those `run` talks to the
venue. And `validate` is tested only where it REFUSES: a run that proceeds
needs held-back bars the fixture does not have, so the error handling around
the two calls that can fail AFTER the touch is charged is still unexercised.
That is the branch in this project that can be re-entered only at the price of
a permanent read, and it is the one nothing covers.

The list of untested type definitions is now empty: `execution/venue.py`'s
`Order`, `Position`, `Balance`, `ReconcileReport` and `OrderType` are exercised
by `tests/test_t212.py` and `tests/test_worker.py`, as are `T212Error` and the
adapter's constants. What remains untested is what no test may reach: the
commands past their refusals, the vendor's real answers, and the vendor's side of
the Trading 212 contract. That list shrank as the components arrived:
`FoldResult`, `ValidationResult` and `Outcome` left it when
`validate/walkforward.py` was built, `DecisionRecord`'s sixteen fields left it
with `tests/test_decisions.py`, and `RiskLimits`, `PortfolioState`, `RiskVerdict`
and the `Verdict` enum have left it now — `tests/test_risk.py` exercises all
four. The default path's VALUE is pinned there too; `strategylab run` is the
one caller of `load_limits()` with no argument, and no test should be: the
default is
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
reproducible: the mutation was run from a scratch directory and no harness was
committed then, which is the same defect this file labels on the content-hash
measurement below and did not label here. A harness is committed now, under
`tests/mutation/`, and it quotes no count for this mutation either.

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

**The noise ceiling decides whether a candidate may be validated, and nothing
more.** `walk_forward` never receives it and the pass decision is unchanged: a
pooled Sharpe against a fixed bar. What stands in front of that decision is
`read_candidate`, which refuses a candidate whose search did not pass the noise
gate, before a touch is charged. So a candidate inside the noise distribution
can no longer reach a verdict, and one never benchmarked cannot either. The
verdict itself still says nothing about noise: it records the gate's counts and
does not adjust the pass bar for them. And the gate is one comparison against
one grid on one price series - no deflated Sharpe, no correction for how many
searches have been run.

**The benchmark is not a criterion.** `buy_and_hold` computes what holding the
index did over the candidate's own bars, and both artifacts carry it. Nothing
compares the two: making "beat the index" a pass condition would be a new
pre-registered threshold, and the protocol is written once. A long-only trend
strategy on the S&P 500 over 2005–2024 must beat simply holding the index to be
worth anything, and the reader is the one who has to check that it did.

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
only source, and by design no test calls it. The fetch path is tested against a
stand-in for the library and the cache path against a recorded fixture.

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
  confirms the count is 90.
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
  with one is decided, and the search loop does it:
  `search/plateau.py` requires the row to be kept with a `NaN` metric, so the
  grid stays complete and the failure is visible in `neighbour_count`, and
  the loop records the cell in the graveyard under `backtest_failed`.
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
the guard has two consumers.**
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

**The fetch path is tested against a stand-in, not against the vendor.**
`YFinanceSource.fetch` and the vendor-frame normalisation behind it are the one
part of the data layer that touches the network, and by policy no test may call
the vendor. `tests/test_data.py` puts an object in the library's place and
checks the request - adjusted daily bars, the end date made inclusive - and the
reshaping of frames in the two layouts the vendor uses. That covers this code.
It does not cover the vendor changing its layout, which is found only when a
human runs `strategylab fetch` and it fails.

**Only `run` fetches the execution instrument, and only recent bars.** `fetch`
downloads `instruments.research` and only that; `run` fetches the last few
months of `VUAG.L` on each invocation and merges them into `data/`, keeping any
bar already cached. So the execution-side history accumulates from the first
run forward and never reaches back: the years of `VUAG.L` history this project
would need to model real fills against are not obtainable from any command.

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

**The report meets four of the five interactions its own standard requires.**
`report-design` and `render`'s contract both ask for five things. Hovering a
cell for its metrics works; clicking one draws its equity curve against holding
the instrument; the surrogates' distribution sits on the same Sharpe axis as
every cell and the candidate; and a verdict's folds are drawn side by side.
Every figure carries its protocol version, snapshot hash and originating
command. What is missing, and what the four are worth:

- **No two-run comparison.** One artifact draws one page. Nothing loads two
  result sets and shows what changed between them.
- **The click-through is untested.** It is JavaScript in the page, and no test
  in this project runs a browser. It was checked by rendering a sample and
  reading a screenshot, which proves it worked once.
- **The curves are thinned.** A drawn curve is at most 400 points of a series
  that may hold thousands, so a one-day spike can fall between two of them. The
  figures beside it are from the full curve and are the ones to trust.
- **The verdict page has only ever drawn a fixture.** No validation has been
  run in this repository, so no real verdict exists for it to render.

A well-drawn page persuades more than a table does, which is the risk worth
naming: the surface is only evidence of anything when the banner above it says
the noise gate passed.

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
on the order of seven hours of work. The choice was to keep the ceiling exact and make
the cost explicit — the command prints the estimate before starting — rather
than shrink the surrogate grid, which would understate the ceiling, since the
ceiling is a property of how many combinations were tried.

**The ceiling's trials can run side by side, and that has been timed exactly
once.** `--workers N` hands the trials to N processes; the default is every core
but two. The surrogates are all drawn first, in order, from the one seed, so the
result does not depend on N - a test asserts the ceiling is identical on one,
two and three processes against a plain loop written out in the test, and the
committed hundred-trial run was repeated on fourteen with every trial value
unchanged. What is thin is the timing: 7h31m on one process and 49m on
fourteen, one run each, on a desktop doing other work both times. That is a
ratio of about nine on a sixteen-thread machine and nothing more general. Three
costs come with it. All the surrogates are held in memory at once instead of
one at a time. The real grid - the one pass that is not a trial - still runs on
one process. And no test starts more than three real processes, so the
fourteen-process case is covered by that one run and not by the suite.

The engine's cost was measured rather than assumed, and an earlier version of
this entry got the reason wrong. It said the engine "re-derives the moving
averages on every bar of every cell", implying quadratic behaviour. Timed across
400, 800, 1,600 and 3,200 bars the scaling is roughly LINEAR — 2.1x for 2x bars
— so the cost is per-call overhead, not the rolling recompute. There is no
cheap win hiding there: making the ceiling affordable means restructuring how
`evaluate` is called, which is an engine change and a separate piece of work.

**An unhandled exception prints your home directory.** Every command catches
the errors it expects and prints a message, but any it does not expect surfaces
as a traceback, and a traceback contains the absolute path of the file that
raised it, which on most machines includes your username. Nothing is written
anywhere and nothing leaves the machine, but if you paste such a traceback into
a bug report or an issue, that path goes with it. `status` prints no ABSOLUTE
paths for that reason — it names `configs/ma_crossover.yaml`, which is a
repository path and carries nothing about the machine it ran on.

`search` follows the same rule and extends it to a file rather than only to a
terminal. It catches its own errors and prints a message instead of a traceback,
prints only repository-relative paths, and reduces the config path to a relative
one — falling back to the bare filename — before recording it in the artifact,
because a search result is a file this project's documentation invites people to
open. What it does NOT do is scrub anything else: if you pass a config from
outside the repository the artifact records its filename, and a filename can
carry information too.

## The live half has never run

Everything below is true of code that passes its tests and has never been
executed against Trading 212. Each is a reason to distrust the first live
decision record until it has been read beside the venue's own account page.

- **No request has ever reached the venue from this code.** `tests/test_t212.py`
  drives every adapter method through an `httpx.MockTransport` that answers with
  canned JSON in the shapes the public v0 documentation describes. Whether the
  endpoints still answer in those shapes, whether the demo account behaves like
  the documentation, and whether `market_value` derived as quantity times current
  price matches the venue's own figure - none of that is established. The first
  real run establishes it, and nothing in the suite can.
- **The Trading 212 ticker for `VUAG.L` is unverified.** The adapter maps
  protocol symbols to venue tickers through `symbol_map`, which defaults to the
  identity, so an unmapped symbol is sent as written and refused by the venue
  rather than traded as something else. Confirming the mapping means calling the
  instruments endpoint, which no test may do. The first run will most likely be
  refused on this point, and that is the intended failure.
- **T212 v0 accepts no client reference.** `venue.py` says `client_ref` is what
  lets a timed-out order be resolved by query rather than resend. The endpoint
  has no such field, so the adapter carries the reference in its own error
  message only, and resolution after a timeout means matching ticker, quantity
  and time against the venue's open orders by hand. The adapter does not do it
  automatically, and it does not retry.
- **The session open is the first observation of the day.** A worker scheduled
  after the close records that close as the session open, so on its first run of
  a day the mark-to-market loss route reads zero by construction, and a loss
  taken before the worker ran is invisible to the daily-loss limit until the next
  day's open resets it. A worker restarted mid-session keeps the file's figure,
  which is the right behaviour; the gap is at the first run.
- **The daily-loss limit latches for the session, and the latch lives in a
  file.** The worker asks on every run whether the limit is reached, remembers a
  yes in `state/session.json`, and hands it to the gate, which then refuses any
  sized intent for the rest of that day whatever the account recovers to
  (`docs/decisions/0013`). A new day clears it, which is the intent: the limit
  is daily. What is not intended is that an unreadable or deleted session file
  clears it too, and nothing detects that. The command wiring that sets and
  saves the latch is past the point `strategylab run` talks to the venue, so no
  test reaches it.
- **`diff` compares seven fields and deliberately not the three risk fields.** The
  replay does not run the gate, so `risk_verdict`, `risk_reason` and
  `approved_size_pct` differ by construction and comparing them would report the
  harness as simulator drift. That is the decision the previous section described
  as unmade; it is now made in the replay's favour, and the cost is that a live
  refusal is invisible to `diff` unless its consequence shows up in a later bar's
  decision.
- **Exposure is in the instrument's currency, and the gate reads it as the
  account's.** `Position.market_value` is quantity times current price as the
  venue reports both; the risk layer divides it by equity in account currency.
  For `VUAG.L` on a GBP account the two coincide, unverified. Any other pairing
  would make the exposure percentage a ratio of two currencies.
- **The four files the live half writes** are `state/session.json`,
  `state/decisions-live.jsonl`, `state/decisions-replay.jsonl` and
  `state/positions.json`, all gitignored; the last belongs to the adapter.
- **The execution cache is written once per date and never revised.** `run`
  fetches from the day after the last cached bar (or the warm-up span if that is
  earlier) and merges with the cache winning on any date both hold, so a vendor
  revision is never silently adopted and `diff` replays what the deciding run
  saw. Two costs: a run made DURING a session caches the in-progress bar at its
  interim close, permanently, since no command re-downloads the execution
  instrument - run after the close, or delete `data/<execution>.parquet` by
  hand; and the sidecar's `fetched_at` is the time of the latest merge, not of
  every bar in it.
- **`run` and `diff` are tested only as far as their refusals.** Their
  decisions are in `live/worker.py`, which is tested. `tests/test_cli.py` covers
  the refusals: any environment string but `demo`, a missing key, an absent
  live log, each exiting non-zero without writing a log or printing the key.
  What is not covered is everything after them - the fetch-and-merge of the
  execution instrument's bars, the order of writes, and every error branch a
  venue can cause.
- **The mutation figures in older commit messages cannot be re-run from the
  repository; newer mutants can.** The harness that produced the early figures
  lived outside it, and `docs/decisions/0006` lists that as a past finding.
  `tests/mutation/` now holds a harness and a list of named mutants in fourteen
  groups: `uv run python tests/mutation/run.py` applies each one, runs the
  tests named for its group, restores the file, and exits non-zero if any
  mutant survives. It covers the work done since it was written, not the whole
  codebase, and it is run by hand - CI does not run it. A mutant list is also
  only as good as the mutants someone thought of.

**There is a server now, and it can start one kind of work.** `strategylab serve` exists
for a desktop app (`docs/decisions/0014`). It has ten
routes. One says it is alive. One reports the protocol version, the access
counter, the cached prices, the kill switch, whether a venue key is set, and
each strategy config with its newest search, its rules in words, where it
came from and whether its rules are confirmed. Three report a strategy's
history: its searches, one search in full, and its reads of the held-back
window. One reports the graveyard, and one the jobs it has started. Three change
something: one records that the operator has read a strategy's rules, one
starts a job and one asks a running job to stop. No job starts for a strategy
whose rules are not confirmed for its file as it is now (`docs/decisions/0018`). A job is
`strategylab search` on a config in `configs/`, with no trials or with the
hundred the noise gate needs, and nothing else (`docs/decisions/0017`). The
server cannot run `validate`, `run` or `fetch`, places no order, and reads no
held-back bar. What is worth knowing about it:

- **Its guard was reviewed, not attacked.** Every request must name this machine
  in its Host header, carry no foreign Origin, and present a token made for that
  launch. A review of the code tried the bypasses it could think of and the
  tests pin the ones it tried. That is a review by the same assistant that
  wrote the code, not a penetration test, and the guard matters more
  once a route can spend the held-back read.
- **The token is as private as the machine's own user account.** It lives in the
  server's environment and in the Electron main process. Another program running
  as the same user can read a process's environment on most systems. The server
  defends against web pages and other users, not against software already
  running as the operator.
- **"Missing" is only as good as the reasons written for it.** A figure that
  cannot be read is returned with a reason. The reasons are passed through from
  the loaders with the project's directory removed; nothing checks that a new
  loader's message is fit to show a person.
- **It reads the whole price cache to report on it.** The cache holds the
  held-back years beside the in-sample ones, and checking its hash means loading
  the file. No held-back bar is used for anything, and the access counter is not
  moved, which is the same thing `status` has always done.
- **A confirmation says a button was pressed, not that rules were compared.**
  The page sends the content hash of the file it showed the rules for, and the
  server refuses if the file has changed since; an edit to the config or to its
  source file afterwards undoes the confirmation, and the job records the hash
  it was started for and will not run a changed file. Nothing can say whether
  the operator read the rules against the video. Confirmations live in
  `state/`, so a fresh clone has none and the committed strategy must be
  confirmed once before the app will search it.
- **The import skill has never been run on a real video.** Its two checking
  commands were run by hand on a scratch copy holding a strategy written for
  the purpose. Everything else in it is instructions to an assistant, and an
  assistant reading a video can misread it; the draft page says so. A review
  found the first version of the skill would have pasted a name from the
  video into a shell command and could have overwritten
  `configs/risk_limits.yaml`. The skill now has the name checked against a
  pattern and passed as an argument, refuses existing files and the risk
  file's name, caps the grid, and says the video is data, not instructions.
  Those are rules for an assistant to follow, not code that enforces them.
- **Nothing caps a grid's size in code.** The loader accepts a search of any
  size; the app counts one without building it, so the first screen does not
  stall, but a search of a vast grid started from the app would run for as
  long as it takes. The import skill caps its own grids at 200 versions.
- **A job was watched surviving the window once, on one machine.** On a copy
  of the project, a search was started from the real window, the window was
  closed, and the search ran on, finished, and was on screen when the window
  was reopened. A luck test was started and asked to stop after three of a
  hundred trials; it ended as stopped, with no result written, 11 minutes 40
  seconds after it was asked, on a machine doing other work. No test repeats
  either: every job test starts no search, and the two that start a process
  start one that prints a line. Windows only; the POSIX branch of the detached
  start has never run.
- **"One job at a time" is a rule the server keeps, not a lock.** It is
  checked under a lock inside one server. The window allows one instance of
  itself, but a search started by hand at the command line is never seen, and
  two servers started by hand could each start a job. Two searches at once
  would both append to the graveyard, which is not safe for two writers.
- **Whether a job is running is read from a heartbeat file.** The process that
  carries the search touches a file every five seconds; sixty seconds of
  silence and the job is reported lost. If that process is killed and the
  search it started is not, the search runs on unseen, the app says no result
  was written, and one may still arrive. A `done` file is believed over a
  heartbeat, so anyone who can write to `state/jobs/` can free the slot early.
- **A stopped luck test stops slowly.** The stop is looked for when a trial
  finishes, and the trials already running on other processes finish first.
  A plain search cannot be stopped at all.
- **A job's files are written where its id says, and links are followed.**
  The stop request refuses to write through a link. The log, the heartbeat
  and the `done` file do not check, so a link planted in `state/jobs/` under a
  job's name would send fixed text - search output, an exit code - to wherever
  it points.
- **The job code was reviewed, not attacked.** A second assistant read the
  pending change and probed it on throwaway projects. It found that a job
  started on a copy of the project would import a Python file planted in that
  copy, that one long line in a log made every job route fail, and several
  ways past the one-at-a-time rule; each is fixed and pinned by a test, except
  the ones listed above as limits. It found no way for a request to start
  anything but a search, and none past the token.
- **It reads files it did not write, and was hardened by review, not by
  attack.** Everything under `state/` and `configs/` is treated as possibly
  damaged: a file that is not text, is nested absurdly deep, or holds a number
  or a string a response cannot carry is listed as not read, and a field of
  the wrong kind comes back as missing. A review found five ways such a file
  took a route down and one way a Windows error message carried the project's
  full path to the window; each is fixed and pinned by a test. The review was
  done by an assistant reading the code and probing it, and the list of
  damaged files is the ones it thought of.
- **One figure the app shows is worked out by the server, not recorded by a
  search.** A version's total return is its last recorded equity against its
  starting equity. The artifact's curves are rounded to two decimal places and
  always keep the final bar, so the figure is exact to far below what is
  shown; but it is a second place a number is made, and every other number
  the app shows is one a command wrote down.
- **The graveyard cannot say which strategy a rejection was for.** Its records
  hold a parameter combination and a reason. The app shows them for the
  project as a whole and says so; with two strategies that share parameter
  names the list will read as one.
- **A search in an older format is listed and not read.** The app reads schema
  version 8 and nothing else. An older artifact appears in the history by date
  with that reason, and none of its figures are shown.
- **The buy-and-hold comparison is recorded and withheld.** The search still
  writes it; the server leaves it out of every answer, because the operator
  chose not to see it (`docs/decisions/0014`). The app therefore shows a
  strategy's return with nothing beside it to say whether doing nothing would
  have done better.
- **A search for another strategy with the same name is not told apart.** The
  newest artifact naming the strategy is the one reported. It says whether the
  config has changed since, and that is the only link between the two.

**The desktop window starts searches and nothing else, and the part that
matters most is the part no test runs.** `desktop/` holds an Electron window
that starts the server and shows four screens of real state - Dashboard,
Improve, Tests and Backtest; two of its six sections say they are not built.
Three buttons start a search or a luck test, in the project the window is
serving: a real one, minutes or about an hour of this machine, behind a dialog
and a Start button but no typed confirmation. Deploying, the final exam and the
live check are disabled. Its Backtest screen shows the replay a search recorded
and can start another search, not a single backtest (`docs/decisions/0016`,
`0017`). Its page is tested in a simulated browser and its helper for finding
`uv` is tested directly. The Electron main process is not: starting the server,
attaching the token to requests, and refusing to navigate anywhere else were
checked by launching the app twice on Windows and inspecting it. That check
found the window showing real data, the page's script unable to see the token
or Node, and no server process left after the window closed - and it is two
launches on one machine, repeated by nobody else. The window now allows one
instance of itself and can be pointed at a copy of the project; both were used
in the check of a job described above, and neither has a test. CI does not build or test anything
under `desktop/`. The built screens were looked at in the real window at two
sizes, by the assistant that built them, and differ from the approved mock where
the mock shows something that does not exist: settings are named as the config
names them, the luck test is shown only for the version a search put forward,
and nothing is marked as deployed. The operator has not yet compared them with
the mock. The simulated-browser tests check words and not layout; one layout
fault got past them and was found by eye.

**The risk layer is built, and its one caller has never run.** All three parts
`CLAUDE.md` demands exist: its own module, its own config file
(`configs/risk_limits.yaml`), and its own tests (`tests/test_risk.py`, 520 of
them). `live/worker.py` is its caller and the backtester deliberately is not, so
no research figure here is gated - and since `strategylab run` has never been
invoked against the venue, every verdict this layer has produced was still
produced by a test.
[docs/decisions/0010](docs/decisions/0010-what-the-risk-gate-refuses.md) argues the
two decisions the stub's contract left open. These consequences are worth stating
plainly,
and they are what a reader should take from this section rather than the fact that
the tests pass.

- **The worker constructs a `PortfolioState` with one field always zero.**
  Equity and exposure come from the venue's balance and positions; the session
  open comes from `state/session.json`; and `realised_pnl_today` is ALWAYS 0.0,
  because the venue's cash endpoint reports a lifetime realised figure and the
  worker keeps no ledger to difference it against. So the daily-loss limit is
  reached only by the mark-to-market route in live use, and the realised route -
  half of what `tests/test_risk.py` tests - is never exercised by the worker.
  Whether the venue's balance and positions resemble what the tests build is
  unverified until the first real run.
- **"The session" is a file the worker keeps, and nothing checks it.** The
  daily-loss limit compares against start-of-day equity; `live/worker.py`
  defines the session as a calendar date, records the first equity observed on
  it in `state/session.json`, and keeps that figure across a restart on the same
  day. What nothing does is validate it: `check` does not read
  `PortfolioState.as_of` at all — an audit confirmed the field has no reader — so
  the layer cannot tell a stale state from a current one, and an unreadable
  session file silently starts a new session at current equity, which resets the
  denominator.
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
  `RiskLimits` object reads the file again. The worker is that component, and
  because it runs once per bar and exits, it reads the file fresh on every
  invocation - so the switch takes effect at the next scheduled run, not sooner.
- **For one input the gate raises instead of answering.** A
  `start_of_day_equity` that is not positive and finite produces a `ValueError`,
  and that check precedes the kill switch, so it fires even with the switch set.
  It is deliberate — the field is the denominator of the daily-loss limit, and
  nothing can tell whether such an account is down three percent or thirty — but
  it is a crash path out of the component whose other answer to an unreadable
  account is a refusal. It is also the field most likely to arrive wrong or absent
  from the venue's balance on the first run, and the worker that builds the
  state is the component that would have to catch it - it does not, so this is
  the one input on which `strategylab run` ends in a traceback rather than a
  message. The entry above about a restarted worker covers the
  field being WRONG; this covers it being missing.

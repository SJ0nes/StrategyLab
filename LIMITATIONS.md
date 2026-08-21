# Limitations

Written before any result existed, which is the only time this is easy to write
honestly. Everything here is a reason to distrust a number this project
produces. Nothing here is a footnote.

## Most of the system is not implemented

Eight modules have working bodies: `protocol.py`, `data/source.py`,
`backtest/costs.py`, `backtest/metrics.py`, `backtest/engine.py`,
`core/config.py`, `core/strategy.py`, and the `status` and `fetch` commands in
`cli.py`. Twenty-three `NotImplementedError` bodies remain, across 10 files. Two
further modules are mostly types rather than behaviour: `core/types.py` holds the
shared value types, and `execution/venue.py` holds the venue interface and its
data classes. `venue.py` has no tests; the one guard in `types.py` is covered by
`tests/test_strategy_config.py`.

**A strategy config can be read and evaluated, and nothing joins the two.**
`core/config.py` parses and validates `configs/ma_crossover.yaml` into a
`StrategyDef`, and the "fixed vocabulary" the declarative-config rule depends on
is now a closed set enforced at load time rather than an example in a docstring.
`core.evaluate` now implements that vocabulary, so the loader is no longer the
only thing that knows what `crosses_above` means. But no code path does both:
`cli.search` still raises before reading its argument, so nothing in this
repository has ever loaded a config and evaluated it. The vocabulary is also
small — two indicators (`close`, `sma`) and three operators (`above`,
`crosses_above`, `crosses_below`), which is what the one committed config uses
and no more. Nor are `$placeholder` periods
resolved: the loader checks that each has a parameter behind it and leaves the
substitution to a grid that does not exist. `strategylab status` points at
`uv run strategylab search configs/ma_crossover.yaml` once price data is cached;
in a clean checkout, where `data/` is empty because it is gitignored, it points at
`fetch` instead. Either way that config now parses and `search` is still a
`NotImplementedError`.

**There are no backtest results, no search results, and no validation outcomes.**
A backtester now exists — `run_backtest` steps `evaluate` bar by bar and charges
`apply_costs` on every fill, so the cost model below has finally been applied to
a trade. But it has only ever run on the synthetic twelve-bar series in its own
tests. It has never been pointed at `SPY`, because nothing calls it: `cli.search`
still raises before reading its argument, and there is no grid to hand it
parameters. There is no search and no validator.

**The engine's fill convention is a decision, not a detail.** A signal computed
from bar `t`'s close is filled at bar `t+1`'s OPEN. Filling at the same close
would transact at a price only known once that bar had closed, which is the
error that flatters most amateur backtests. The cost: `apply_costs` prices
against a mid and an open is a traded price, so using it as the mid is an
approximation — a smaller error than the one it avoids, and stated rather than
hidden. The last bar's signal is recorded and never filled.

338 tests pass (`uv run pytest`). They cover the protocol loader, the data
layer, the cost model, the strategy-config loader, strategy evaluation, and the
performance metrics. Nothing else has tests because nothing else has code.

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

**The lookahead risk has moved, not gone.** Because `evaluate` can only see the
window it is handed, the entire guarantee now rests on whoever chooses that
window — which will be the backtest engine, which does not exist. When it is
written, the question "does this read the future" becomes a question about its
slicing, and no test in this repository addresses that yet. That is the one to
write alongside the engine, and it is not the test that exists today.

**The determinism guarantee now has its test.** `run_backtest` consults no clock
and no random source, and `tests/test_engine.py` asserts that the same inputs
produce an identical equity curve, decision log, fill list and metrics.

**And the lookahead guarantee finally has a test that can fail.** The engine holds
the whole frame and chooses what to hand `evaluate`; that choice is the entire
guarantee. `test_corrupting_the_future_does_not_change_any_earlier_decision`
corrupts every bar after `k` and passes the frame in INTACT — nothing is sliced
away before the call — so an engine that peeked by a single bar produces
different earlier decisions and the test says so. Verified against a deliberately
broken engine: widening the slice by one bar turns seven tests red. That is the
first test in this project to carry the claim the whole design rests on.

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
search, so it informs which candidate a human chooses. `walk_forward` never
receives it. A candidate can pass validation while sitting inside the noise
distribution, and nothing in the code will stop that.

**Nothing computes a benchmark.** There is no buy-and-hold comparison anywhere.
A long-only trend strategy on the S&P 500 over 2005–2024 must beat simply
holding the index to be worth anything, and no module computes that number.

**Folds may contain few trades.** `min_trades_per_fold` is 10 and
`min_trades_pooled` is 30. A moving-average crossover on daily bars does not
trade often. `insufficient_data` is expected to be a common and honest outcome —
it means get more history or trade more often, and it is a different answer from
`fail`. If it becomes the *only* answer, that is a finding about whether this
design can test this strategy at all. It is never a reason to lower a threshold
after seeing the counts.

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
- **Three searched parameters, 90 grid cells.** The config's own comment notes
  that this much data supports three or four parameters at most.
- **Integer parameters only.** Lookbacks are whole bars, and the loader refuses a
  config where they are not — every bound of a searched range, not merely its
  start, because a fractional step puts some of the cells it generates off the
  whole numbers. (Not *all* of them: `step: 2.5` from `min: 5` gives 5, 7.5, 10,
  12.5, so every other cell is whole. An earlier version of this line claimed
  every cell after the first was fractional, which is true of `step: 0.3` and
  false of `step: 2.5`.)
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

**The held-back window is cached in the same file as the in-sample window.**
`fetch` downloads 2005-01-01 through 2024-12-31 in one request and writes one
parquet file. Nothing in the code partitions it, and nothing prevents a future
component from slicing past 2018-12-31. The split is this project's central
claim, and at present it is enforced by the absence of a consumer rather than by
any guard.

**Nothing calls `increment_walk_forward_touches`.** The counter is implemented
and tested, and no production code path invokes it, because the only thing that
should — walk-forward validation — does not exist. The counter currently reads
zero because nothing has ever read the held-back window, not because anything is
enforcing that.

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

**A stub command prints your home directory.** The five unbuilt CLI commands —
`search`, `validate`, `report`, `run`, `diff` — raise `NotImplementedError`,
and the resulting traceback contains the absolute path of the file that raised
it, which on most machines includes your username. Nothing is written anywhere
and nothing leaves the machine, but if you paste such a traceback into a bug
report or an issue, that path goes with it. `status` prints no ABSOLUTE paths
for that reason — it names `configs/ma_crossover.yaml`, which is a repository
path and carries nothing about the machine it ran on.

**The risk layer is not implemented.** `risk/limits.py` exists and carries its
contracts, but both functions are stubs, there is no `configs/risk_limits.yaml`
for it to read, and there are no tests. `CLAUDE.md` describes it as untouchable
with its own file and its own tests: it has the file, and neither of the other
two.

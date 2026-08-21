# Limitations

Written before any result existed, which is the only time this is easy to write
honestly. Everything here is a reason to distrust a number this project
produces. Nothing here is a footnote.

## Most of the system is not implemented

Five modules have working bodies: `protocol.py`, `data/source.py`,
`backtest/costs.py`, `core/config.py`, and the `status` and `fetch` commands in
`cli.py`. Twenty-seven `NotImplementedError` bodies remain, across 13 files. Two
further modules are mostly types rather than behaviour: `core/types.py` holds the
shared value types, and `execution/venue.py` holds the venue interface and its
data classes. `venue.py` has no tests; the one guard in `types.py` is covered by
`tests/test_strategy_config.py`.

**A strategy config can be read, and nothing can act on what it says.**
`core/config.py` parses and validates `configs/ma_crossover.yaml` into a
`StrategyDef`, and the "fixed vocabulary" the declarative-config rule depends on
is now a closed set enforced at load time rather than an example in a docstring.
Two things that vocabulary is not: it is not implemented anywhere, because
`core.evaluate` is still a stub, so the loader is the only thing in the system
that knows what `crosses_above` means; and it is small — two indicators (`close`,
`sma`) and three operators (`above`, `crosses_above`, `crosses_below`), which is
what the one committed config uses and no more. Nor are `$placeholder` periods
resolved: the loader checks that each has a parameter behind it and leaves the
substitution to a grid that does not exist. `strategylab status` prints
`Next: uv run strategylab search configs/ma_crossover.yaml`; that file now parses,
and `search` is still a `NotImplementedError`.

**There are no backtest results, no search results, and no validation outcomes**,
because there is no backtester, no search, and no validator. No cost has ever
been applied to a trade: `apply_costs` works and is heavily tested, but
`run_backtest` is a stub, so the entire cost section below describes a model
that has never run on anything.

217 tests pass (`uv run pytest`). They cover the protocol loader, the data
layer, the cost model, and the strategy-config loader. Nothing else has tests
because nothing else has code.

Two guarantees this project rests on are **currently unproven**: that
`core.evaluate` cannot read past the current bar, and that the backtest engine
is deterministic. Both are stated as design rules in `CLAUDE.md`; neither has a
test, because neither has an implementation.

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

**Two real cost components are configured to zero.** `commission_min_flat` and
`fx_conversion_pct` both default to `0.0` because `protocol.yaml` does not set
them. The first is the flat minimum commission that dominates small positions;
the second is the currency conversion charge — for a strategy whose research
currency is USD and whose execution currency is GBP. The cost model's own
documentation names both as the things that kill otherwise-viable strategies,
and both are currently switched off.

**Missing entirely:** UK stamp duty on purchases, taxes of any kind, interest
earned on uninvested cash, and borrow costs.

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
  config where they are not — across a searched range's whole span, not merely at
  its start, since a fractional step walks off the whole numbers after one cell.
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

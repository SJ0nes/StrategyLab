# Limitations

Written before any result existed, which is the only time this is easy to write
honestly. Everything here is a reason to distrust a number this project
produces. Nothing here is a footnote.

## Nothing is implemented yet

As of this commit, `protocol.py` is the only module with a working body. Every
other function in `src/` raises `NotImplementedError`. There are no backtest
results, no search results, and no validation outcomes, because there is no
backtester, no search, and no validator. The 23 passing tests cover the protocol
loader and nothing else.

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

**One instrument, few distinct regimes.** Hundreds of parameter combinations are
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
- **Integer parameters only.** Lookbacks are whole bars.
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

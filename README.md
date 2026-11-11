# StrategyLab

A research pipeline for testing whether a trading strategy has any real edge.
It searches parameters on years of price history, tests the survivor on data the
search was never allowed to see, and compares the result against what the same
search achieves on noise. A paper-trading worker then measures how far live
behaviour drifts from what the backtest predicted.

The project is built around one assumption: **given enough parameter
combinations, something will always look profitable.** Most of the machinery here
exists to tell that apart from an actual edge.

*CI runs test, lint, and type-check on every push and every pull request
([.github/workflows/ci.yml](.github/workflows/ci.yml)). No badge is embedded: a
badge URL hardcodes the repository owner's account name, and this README
deliberately names no account and no absolute path. That means this file makes no
claim about whether the pipeline is currently green — check the Actions tab,
which is the only place that answer is ever current.*

## Current state

**A parameter search runs end to end and can now be benchmarked against noise.**
`strategylab search` walks all ninety cells of the committed config over
2005–2018, scores the surface by neighbourhood median, and records every cell.
With `--trials N` it also re-runs the whole grid against block-resampled
surrogate series and reports where the candidate sits in that distribution —
the comparison without which a winning Sharpe is a selection artefact rather
than a finding.

**The part that tests a candidate on unseen data now exists too.**
`strategylab validate state/search-<stamp>.json` walks that search's winner
through 2019–2024 in folds, pools the result, and returns pass, fail, or
`insufficient_data` against thresholds fixed before anything ran. Every run
increments `access_log.walk_forward_touches`, permanently, because the split
degrades with use.

**The repository's own counter still reads 0, and no strategy has been
validated here.** The command was exercised end to end against a *copy* of
`protocol.yaml`, `configs/` and `data/` in a scratch directory, so nothing in
this repository has read the held-back window. There is no CLI flag for that —
`validate` takes no options — it is done by running the command somewhere else;
the `protocol_path` argument that makes it possible belongs to the library
function `validate.walk_forward`, which is what the tests use. The first real
run is the operator's to make. **One search result is reported, under Results:
its candidate failed the noise gate.**

**The veto gate between a strategy and a venue now exists, and the live worker
is its one caller.** `risk/limits.py` reads its own config file — `configs/risk_limits.yaml`,
which is new, and deliberately neither `protocol.yaml` nor anything a search
touches — and answers one question per intent: may this proceed, and at what
size. A position or exposure limit may reduce a size rather than refuse; the
kill switch and the daily-loss limit always refuse outright, and so does the
exposure limit when there is no headroom left to reduce into. Two decisions in it are worth stating rather
than leaving to be discovered. **The kill switch refuses an exit too** — a
switch a human throws means stop sending orders, not flatten the account, so
unwinding an open position while it is set is a manual act at the venue.
**The daily-loss limit does not**, because a limit that blocked a
position-closing intent would forbid the one action that stops the loss it is
measuring. That second claim is about SIZE, not about exits: the gate reads the
size an intent asks for, so a well-formed exit — which asks for none — passes,
while a malformed one carrying a size is refused like any other request.
`strategylab run` passes every intent through it; a backtest deliberately does
not, so no research figure in this repository is gated. Both decisions, and why a
limit of zero is refused rather than honoured, are argued in
[docs/decisions/0010](docs/decisions/0010-what-the-risk-gate-refuses.md).

**The live half is built, and has never been run.** `strategylab run` runs the
paper-trading worker for one bar and exits: it reconciles against Trading 212's
demo venue, fetches the execution instrument's latest bars and appends them to
the cache, evaluates the bar through the same `core.evaluate` the backtester
uses, gates the intent, writes a decision record, and sends at most one market
order. `strategylab diff` replays every bar in that log from the cached bars and
reports each field on which the two disagree — which tests the simulator, not
the strategy. The venue adapter is tested through a mock transport, so what is
proven is this side of the contract and not the vendor's: no request has ever
reached Trading 212 from this code, the T212 ticker for the execution instrument
is unverified, and the first real run is the operator's to make with
`T212_ENVIRONMENT=demo` and a demo key. The command refuses any other
environment string; nothing can tell a demo key from a live one, so the string
is what stands between paper and real money.

| Built | Not built |
|---|---|
| Protocol loader and validator | A benchmark in any DECISION — buy-and-hold is reported beside a search and a verdict, and rejects nothing |
| Price data layer — validation, provenance, parquet cache | A comparison of two searches side by side on one page |
| Cost model — hand-verified arithmetic | A candidate that clears the noise gate — the committed grid has been run through a hundred-trial ceiling once, and its candidate beat 25 surrogates of the 95 it needed |
| Strategy-config loader — closed schema, closed vocabulary, and a content hash a search records and a later reader checks | A real verdict to draw — the fold page exists and has only ever rendered a fixture |
| The noise ceiling — block-resampled surrogates, `--trials`, and a gate: `validate` refuses a candidate that has not cleared it | `diff` reading the backtester's own log — the engine now emits the schema and is tested to agree with the replay, which `diff` still uses |
| Strategy evaluation (`core.evaluate`), with the lookahead test | A test that opens the report in a browser — the click-through script was checked by hand, in a screenshot |
| Backtest engine and metrics, with the determinism test, and a buy-and-hold benchmark over the candidate's own bars | A record of the code version — a search and its validation can run under different commits |
| Grid expansion (`search.expand_grid`) — the 90 cells of the committed config | A latch that survives its own file — the daily-loss limit now holds for the session, and deleting `state/session.json` clears it |
| Plateau scoring, the edge check, the degeneracy check | A verified Trading 212 ticker for `VUAG.L`, or any request that has reached the venue |
| The graveyard — append-only JSON Lines, closed reason vocabulary | A daily check that has reached Trading 212 — `desktop/` can deploy a strategy to the practice account and run its daily check, and that path has never run against the venue: no key has been set, and every check of it used a copy of the project with trading stopped. No running several strategies at once, and no final exam ever taken from the app |
| The search loop — grid → engine → scoring → graveyard, with the window test and an input gate | |
| The surface report — faceted heatmap, every Sharpe and the surrogate band on one axis, click a cell for its equity curve against buy-and-hold; self-contained HTML, no build step | |
| Walk-forward validation — folds, three outcomes, the pass margin, the access counter, and a page drawing the folds side by side | |
| The decision-log schema — append-only JSON Lines, exact round trip *(emitted by the worker, the replay and now the backtester; nothing writes the backtester's to disk)* | |
| The risk layer — its own config file, the kill switch, three limits *(called by the worker; not by the backtester)* | |
| The Trading 212 adapter — demo or live by explicit choice, no retry, key masked *(tested against a mock transport only)* | |
| The live worker's decisions and the replay `diff` compares them against | |
| All eight commands — `status`, `fetch`, `search`, `report`, `validate`, `run`, `diff`, `serve` *(tested as far as their refusals and what they write; never past the network or a held-back read)* | |
| A local server for a desktop app — `strategylab serve`: this machine only, a token on every request, seven routes that report the project's state, each strategy's searches (one of them in full, cell by cell), its reads of the held-back window, the graveyard and the jobs it has started; one that records the operator's confirmation of a strategy's rules, two that start or stop a job - a search, a search with a hundred-trial noise ceiling, or the deployed strategy's daily check, as a process that outlives the window - and four that deploy, pause, resume, undeploy and stop all trading from the app; a scheduler starts the daily check once a weekday after the close while the app is open *(it runs `run` for the one deployed strategy, on the practice account only, and `validate` - the final exam, one permanent look - only after a passed luck test and the phrase SPEND ONE LOOK typed; never `fetch`)* | |
| Strategies from videos — `.claude/skills/import-strategy/` turns a `/watch` of a video into `configs/<name>.yaml` and `configs/<name>.source.json`, using the existing vocabulary only and listing what it left out; the app shows it as a draft with its rules in words and a path to deployment *(the skill has never been run on a real video)* | |

**2463 tests pass** — verify with `uv run pytest`. They cover the protocol
loader, the window accessors, the data layer, the cost model, the
strategy-config loader, strategy evaluation, the performance metrics, the
backtest engine, grid expansion, plateau scoring, the graveyard, the search
loop, the noise ceiling, the reports, walk-forward validation, the candidate
reader, the decision log, the risk layer, the Trading 212 adapter, the live
worker, the command-line wiring, the app's server, what it reports about the project and its history, the jobs it starts, and the
commit-time credential scan in `.claude/hooks/` — thirty-one areas, one per test file. 42 of the 125 plant a `core.fsmonitor` command in a scratch repository and skip themselves where the installed git does not run one.
`uv run python tests/mutation/run.py` breaks the code in named ways, one at a
time, and fails if the tests stay green; it covers sixteen areas of recent work,
not the whole codebase, and CI does not run it.
520 of them are the risk layer's, which is a statement about parameterisation
rather than about size. Thirty-four more arrived with the daily-loss latch: six assertions at those four equities, four asserting an exception and six a returned value, one of them a rejection from `check`. Of the 486 before
that, 316 are the same assertions repeated at four
account equities, because at an equity of 100 a cash exposure and its percentage
of equity are the same number and an implementation that never divided would pass.
`test_the_committed_daily_loss_ceiling_binds` deliberately runs at one account
size instead, because 2.8% and 3.2% are round cash amounts there and it brackets
the shipped 3% ceiling rather than sitting on it. The other 170 items exercise the
loader, the type's own range check and the gate itself, in no clean partition:
several tests use two of the three. What does divide cleanly is 109 asserting an
exception and 61 a returned value, 24 of those a verdict from `check`, 23 of which
carry a rejection. Three successive audits corrected that one sentence — "106
refusals and 58 acceptances" had the wrong labels on both halves, and the
three-way split that replaced it could not be made to add up without filing a test
under a heading that did not describe it. Three test functions, twelve of those items, came out of an audit showing
the suite could not tell a correct denominator from a wrong one — a realised loss
or a mark-down measured against what is left of an account rather than against
what it opened at, and an exposure measured the other way round. Two of the three
closed real holes; the third states a rule another test already covered, and says
so rather than claiming credit for it.
The seven CLI commands are tested in a throwaway workspace, up to what each
writes and what each refuses, and no further: `fetch` only where it finds a cache
already there, `run` and `diff` only as far as their refusals, and `validate`
only where it declines to read the held-back window. The vendor fetch path is tested against a stand-in for the vendor library, which shows what this code asks for and what it does with the answer, and nothing about whether the vendor still answers that way,
because by policy no test may call the vendor — and the vendor's side of the
Trading 212 contract is untested for the same reason. No `raise NotImplementedError` remains under
`src/` (`grep -r "raise NotImplementedError" src/ --include='*.py' | wc -l`
prints 0).

**The split now has a guard, not just an absence, and the guard has two
consumers.** `fetch` caches 2005–2024 in one file and hands all of it to
whoever asks; `windows.py` is the one place either component decides which bars
it may see. Reading the in-sample window is free; reading the held-back window
cannot be done without the counter moving, and there is deliberately no dry run.
`tests/test_search_loop.py` corrupts every bar after the in-sample window's end,
passes the frame in intact, and asserts both the bar count and that no cell's
result moves. `tests/test_walkforward.py` does the same on both sides of the
validator's entitlement — the future beyond the held-back window, and the
in-sample past beyond its warm-up.

Both guarantees this project rests on now have tests.
**Strategy evaluation cannot read past the current bar** — the lookahead
test exists and is green, and `LIMITATIONS.md` states plainly that the half
named in the project's own rules cannot fail, and which test carries the weight
instead.
**The backtest engine is deterministic** now has one too, and the engine has
the lookahead test that can actually fail — it hands the engine a corrupted
future intact rather than slicing it away. See [LIMITATIONS.md](LIMITATIONS.md).

## Architecture

```
protocol.yaml ──▶ protocol.py ──▶ data/ ──▶ core/ ──▶ backtest/ ──▶ search/
   the rules       the only        bars      shared     costs and     grid,
   written once    reader                    by both    engine        plateau,
                                             sim and                  noise
                                             live          │             │
                                                │          └──────┬──────┘
                                                ▼                 ▼
                                          execution/ ◀─ risk/  validate/
                                          venue, T212   veto   walk-forward
                                                │                  │
                                                └────▶ logging/ ◀───┘
                                                          │
                                                          ▼
                                                       report/
```

Full version, with every module marked and every test counted, in
[docs/architecture.md](docs/architecture.md).

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Both commands below
were last run with an empty `data/` cache:
**2463 passed**, and `status` reported `data  not cached` and pointed at `fetch`.
The clone and the first `uv run` need the network to fetch dependencies; **the
test suite itself makes no network call**, which is the property that matters.
Three tests start the app's server as a child process and talk to it on
`127.0.0.1`; that is this machine and goes no further.

```bash
git clone <this-repo> && cd StrategyLab
uv run pytest                   # 2463 tests, none touching the network
uv run strategylab status       # what exists, and what to run next
```

`status` reads the protocol and reports where the project is. It never changes
anything, so it is safe to run at any time.

Six further commands do the work. `fetch` and `run` are **the two commands that
touch the network**, so unlike the others they can fail for reasons outside
this repository. `search` needs `fetch` to have run; `report` and `validate`
need `search`; `run` needs a search artifact and a demo key in the environment;
`diff` needs `run` to have written `state/decisions-live.jsonl`:

```bash
uv run strategylab fetch                              # caches SPY, with provenance
uv run strategylab search configs/ma_crossover.yaml   # ~4 min; writes state/search-*.json
uv run strategylab search configs/ma_crossover.yaml --trials 100   # + noise ceiling; hours
uv run strategylab report                             # renders those to reports/*.html
uv run strategylab validate state/search-<stamp>.json # SPENDS A HELD-BACK READ
uv run strategylab run state/search-<stamp>.json      # ONE bar against the DEMO venue; network
uv run strategylab diff state/search-<stamp>.json     # live log vs replay; writes state/decisions-replay.jsonl
```

`search` reads only 2005–2018, whatever the cache holds. `report` recomputes
nothing — it re-renders what is already on disk, so changing a template costs no
protocol access.

**`validate` is the one command that is not free to re-run.** It reads
2019–2024, increments `access_log.walk_forward_touches` in `protocol.yaml`
before it is handed a single bar, and writes its verdict to
`state/validation-<stamp>.json` so the answer never has to be re-derived. There
is no dry run and no confirmation prompt: typing the command is the decision.
Development and tests point the counter at a temporary copy of the protocol,
which is why this repository's counter still reads 0.

Run every command from the repository root — paths in the protocol are relative
to the working directory.

## Methodology

The full argument is in [docs/methodology.md](docs/methodology.md). In short:

**The split.** Parameter search may look at 2005–2018 and nothing else.
2019–2024 is held back and used only to test a candidate already chosen. The
loader refuses a protocol whose windows overlap. Every read of the held-back
window increments a counter, because the split degrades with use and that
degradation should be visible rather than forgotten. *`validate` is the caller;
`windows.walk_forward_bars` is the only way in, and it charges before it
returns. This repository's counter reads 0 because nobody has spent it here.*

**Plateaus over peaks.** *Built, tested, and now applied to a decision rather
than merely computed.* The best single cell in a parameter grid is usually the
luckiest one. Candidates are scored by the median of their parameter
neighbourhood; winners sitting on a grid boundary are rejected as walls rather
than optima — measured against the highest value the grid actually reaches,
which is not always the declared maximum; and flatness caused by parameters
doing nothing is flagged rather than reported as robustness, by comparing trade
sets rather than trade counts. What goes in that trade comparison is
[docs/decisions/0007](docs/decisions/0007-what-goes-in-a-trade-fingerprint.md).
`search` ranks by the neighbourhood median, refuses an edge winner and moves to
the next-best cell, and writes every refusal to `state/graveyard.jsonl`.

**The noise ceiling.** *Built, and off by default.* The expected maximum across
90 draws from noise is well above zero, so the best of 90 parameter combinations
(6 x 5 x 3, from the `search` block of `configs/ma_crossover.yaml`) will post a
positive Sharpe on data with no *exploitable* structure in it. `search --trials
N` reruns the entire grid against `N` block-resampled surrogate series and
reports where the candidate falls in that distribution. Blocks are as long as
the grid's longest lookback, so a trend can exist inside one — a shorter block
destroys the structure under test and sets the bar too low, which is the same
failure as shuffling arriving more quietly
([docs/decisions/0008](docs/decisions/0008-surrogate-block-length.md)).

It is **off by default** because one trial costs a full grid: the committed
search takes about four minutes, so a hundred trials is hours of work. `--workers`
spreads the trials over several processes without changing the result; see
Results for the one pair of timings there is. A search
run without it says on every line of output that nothing it printed is evidence
of an edge. That default diverges from a rule `CLAUDE.md` calls non-negotiable,
and the divergence is argued out in
[docs/decisions/0008](docs/decisions/0008-surrogate-block-length.md) rather than
left to be noticed.

**Two honest caveats.** The surrogates are not noise — each block is a verbatim
stretch of real history, so the comparison is against a *reordering of this
market's own regimes*, which is a harder and narrower test than the rules
describe. And **the ceiling now gates validation, by a rule the assistant fixed
rather than the operator**: a candidate must beat the grid's best cell on at
least 95 in every 100 scoreable surrogates, with at least 100 of them, or
`validate` refuses it before the held-back window is read. A search run without
`--trials`, or with too few, has no verdict and cannot be validated either. The
candidate is still recorded as the search's winner, because the paper-trading
worker tests the simulator and has every use for it.
[docs/decisions/0012](docs/decisions/0012-the-noise-ceiling-becomes-a-gate.md)
records the rule, what had been seen when it was fixed, and that it has not been
ratified.

**Three outcomes, not two.** *Built and tested.* Validation returns pass, fail,
or `insufficient_data`. The third is not a soft failure — it means the strategy
was never really tested. Conflating it with failure throws away a candidate that
was never refuted, and the two have separate pre-registered floors:
`min_trades_per_fold` decides whether one fold reports a verdict,
`min_trades_pooled` whether the validation as a whole does. The pass bar is
`min_sharpe + pass_margin`, additive, so a marginal pass is a fail; a pooled
Sharpe that is not a finite number is `insufficient_data` rather than a silent
pass. The held-back window is run as **one continuous backtest sliced into
folds**, warmed up on in-sample bars so the curve starts on the window's first
day — argued out in
[docs/decisions/0009](docs/decisions/0009-how-the-held-back-window-is-run.md).

**Expect `insufficient_data` from the committed config, and do not fix it by
lowering the floor.** A 150-bar trend filter takes one or two round trips a
year; six years of held-back data cannot reach thirty trades. The run also ends
holding a position that pays no exit cost and is missing from the trade count
while still moving the pooled Sharpe. [LIMITATIONS.md](LIMITATIONS.md) records
both, and what the honest routes out are.

## Results

**One search has been run through a hundred-trial noise ceiling. Its candidate
failed the gate, so there is no validated strategy here and the held-back window
is still unread.**

```bash
uv run strategylab fetch                                          # caches SPY 2005-2024
uv run strategylab search configs/ma_crossover.yaml --trials 100  # 101 grids
uv run strategylab report                                         # draws it
```

Run from a clean clone at commit `e510a28`, started 2026-11-10 18:40 UTC and
finished 2026-11-11 02:11 UTC on a desktop that was doing other work at the same
time. Protocol version 1. Data snapshot `2b9347c374c0`, config hash
`912a24e200f7` (both are SHA-256 hashes, shortened here; the artifact carries
them whole). In-sample window 2005-01-01 to 2018-12-31, 3,523 bars. Ninety
cells, none failed.

| | |
|---|---|
| Candidate, by plateau score | fast 10, slow 80, trend 150 |
| Its Sharpe, after costs | 0.640 |
| Its largest drawdown | 19.40% |
| Its trades over the window | 17 |
| Plateau score (neighbourhood median) | 0.586 over 27 cells |
| Cells rejected as edge winners | 4 |
| Noise ceiling, 100 surrogates: median / 90th / 95th / maximum | 0.769 / 1.039 / 1.114 / 1.230 |
| Surrogates the candidate beat | **25 of 100. It needed 95.** |
| Noise gate | **Failed** |
| Buy-and-hold over the candidate's own bars, no costs | Sharpe 0.483, drawdown 55.19% |

**What that says.** A surrogate is the real series resampled in 200-bar blocks:
each block is a verbatim stretch of history, and the sequence across blocks is
not the one that happened. The best cell the same grid finds on a surrogate
scores above 0.640 on 75 of the 100. A Sharpe of 0.640 from the best of ninety
combinations is therefore inside what this search finds on resampled history.
The candidate is recorded in the graveyard as `below_noise_ceiling`, and
`validate` refuses it.

**What it does not say.** It does not say the strategy loses money: over this
window its Sharpe was positive and its largest drawdown was smaller than the
instrument's. It says the search cannot tell that result from what resampling
alone produces. The ceiling is
itself an estimate, from one price series, one grid, one seed (0) and one block
length; `docs/decisions/0008` records why the surrogates are resampled history
rather than noise, and what that costs.

**What happens next is nothing, deliberately.** The grid and the pass bar were
written before this result existed. Widening the grid, changing the block
length, or re-running with another seed until the candidate clears the gate
would be the selection the gate exists to prevent. `access_log.walk_forward_touches`
still reads 0.

**It was run twice, and the second run is the same hundred numbers.** The first
used one process and took 7 hours 31 minutes. After the ceiling learned to spread
its trials over processes, the same command was run again on fourteen and took
49 minutes; every one of the hundred trial values, the candidate, the gate and
all ninety cells came out identical to the first run's. Both timings were taken
on a desktop that was doing other work, so they say "hours became under an hour
here" and are not a benchmark.

`state/` and `reports/` are gitignored, so the artifact and its page are local
to whoever runs the commands. The figures above were copied from the command's
output and checked against the artifact.

## Limitations

[LIMITATIONS.md](LIMITATIONS.md) was written before any result existed, which is
the only time such a document gets written honestly. The most important entries:
the strategy is searched on `SPY` but would be traded as `VUAG.L`, which did not
exist for most of the in-sample window; the cost model's minimum-commission and
FX-conversion terms are both configured to zero; the noise ceiling gates
which candidate may be validated but is not part of the pass arithmetic; the buy-and-hold benchmark is reported and decides nothing; and the committed
strategy trades too rarely for the pre-registered trade floors to be reachable,
which is a finding about the validation design rather than a reason to move a
threshold.

## How this was built

This repository was built in supervised sessions, pair-working with an AI
assistant. That is visible in `git log` if you look at the timestamps, and
`git log --format=%ad --date=iso` will show you exactly when the work happened —
including that it is more than one sitting, with a long gap in the middle. It is
stated here rather than left to be inferred. No figure is quoted because it grows
with every commit: twice now a written-down span has been stale or wrong by the
time it was read, most recently a claim of "one sitting of a few hours" that the
command cited beside it disproved. The command is the only honest form.

What that does and does not mean:

- **Every design decision was ratified by a human at three explicit gates**: the
  pre-registration in `protocol.yaml`, a review of every module contract, and a
  check of the first fetched prices against known values. The contract review
  came before every implementation except `protocol.py`, which was already
  written by then — so that one module was reviewed as code rather than as a
  contract. Be precise about what that means: the session prompt in
  `.claude/setup.md` is tracked in this repository, and for the instrument, the
  windows and the cost rates its suggested defaults are verbatim the values that
  ended up in `protocol.yaml`. Those were *chosen from* proposed defaults rather
  than originated. Not all of it was: `min_trades_per_fold` is 10 where the
  prompt proposed 30, and `min_sharpe` appears in no default at all. The amendments at the second gate were not — those came from
  reviewing the contracts, and they are recorded in `docs/decisions/0003`,
  including the one that is not a uniform tightening.
- **Every change-set was reviewed by read-only audit agents** — one checking
  implementations against their docstring contracts, one recomputing every
  hardcoded number in the tests from first principles without reading the
  implementation, one comparing the repository against the session plan, and one
  checking documentation against the code. They found real defects: a NaN volume
  that passed validation entirely, an access counter that could not read a CRLF
  file, a cost model that accepted infinity, and several documents that claimed
  tests which did not exist. Those findings and their fixes are in the history.
- **The numbers here were measured, not estimated.** Where a figure appears in
  this README, the command that produces it appears beside it. One measurement
  in `LIMITATIONS.md` — the two fetches ninety minutes apart that returned 4,161
  differing rows out of 5,033 — came from a paired comparison run during the
  audit and is not reproducible from a single command. `LIMITATIONS.md` says so at
  the point it appears. A second unreproducible figure was added later and then
  retracted rather than labelled, because a figure nobody can regenerate earns
  its place only when the measurement itself is the point.

The reason to say this plainly is the same reason `LIMITATIONS.md` exists: a
reviewer who works it out for themselves reads it as concealment, and a reviewer
told upfront can weigh it.

## Licence

MIT — see [LICENSE](LICENSE).

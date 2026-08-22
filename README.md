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

**What is still missing is the part that tests a candidate on unseen data.**
`validate/` is a stub, nothing has read the held-back window, and nothing
computes a buy-and-hold benchmark. **No strategy has been validated, and no
figure from a search appears in this README.**

| Built | Not built |
|---|---|
| Protocol loader and validator | Walk-forward validation — no candidate has been tested on unseen data |
| Price data layer — validation, provenance, parquet cache | Any benchmark — nothing computes buy-and-hold |
| Cost model — hand-verified arithmetic | The noise band drawn on the figure's own axis |
| Strategy-config loader — closed schema, closed vocabulary | Decision logging, risk limits, venue adapters |
| The noise ceiling — block-resampled surrogates, `--trials` | Rejection *on* the ceiling — it informs, it does not gate |
| Strategy evaluation (`core.evaluate`), with the lookahead test | Equity curves — not stored, so a cell cannot be clicked through |
| Backtest engine and metrics, with the determinism test | |
| Grid expansion (`search.expand_grid`) — the 90 cells of the committed config | |
| Plateau scoring, the edge check, the degeneracy check | |
| The graveyard — append-only JSON Lines, closed reason vocabulary | |
| The search loop — grid → engine → scoring → graveyard, with the window test and an input gate | |
| The surface report — faceted heatmap, self-contained HTML, no build step | |
| `status`, `fetch`, `search` and `report` commands *(untested)* | |

**652 tests pass** — verify with `uv run pytest`. They cover the protocol
loader, the data layer, the cost model, the strategy-config loader, strategy
evaluation, the performance metrics, the backtest engine, grid expansion,
plateau scoring, the graveyard, the search loop and the reports — twelve areas,
one per test file.
The four CLI commands have no tests, and neither does the vendor fetch path,
which by policy no test may call. Everything else is a signature and a
docstring contract with a `raise NotImplementedError` body — 15 of them across 6
files (`grep -r "raise NotImplementedError" src/ --include='*.py' | wc -l`) — so
that each later component is built against a fixed interface.

**The split now has a guard, not just an absence.** `fetch` caches 2005–2024 in
one file and hands all of it to the search; the search cuts it down from
`protocol.windows.in_sample` and from nothing else.
`tests/test_search_loop.py` corrupts every bar after the window's end, passes
the frame in intact, and asserts both the bar count and that no cell's result
moves. The count assertion is not decoration: an audit showed the corruption
alone is blind to a one-bar leak on this fixture, and `LIMITATIONS.md` says why.
The guard protects one consumer — the next component that reads bars will need
the same treatment, and `docs/architecture.md` says how.

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

Full version, with every module marked built or stubbed, in
[docs/architecture.md](docs/architecture.md).

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). The clone and the
first `uv run` need the network to fetch dependencies; **the test suite itself
makes no network call**, which is the property that matters.

```bash
git clone <this-repo> && cd StrategyLab
uv run pytest                   # 652 tests, none touching the network
uv run strategylab status       # what exists, and what to run next
```

`status` reads the protocol and reports where the project is. It never changes
anything, so it is safe to run at any time.

Three further commands do the work. `fetch` is **the only command that touches
the network**, so unlike the others it can fail for reasons outside this
repository. `search` needs `fetch` to have run, and `report` needs `search`:

```bash
uv run strategylab fetch                              # caches SPY, with provenance
uv run strategylab search configs/ma_crossover.yaml   # ~4 min; writes state/search-*.json
uv run strategylab search configs/ma_crossover.yaml --trials 100   # + noise ceiling; hours
uv run strategylab report                             # renders those to reports/*.html
```

`search` reads only 2005–2018, whatever the cache holds. `report` recomputes
nothing — it re-renders what is already on disk, so changing a template costs no
protocol access. Both report no noise ceiling, because that component is not
built.

Run every command from the repository root — paths in the protocol are relative
to the working directory.

## Methodology

The full argument is in [docs/methodology.md](docs/methodology.md). In short:

**The split.** Parameter search may look at 2005–2018 and nothing else.
2019–2024 is held back and used only to test a candidate already chosen. The
loader refuses a protocol whose windows overlap. Every read of the held-back
window is meant to increment a counter, because the split degrades with use and
that degradation should be visible rather than forgotten. *The counter is built
and tested but nothing calls it yet, because nothing reads that window yet.*

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
search takes about four minutes, so a hundred trials is most of a day. A search
run without it says on every line of output that nothing it printed is evidence
of an edge. **The ceiling informs; it does not gate** — `select_candidate` never
sees it, so a candidate can be nominated while sitting inside the noise.

**Three outcomes, not two.** *Not yet implemented.* Validation returns pass,
fail, or `insufficient_data`. The third is not a soft failure — it means the strategy was
never really tested. Conflating it with failure throws away a candidate that was
never refuted.

## Results

**A search has been run. No figure from it is quoted here, and that is a
decision rather than an omission.**

```bash
uv run strategylab fetch                                  # caches SPY 2005-2024
uv run strategylab search configs/ma_crossover.yaml       # ~4 minutes, 90 cells
```

That writes every cell to `state/search-<stamp>.json` with the protocol version,
the in-sample window, the content hash of the snapshot it ran against, the
winner, and every rejection. `uv run strategylab report` turns each of those
into a self-contained HTML page: the surface as a heatmap faceted by the third
parameter, coloured by each cell's own Sharpe so a spike still reads as a spike,
with failed cells hatched and the winner accented. Both `state/` and `reports/`
are gitignored, so the files are local to whoever ran them.

Nothing from it appears in this section because a search result without a noise
ceiling is not a weak finding — it is not a finding. The best of ninety
parameter combinations posting a positive Sharpe is what noise does. Publishing
that number and captioning it with a caveat is how a reader ends up remembering
the number, so the number stays out until there is a ceiling beside it. When
there is, it will appear here with the command that produced it, the protocol
version in force, and the content hash of the data it used.

## Limitations

[LIMITATIONS.md](LIMITATIONS.md) was written before any result existed, which is
the only time such a document gets written honestly. The most important entries:
the strategy is searched on `SPY` but would be traded as `VUAG.L`, which did not
exist for most of the in-sample window; the cost model's minimum-commission and
FX-conversion terms are both configured to zero; and the noise ceiling does not
currently feed the pass decision.

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

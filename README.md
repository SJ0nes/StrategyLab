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

**Foundations only. No strategy has been backtested, searched, or validated,
because the backtester, the search, and the validator are not written yet.**

| Built | Not built |
|---|---|
| Protocol loader and validator | Grid search, plateau scoring, noise ceiling |
| Price data layer — validation, provenance, parquet cache | Walk-forward validation |
| Cost model — hand-verified arithmetic | Decision logging, risk limits, venue adapters, HTML reports |
| Strategy-config loader — closed schema, closed vocabulary | |
| Strategy evaluation (`core.evaluate`), with the lookahead test | |
| Backtest engine and metrics, with the determinism test | |
| `status` and `fetch` commands *(untested)* | |

**338 tests pass** — verify with `uv run pytest`. They cover the protocol
loader, the data layer, the cost model, the strategy-config loader, strategy
evaluation, the performance metrics, and the backtest engine. The two CLI
commands have no tests,
and neither does the vendor fetch path, which by policy no test may call. Everything else is a signature and a
docstring contract with a `raise NotImplementedError` body — 23 of them across 10
files (`grep -r "raise NotImplementedError" src/ --include='*.py' | wc -l`) — so
that each later component is built against a fixed interface.

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

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Both commands below
were last run against a fresh clone of `58217b7` with an empty `data/` cache:
154 passed, and `status` reported `data  not cached`. The suite has grown since
— `uv run pytest` reports 338 passed in the working tree, and the fresh-clone
run has not been repeated at that count. The clone and the first `uv run` need
the network to fetch dependencies; **the test suite itself makes no network
call**, which is the property that matters.

```bash
git clone <this-repo> && cd StrategyLab
uv run pytest                   # 338 tests, none touching the network
uv run strategylab status       # what exists, and what to run next
```

`status` reads the protocol and reports where the project is. It never changes
anything, so it is safe to run at any time.

A fourth command downloads price data. **It is the only command that touches the
network**, so unlike the three above it can fail for reasons outside this
repository:

```bash
uv run strategylab fetch        # downloads and caches SPY, with provenance
```

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

**Plateaus over peaks.** *Not yet implemented.* The best single cell in a
parameter grid is usually the luckiest one. Candidates are scored by the median of their parameter
neighbourhood; winners sitting on a grid boundary are rejected as walls rather
than optima; and flatness caused by parameters doing nothing is flagged rather
than reported as robustness.

**The noise ceiling.** *Not yet implemented.* The expected maximum across 90
draws from noise is well above zero, so the best of 90 parameter combinations
(6 x 5 x 3, from the `search` block of `configs/ma_crossover.yaml`)
will post a positive Sharpe on data with no *exploitable* structure in it. Every search therefore reruns its entire
grid against surrogate series and reports the distribution of best-in-grid
results. A winning Sharpe means nothing until compared against it.
*Not yet implemented.*

**Three outcomes, not two.** *Not yet implemented.* Validation returns pass,
fail, or `insufficient_data`. The third is not a soft failure — it means the strategy was
never really tested. Conflating it with failure throws away a candidate that was
never refuted.

## Results

**There are none.** No backtest has ever been run. When results exist, they will
appear here with the command that produced them, the protocol version in force,
and the fixture hash of the data they used.

## Limitations

[LIMITATIONS.md](LIMITATIONS.md) was written before any result existed, which is
the only time such a document gets written honestly. The most important entries:
the strategy is searched on `SPY` but would be traded as `VUAG.L`, which did not
exist for most of the in-sample window; the cost model's minimum-commission and
FX-conversion terms are both configured to zero; and the noise ceiling does not
currently feed the pass decision.

## How this was built

This repository was built in a single supervised session, pair-working with an
AI assistant. That is visible in `git log` if you look at the timestamps — the
whole history spans one sitting of a few hours, and
`git log --format=%ad --date=iso` will show you exactly how long. It is stated
here rather than left to be inferred. No figure is quoted because it grows with
every commit: the one time a precise span was written down it was already stale
at the commit that introduced it, so the command is the only honest form.

What that does and does not mean:

- **Every design decision was ratified by a human at three explicit gates**: the
  pre-registration in `protocol.yaml`, a review of every module contract, and a
  check of the first fetched prices against known values. The contract review
  came before every implementation except `protocol.py`, which was already
  written by then — so that one module was reviewed as code rather than as a
  contract. Be precise about what that means: the session prompt in
  `.claude/setup.md` is tracked in this repository, and its suggested defaults
  are verbatim the values that ended up in `protocol.yaml`. So the instrument,
  the windows, and the cost rates were *chosen from* proposed defaults rather
  than originated. The amendments at the second gate were not — those came from
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
  the point it appears.

The reason to say this plainly is the same reason `LIMITATIONS.md` exists: a
reviewer who works it out for themselves reads it as concealment, and a reviewer
told upfront can weigh it.

## Licence

MIT — see [LICENSE](LICENSE).

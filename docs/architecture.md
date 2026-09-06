# Architecture

The intended system, and what actually exists today. The two are very different,
and this document keeps them visibly apart: everything marked **STUB** has a
signature and a docstring contract but a `raise NotImplementedError` body.

## The shape of it

```
                       protocol.yaml  ── the pre-registration, written once
                             │
                             ▼
                     ┌───────────────┐
                     │  protocol.py  │  BUILT · the only reader of that file
                     └───────┬───────┘  percent → fraction, exactly once
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                     ▼
┌───────────────┐   ┌─────────────────┐   ┌────────────────┐
│    data/      │   │    backtest/    │   │     risk/      │
│               │   │                 │   │                │
│ source.py     │   │ costs.py  BUILT │   │ limits.py STUB │
│        BUILT  │   │ engine.py BUILT │   │ + no config    │
│               │   │ metrics.py BUILT│   │   file exists  │
└───────┬───────┘   └────────┬────────┘   └────────┬───────┘
        │                    │                     │
        │  bars              │  fills, equity      │  veto
        │                    │                     │
        │            ┌───────┴────────┐            │
        └───────────▶│     core/      │◀───────────┘
                     │                │
                     │ strategy.py    │  BUILT — the shared brain.
                     │      BUILT     │  Used by BOTH the backtester and
                     │ types.py BUILT │  the live worker. Written once.
                     │ config.py BUILT│  The only reader of configs/.
                     └───────┬────────┘
                             │  Intent
              ┌──────────────┼──────────────┐
              ▼                             ▼
     ┌────────────────┐            ┌─────────────────┐
     │    search/     │            │   execution/    │
     │ grid.py  BUILT │            │ venue.py   STUB │
     │plateau.py BUILT│            │ t212.py    STUB │
     │ noise.py BUILT │            └────────┬────────┘
     │surrogate BUILT │
     │graveyard  BUILT│                     │
     │ loop.py  BUILT │                     │
     └───────┬────────┘                     │
             │                              │
             ▼                              ▼
     ┌────────────────┐            ┌─────────────────┐
     │   validate/    │            │    logging/     │
     │ walkforward    │            │ decisions.py    │
     │        BUILT   │            │          STUB   │
     │ candidate.py   │            │                 │
     │        BUILT   │            │                 │
     └───────┬────────┘            └────────┬────────┘
             │                              │
             └──────────────┬───────────────┘
                            ▼
                   ┌─────────────────┐
                   │     report/     │
                   │ render.py BUILT │
                   │ surface.py BUILT│
                   │ palette.py BUILT│
                   │ + templates/    │
                   └─────────────────┘
```

`cli.py` sits across the top of all of it. **BUILT:** `status`, `fetch`,
`search`, `report`, `validate`. **STUB:** `run`, `diff`.

## What is built

| Module | What works | Tests |
|---|---|---|
| `windows.py` | `in_sample_bars` and `walk_forward_bars` — the one place any component asks which bars it may see. The held-back accessor increments the protocol's touch counter before returning a bar and takes the file it will increment, so tests point it at a copy. No dry run, deliberately. | 16 |
| `protocol.py` | Full loader and validator. Window ordering and non-overlap. The single percent→fraction conversion. The access counter, byte-preserving on LF and CRLF alike. | 34 |
| `data/source.py` | `validate_bars`, `content_hash`, `cache_write`/`cache_read`/`is_cached`, the `CacheEntry` provenance sidecar, and `YFinanceSource.fetch`. | 40 |
| `backtest/costs.py` | `apply_costs` — half-spread plus slippage on the price, commission and FX as cash on the executed notional. | 81 |
| `backtest/engine.py` | `run_backtest` — `evaluate` stepped bar by bar with costs applied. Signal from a close, fill at the NEXT bar's open. Carries the lookahead test that can actually fail, plus the determinism test. | 33 |
| `backtest/metrics.py` | `sharpe` — annualised, sample standard deviation, zero risk-free rate. `max_drawdown` — largest peak-to-trough decline against the RUNNING peak, as a positive percentage. | 28 |
| `core/strategy.py` | `evaluate` — the shared brain, used by the backtester and the live worker alike. Two indicators and three operators, matching the loader's vocabulary. Ships with the lookahead test. | 41 |
| `core/config.py` | `load_strategy` — the only reader of `configs/`. Closed schema, closed indicator and operator vocabulary, and the cross-check that every `$placeholder` has a parameter behind it and every searched parameter is used. Does not resolve placeholders. | 81 |
| `core/types.py` | The value types shared by the backtester and the live worker. Types rather than behaviour, apart from one guard on `IndicatorRef`. | in the 81 |
| `search/graveyard.py` | `record_rejection` — append-only JSON Lines, one object per line, closed reason vocabulary, non-finite metric written as JSON null so the file stays parseable by something other than Python. | 33 |
| `search/plateau.py` | `plateau_score` — neighbourhood median, radius in grid steps, with `neighbour_count` so a thin neighbourhood is visible. `is_edge_winner` — against the highest value the grid REACHES, not the declared `max`. `is_degenerate` — over trade sets, not trade counts. Fixes the results-frame schema the search loop must emit. | 51 |
| `search/grid.py` | `expand_grid` — every combination of the declared ranges, inclusive of `max` only on a step boundary, integer arithmetic throughout, and validation that refuses a searched range for every reason the config loader does, bar three: two cross-block checks it cannot make from the `search` block alone, and a huge integer bound it accepts because it never converts to float. Produces the `params` mapping that makes a `StrategyDef` evaluable. `plateau.is_edge_winner` calls it to build an axis, so the two can never disagree about which values a range generates. Reached by `strategylab search`, through both `cli.search` and `search/loop.py`. | 74 |
| `search/loop.py` | `run_search` — the caller everything above was waiting for. Cuts the bars to `protocol.windows.in_sample` and to nothing else, walks the grid, fingerprints each cell's trades, scores the surface, and takes the best surviving cell. `select_candidate` ranks by plateau score rather than by Sharpe, rejects an edge winner or a drawdown breach and moves on, and flags a degenerate plateau rather than rejecting it. `write_search_result` persists the whole surface. Refuses a bad input up front with `SearchError`, and refuses a grid in which every cell failed - a fault in the definition or the data would otherwise read as a research finding. Carries the window test, which asserts the bar count as well as the values, because the corruption half alone cannot see a one-bar leak. | 80 |
| `execution/venue.py` | The venue interface and its data classes. A `typing.Protocol`, so no `NotImplementedError` appears in it. | none |
| `search/surrogate.py` | `block_length_for` and `block_resample` — moving-block resampling over bar INDICES, so a bar's own shape travels with its return. Block length is the grid's longest lookback, per `docs/decisions/0008`. Log returns, so a surrogate price can never reach zero. | 36 |
| `search/noise.py` | `noise_ceiling` — the whole grid against `n_trials` surrogates, recording the best Sharpe on each. Failed cells skipped rather than scored zero; a trial with nothing scoreable is `NaN` rather than dropped. `trials_note` says what a trial count buys. | in the 36 |
| `report/render.py` | `render` — one self-contained page from a bundled Jinja2 template. Refuses a context missing any traceability key, and refuses a rendered page that references anything outside itself. | 48 |
| `report/surface.py` | `build_context` and `render_surface` — the search surface as a faceted heatmap, coloured by each cell's OWN metric so a spike still reads as a spike. Failed cells hatched, winner accented, rejections ringed. | in the 48 |
| `report/palette.py` | The one colour ramp: viridis, sampled at ten stops, hand-coded so the page needs no plotting dependency. | in the 48 |
| `validate/walkforward.py` | `walk_forward` — the second consumer of `windows.py`, and the first caller the access counter has ever had. One continuous backtest over the held-back window, warmed up from the tail of the in-sample window, sliced into folds by date. Pooled figures carry the pass decision; per-fold ones are advisory. A non-finite pooled Sharpe is `insufficient_data`, never a pass. `write_validation_result` persists the verdict, because re-deriving one costs another touch. | 46 |
| `validate/candidate.py` | `read_candidate` — the winner out of a search artifact, refusing a mismatched schema version, protocol version or snapshot hash BEFORE any touch is charged. Defaults nothing: a missing field is refused rather than filled in, because a default would be recorded in the verdict as though the search had stated it. | 24 |
| `cli.py` | `status`, `fetch`, `search`, `report` and `validate`. | none |

**746 tests pass** — 34 + 40 + 81 + 81 + 41 + 28 + 33 + 74 + 51 + 33 + 80 + 48 + 36 + 16 + 46 + 24. Run `uv run pytest` to see the
count yourself.

One caveat inside `data/`: `YFinanceSource.fetch` and the vendor-frame
normalisation behind it are **written but untested**, because no test may make a
network call. What the fixture tests exercise is the cache path.

## What is stubbed

Every one of these has its full signature and docstring contract, so a later
session builds against a fixed interface rather than inventing one. **12
`NotImplementedError` bodies across 4 files:**

`cli.py` (2 — `run` and `diff`) · `execution/t212.py` (6) ·
`logging/decisions.py` (2) ·
`risk/limits.py` (2).

`execution/venue.py` is marked STUB in the diagram above but is **not** in that
list and contains no `NotImplementedError`. It is a `typing.Protocol` — a
structural interface whose methods have `...` bodies by design, which is what an
interface looks like rather than an unwritten function. `data/source.py`'s
`PriceSource` is the same shape inside a module that is otherwise BUILT.

## What is not even stubbed

Listed separately, because a reader scanning module names will otherwise assume
these exist:

- **`configs/risk_limits.yaml`** — `risk/limits.py` names it as its default path.
  The file does not exist, so the risk layer has no configuration to read.
- **`tests/test_risk.py`** — `CLAUDE.md` says the risk layer has its own file and
  its own tests. It has the file (`risk/limits.py`, stubbed); it has no tests.
- **Any benchmark computation** — nothing computes buy-and-hold, so no result can
  yet be compared against simply owning the index. This is the largest remaining
  hole in the search output: `search` now prints a Sharpe with no benchmark and
  no noise ceiling beside it.
- **Any comparison of a verdict against a benchmark or a ceiling.** The counter
  now has a caller — `windows.walk_forward_bars`, reached by
  `validate.walk_forward` — so the held-back window is readable and read. What
  is still missing is anything to read the verdict *against*: no buy-and-hold,
  and no rejection on the noise ceiling. A `pass` would mean "cleared a
  pre-registered Sharpe bar", not "beat owning the index".
- **A noise ceiling by default.** `search` computes one only when given
  `--trials`, because each trial costs a full grid. Without it the artifact
  records `"noise_ceiling": null` and every output says nothing it printed is
  evidence of an edge.
- **Any equity curve, anywhere.** The report's contract asks for click-a-cell to
  show one and the search artifact carries none: ninety curves over three and a
  half thousand bars is tens of megabytes against a thirty-kilobyte file. What
  to store is undecided, so that interaction does not exist.
- **Any noise band on a figure.** The report standard requires the ceiling drawn
  on the same axis as the result so a winner inside the noise is *seen* rather
  than read. A ceiling now exists, but the page states it as a sentence and a
  percentile; the band itself is roadmap session 8.
- **Any rejection on the noise ceiling.** `select_candidate` never receives it,
  so a candidate can be nominated while sitting inside the noise distribution.
  The ceiling informs a human; it does not gate anything.
- **A `StrategyDef` with its placeholders substituted into it.** `expand_grid`
  is built, but it does not rewrite a definition: it produces the `params`
  mapping that `evaluate` resolves each `$placeholder` against, once per bar. So
  the periods in a `StrategyDef` are still `$fast`, by design — one parsed
  definition serves all ninety cells — and any future reader expecting a
  resolved definition will not find one.

## The one thing the diagram does not show

**`fetch` caches the in-sample and the held-back windows in the same file.** It
requests 2005-01-01 through 2024-12-31 in a single call and writes one parquet
snapshot. Nothing partitions that frame, and the whole of it — 5,033 bars — is
handed to `run_search`.

The in-sample/walk-forward split is this project's central methodological claim.
Until the search loop existed it was enforced by the absence of anything that
could violate it. It is now enforced by `windows.in_sample_bars`, which cuts the
frame down from `protocol.windows.in_sample` and from nothing else — not from
the frame it was handed, not from an argument a caller could widen — and by
`tests/test_search_loop.py`, which corrupts every bar after the window's end,
passes the frame in intact, and asserts no cell's result moves.

**That chokepoint now exists, and it now has two consumers rather than one.**
`windows.py` owns both accessors and every component asks it for bars.
`in_sample_bars` is a plain slice; `walk_forward_bars` CANNOT BE CALLED WITHOUT
INCREMENTING the protocol's touch counter, and takes the protocol file it will
increment so tests and development can point it at a copy. There is deliberately
no dry run: a mode returning held-back bars without charging for them is the
loophole that would make the counter meaningless.

The search loop's private `_in_sample_window` is gone rather than kept beside
it, so there is one slicer and it cannot drift.

**`validate.walk_forward` is the second consumer, and it uses BOTH accessors.**
It reads the held-back window through the paid one, and the tail of the in-sample
window through the free one — `warmup - 1` bars of it, so the equity curve starts
on the held-back window's first bar rather than seven months into it. That is a
warm-up, not a leak: the rule is that the search must not see the future, and
this is a held-back evaluation reading its own past. `docs/decisions/0009` argues
it out, and `tests/test_walkforward.py` corrupts the bars on BOTH sides of what
the validator may read — the future beyond the window's end, and the in-sample
past beyond the warm-up — and asserts no fold moves.

## Design rules the structure encodes

- **One strategy implementation.** `core/` is shared by the backtester and the
  live worker. Two implementations would drift, and the backtest would stop
  describing the thing that is actually trading.
- **The risk layer reads its own config.** No search, no strategy file, and no
  review may reach it. A limit a search can relax is not a limit.
- **Costs are protocol data; the fill formula is code.** A buyer crosses half the
  spread under every broker; what that spread is depends on the broker.
- **One conversion boundary.** `load_protocol` divides rates by 100, once.
  Everything downstream carries fractions.
- **Decision logs share a schema** between backtest and live. That is what makes
  `diff` possible, and it is why the schema is defined once in `logging/`.

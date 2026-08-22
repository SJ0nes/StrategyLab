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
     │ noise.py  STUB │            └────────┬────────┘
     │graveyard  BUILT│                     │
     │ loop.py  BUILT │                     │
     └───────┬────────┘                     │
             │                              │
             ▼                              ▼
     ┌────────────────┐            ┌─────────────────┐
     │   validate/    │            │    logging/     │
     │ walkforward    │            │ decisions.py    │
     │         STUB   │            │          STUB   │
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
`search`, `report`. **STUB:** `validate`, `run`, `diff`.

## What is built

| Module | What works | Tests |
|---|---|---|
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
| `search/loop.py` | `run_search` — the caller everything above was waiting for. Cuts the bars to `protocol.windows.in_sample` and to nothing else, walks the grid, fingerprints each cell's trades, scores the surface, and takes the best surviving cell. `select_candidate` ranks by plateau score rather than by Sharpe, rejects an edge winner or a drawdown breach and moves on, and flags a degenerate plateau rather than rejecting it. `write_search_result` persists the whole surface. Refuses a bad input up front with `SearchError`, and refuses a grid in which every cell failed - a fault in the definition or the data would otherwise read as a research finding. Carries the window test, which asserts the bar count as well as the values, because the corruption half alone cannot see a one-bar leak. | 77 |
| `execution/venue.py` | The venue interface and its data classes. A `typing.Protocol`, so no `NotImplementedError` appears in it. | none |
| `report/render.py` | `render` — one self-contained page from a bundled Jinja2 template. Refuses a context missing any traceability key, and refuses a rendered page that references anything outside itself. | 32 |
| `report/surface.py` | `build_context` and `render_surface` — the search surface as a faceted heatmap, coloured by each cell's OWN metric so a spike still reads as a spike. Failed cells hatched, winner accented, rejections ringed. | in the 32 |
| `report/palette.py` | The one colour ramp: viridis, sampled at ten stops, hand-coded so the page needs no plotting dependency. | in the 32 |
| `cli.py` | `status`, `fetch`, `search` and `report`. | none |

**605 tests pass** — 34 + 40 + 81 + 81 + 41 + 28 + 33 + 74 + 51 + 33 + 77 + 32. Run `uv run pytest` to see the
count yourself.

One caveat inside `data/`: `YFinanceSource.fetch` and the vendor-frame
normalisation behind it are **written but untested**, because no test may make a
network call. What the fixture tests exercise is the cache path.

## What is stubbed

Every one of these has its full signature and docstring contract, so a later
session builds against a fixed interface rather than inventing one. **15
`NotImplementedError` bodies across 6 files:**

`cli.py` (3 — three of the seven commands) · `execution/t212.py` (6) ·
`logging/decisions.py` (2) ·
`risk/limits.py` (2) ·
`search/noise.py` ·
`validate/walkforward.py`.

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
- **Any caller of `increment_walk_forward_touches`** — the counter is built and
  tested and wired to nothing, because walk-forward validation does not exist.
  The counter reads zero because nothing has read the held-back window, and that
  is now guaranteed by `loop._in_sample_window` rather than by there being no
  reader at all.
- **Any noise ceiling.** `search/noise.py` is a stub, so `search` reports
  `"noise_ceiling": null` in every artifact it writes and says so on every run.
  Nothing it prints is yet evidence of an edge.
- **Any equity curve, anywhere.** The report's contract asks for click-a-cell to
  show one and the search artifact carries none: ninety curves over three and a
  half thousand bars is tens of megabytes against a thirty-kilobyte file. What
  to store is undecided, so that interaction does not exist.
- **Any noise band on a figure.** The report standard requires the ceiling drawn
  on the same axis as the result. `search/noise.py` is a stub, so every page
  states the absence where the band would be.
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
could violate it. It is now enforced by `loop._in_sample_window`, which cuts the
frame down from `protocol.windows.in_sample` and from nothing else — not from
the frame it was handed, not from an argument a caller could widen — and by
`tests/test_search_loop.py`, which corrupts every bar after the window's end,
passes the frame in intact, and asserts no cell's result moves.

Be precise about the scope of that. It guards ONE consumer. The next component
that reads bars gets no protection from it, and there is no shared chokepoint
that would give it any: `_in_sample_window` is private to the search loop. A
validator, a report or a live worker that slices the cached frame itself would
destroy the guarantee exactly as before. The right fix when the second consumer
arrives is to lift that function somewhere both can reach, not to copy it.

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

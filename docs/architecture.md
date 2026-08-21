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
│        BUILT  │   │ engine.py  STUB │   │ + no config    │
│               │   │ metrics.py STUB │   │   file exists  │
└───────┬───────┘   └────────┬────────┘   └────────┬───────┘
        │                    │                     │
        │  bars              │  fills, equity      │  veto
        │                    │                     │
        │            ┌───────┴────────┐            │
        └───────────▶│     core/      │◀───────────┘
                     │                │
                     │ strategy.py    │  STUB — the shared brain.
                     │      STUB      │  Used by BOTH the backtester and
                     │ types.py BUILT │  the live worker. Written once.
                     │ config.py BUILT│  The only reader of configs/.
                     └───────┬────────┘
                             │  Intent
              ┌──────────────┼──────────────┐
              ▼                             ▼
     ┌────────────────┐            ┌─────────────────┐
     │    search/     │            │   execution/    │
     │ grid.py   STUB │            │ venue.py   STUB │
     │ plateau.py STUB│            │ t212.py    STUB │
     │ noise.py  STUB │            └────────┬────────┘
     │ graveyard  STUB│                     │
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
                   │ render.py  STUB │
                   │ + no templates  │
                   └─────────────────┘
```

`cli.py` sits across the top of all of it. **BUILT:** `status`, `fetch`.
**STUB:** `search`, `validate`, `report`, `run`, `diff`.

## What is built

| Module | What works | Tests |
|---|---|---|
| `protocol.py` | Full loader and validator. Window ordering and non-overlap. The single percent→fraction conversion. The access counter, byte-preserving on LF and CRLF alike. | 33 |
| `data/source.py` | `validate_bars`, `content_hash`, `cache_write`/`cache_read`/`is_cached`, the `CacheEntry` provenance sidecar, and `YFinanceSource.fetch`. | 40 |
| `backtest/costs.py` | `apply_costs` — half-spread plus slippage on the price, commission and FX as cash on the executed notional. | 81 |
| `core/config.py` | `load_strategy` — the only reader of `configs/`. Closed schema, closed indicator and operator vocabulary, and the cross-check that every `$placeholder` has a parameter behind it and every searched parameter is used. Does not resolve placeholders. | 63 |
| `core/types.py` | The value types shared by the backtester and the live worker. Types rather than behaviour, apart from one guard on `IndicatorRef`. | in the 63 |
| `execution/venue.py` | The venue interface and its data classes. A `typing.Protocol`, so no `NotImplementedError` appears in it. | none |
| `cli.py` | `status` and `fetch`. | none |

**217 tests pass** — 33 + 40 + 81 + 63. Run `uv run pytest` to see the count
yourself.

One caveat inside `data/`: `YFinanceSource.fetch` and the vendor-frame
normalisation behind it are **written but untested**, because no test may make a
network call. What the fixture tests exercise is the cache path.

## What is stubbed

Every one of these has its full signature and docstring contract, so a later
session builds against a fixed interface rather than inventing one. **27
`NotImplementedError` bodies across 13 files:**

`cli.py` (5 — five of the seven commands) · `execution/t212.py` (6) ·
`search/plateau.py` (3) · `backtest/metrics.py` (2) · `logging/decisions.py` (2) ·
`risk/limits.py` (2) · `core/strategy.py` · `backtest/engine.py` ·
`search/grid.py` · `search/noise.py` · `search/graveyard.py` ·
`validate/walkforward.py` · `report/render.py`.

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
- **The report template directory** — `report/render.py` resolves templates
  against the package. There is no template directory and no package data.
- **The lookahead test and the determinism test** — the two guarantees the
  project's correctness rests on. Neither exists, because neither implementation
  does.
- **Any benchmark computation** — nothing computes buy-and-hold, so no result can
  yet be compared against simply owning the index.
- **Any caller of `increment_walk_forward_touches`** — the counter is built and
  tested and wired to nothing, because walk-forward validation does not exist.
- **Any caller of `load_strategy`.** The loader is built and tested; nothing
  calls it. `cli.search` takes a config path and raises `NotImplementedError`
  before reading it.
- **Placeholder resolution.** `load_strategy` returns a `StrategyDef` whose
  periods may still be `$fast` rather than a number. Substituting the grid's
  values belongs with `expand_grid`, which is a stub, so no `StrategyDef` in this
  repository has ever been made evaluable.
- **An engine that implements the vocabulary.** `core/config.py` declares two
  indicators and three operators and enforces them at load time, but
  `core.evaluate` is a stub, so nothing computes an `sma` or decides what
  `crosses_above` means. The vocabulary is what the engine will be built against,
  not a description of running code.

## The one thing the diagram does not show

**`fetch` caches the in-sample and the held-back windows in the same file.** It
requests 2005-01-01 through 2024-12-31 in a single call and writes one parquet
snapshot. Nothing partitions that frame, and nothing stops a future component
slicing past 2018-12-31 out of it.

The in-sample/walk-forward split is this project's central methodological claim.
Right now it is enforced by the absence of anything that could violate it — not
by a guard. Any component that reads bars must take its window from
`protocol.windows`, and the first one that does not silently destroys the
guarantee the whole design exists to provide.

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

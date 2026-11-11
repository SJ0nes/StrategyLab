# Architecture

The intended system, and what actually exists today. For most of this
project's history the two were very different and this document kept them
visibly apart with a **STUB** marker. Nothing carries that marker any more; the
distinction that remains is between what is BUILT and what has RUN, and it is
drawn at the end of this file and at length in `LIMITATIONS.md`.

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
│ source.py     │   │ costs.py  BUILT │   │ limits.py BUILT│
│        BUILT  │   │ engine.py BUILT │   │ + its own      │
│               │   │ metrics.py BUILT│   │   config file  │
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
                     │ config.py BUILT│  The only reader of a STRATEGY config.
                     └───────┬────────┘
                             │  Intent
              ┌──────────────┼──────────────┐
              ▼                             ▼
     ┌────────────────┐            ┌─────────────────┐
     │    search/     │            │   execution/    │
     │ grid.py  BUILT │            │ venue.py  BUILT │
     │plateau.py BUILT│            │ t212.py   BUILT │
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
     │        BUILT   │            │         BUILT   │
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
                   │ verdict.py BUILT│
                   │ + templates/    │
                   └─────────────────┘
```

`cli.py` sits across the top of all of it, and all eight commands are
**BUILT**: `status`, `fetch`, `search`, `report`, `validate`, `run`, `diff`,
`serve`. `run` and `diff` are thin: their decisions live in `live/worker.py`, which is not in the
diagram because it sits between `core/`, `risk/`, `execution/` and `logging/`
rather than in any one column, and it is the module `tests/test_worker.py`
covers.

## What is built

| Module | What works | Tests |
|---|---|---|
| `windows.py` | `in_sample_bars` and `walk_forward_bars` — the one place any component asks which bars it may see. The held-back accessor increments the protocol's touch counter before returning a bar and takes the file it will increment, so tests point it at a copy. No dry run, deliberately. | 16 |
| `protocol.py` | Full loader and validator. Window ordering and non-overlap. The single percent→fraction conversion. The access counter, byte-preserving on LF and CRLF alike. | 34 |
| `data/source.py` | `validate_bars`, `content_hash`, `cache_write`/`cache_read`/`is_cached`, the `CacheEntry` provenance sidecar, and `YFinanceSource.fetch`. | 48 |
| `backtest/costs.py` | `apply_costs` — half-spread plus slippage on the price, commission and FX as cash on the executed notional. | 81 |
| `backtest/engine.py` | `run_backtest` — `evaluate` stepped bar by bar with costs applied. Signal from a close, fill at the NEXT bar's open. Carries the lookahead test that can actually fail, plus the determinism test. `decision_records` renders a finished backtest in the shared decision-log schema, with the risk fields filled in rather than computed. | 40 |
| `backtest/metrics.py` | `sharpe` — annualised, sample standard deviation, zero risk-free rate. `max_drawdown` — largest peak-to-trough decline against the RUNNING peak, as a positive percentage. `buy_and_hold` — the Sharpe and drawdown of simply holding over a run of closes, with no costs and fully invested, so the bar it sets is slightly too high. | 42 |
| `core/strategy.py` | `evaluate` — the shared brain, used by the backtester and the live worker alike. Two indicators and three operators, matching the loader's vocabulary. Ships with the lookahead test. | 58 |
| `core/config.py` | `load_strategy` — the only reader of a STRATEGY config. `configs/` has a second reader since the risk layer arrived — `risk/limits.py` reads `configs/risk_limits.yaml` and nothing else — and neither module reads the other's file, which is the whole point of the risk layer having its own. Closed schema, closed indicator and operator vocabulary, the cross-check that every `$placeholder` has a parameter behind it and every searched parameter is used, and the refusal of two conditions in one list comparing against distinct literals that render alike at six significant figures — which a decision log would key alike and so record only one of. Does not resolve placeholders. Also `config_content_hash` — a digest of what a config MEANS rather than of its bytes, which a search records and `read_candidate` checks, so an edit is caught and a line ending is not. | 105 |
| `core/types.py` | The value types shared by the backtester and the live worker, plus `reference_name` — the one function that names a condition's side, used for the rendered log lines, for the keys of the two `Intent` mappings, and by the loader's collision check, so the three cannot drift. | in the 105 |
| `search/graveyard.py` | `record_rejection` — append-only JSON Lines, one object per line, closed reason vocabulary, non-finite metric written as JSON null so the file stays parseable by something other than Python. | 33 |
| `search/plateau.py` | `plateau_score` — neighbourhood median, radius in grid steps, with `neighbour_count` so a thin neighbourhood is visible. `is_edge_winner` — against the highest value the grid REACHES, not the declared `max`. `is_degenerate` — over trade sets, not trade counts. Fixes the results-frame schema the search loop must emit. | 51 |
| `search/grid.py` | `expand_grid` — every combination of the declared ranges, inclusive of `max` only on a step boundary, integer arithmetic throughout, and validation that refuses a searched range for every reason the config loader does, bar three: two cross-block checks it cannot make from the `search` block alone, and a huge integer bound it accepts because it never converts to float. Produces the `params` mapping that makes a `StrategyDef` evaluable. `plateau.is_edge_winner` calls it to build an axis, so the two can never disagree about which values a range generates. Reached by `strategylab search`, through both `cli.search` and `search/loop.py`. | 74 |
| `search/loop.py` | `run_search` — the caller everything above was waiting for. Cuts the bars to `protocol.windows.in_sample` and to nothing else, walks the grid, fingerprints each cell's trades, scores the surface, and takes the best surviving cell. `select_candidate` ranks by plateau score rather than by Sharpe, rejects an edge winner or a drawdown breach and moves on, and flags a degenerate plateau rather than rejecting it. `write_search_result` persists the whole surface. Refuses a bad input up front with `SearchError`, and refuses a grid in which every cell failed - a fault in the definition or the data would otherwise read as a research finding. Carries the window test, which asserts the bar count as well as the values, because the corruption half alone cannot see a one-bar leak. Applies the noise gate to the cell selection chose, records the verdict as `noise_gate`, and writes a failure to the graveyard under `below_noise_ceiling` while leaving the cell the winner. Records buy-and-hold over the winner's own bars, and every cell's equity curve thinned to at most 400 points for drawing - never for recomputing a metric. | 108 |
| `execution/venue.py` | The venue interface and its data classes. A `typing.Protocol`, so no `NotImplementedError` appears in it; its data classes are exercised through the adapter and the worker. | in the 39 and 50 |
| `execution/t212.py` | `T212Venue` — demo or live by explicit choice, never defaulted to live; market orders with direction as the sign of the quantity; balance under the five-second account-summary limit, waited out rather than refused; reconcile with the venue authoritative and `state/positions.json` rewritten whenever anything differs. NO RETRY on any method, because the order endpoint is not idempotent. The key is masked before any `T212Error` exists. Tested through an `httpx.MockTransport`: this side of the contract, not the vendor's. | 39 |
| `live/account.py` | `account_record` and `write_account` — one line per daily check in `state/account-live.jsonl`: the account's equity and cash in account currency, the units held of the instrument traded, and whether the check's order was asked for, sent or refused. Refuses a record that contradicts itself - an order id for an order not sent, a check with an order and an outcome of none, a figure that is not a number. What a decision log cannot say about the account. | 15 |
| `live/worker.py` | `decide`, `order_for`, `record`, `portfolio_state`, the session file — one bar per invocation, long-only and one position, an allowance of zero never read as an order. `replay` re-decides every live bar through `evaluate` alone; `compare` diffs seven fields per bar - `params` included, so a diff run against the wrong artifact says so - and deliberately not the three risk fields. `trip_if_reached` sets the daily-loss latch on any run that finds the limit reached and never clears it; the session file carries it across a restart, and a latch value that is not a boolean is taken as set, while a file it cannot read at all starts a new session. | 53 |
| `search/surrogate.py` | `block_length_for` and `block_resample` — moving-block resampling over bar INDICES, so a bar's own shape travels with its return. Block length is the grid's longest lookback, per `docs/decisions/0008`. Log returns, so a surrogate price can never reach zero. | 93 |
| `search/noise.py` | `noise_ceiling` — the whole grid against `n_trials` surrogates, recording the best Sharpe on each. Failed cells skipped rather than scored zero; a trial with nothing scoreable is `NaN` rather than dropped. The trials can be spread over processes (`workers`): every surrogate is drawn first, in order, from the one seed, so the count changes the time taken and not the result. `trials_note` says what a trial count buys. `evaluate_gate` — whether a candidate beat 95 in every 100 scoreable surrogates, counted rather than interpolated, with no verdict below 100 of them; `docs/decisions/0012`. | in the 93 |
| `report/render.py` | `render` — one self-contained page from a bundled Jinja2 template. Refuses a context missing any traceability key, and refuses a rendered page that references anything outside itself. | 72 |
| `report/surface.py` | `build_context` and `render_surface` — the search surface as a faceted heatmap, coloured by each cell's OWN metric so a spike still reads as a spike. Failed cells hatched, winner accented, rejections ringed. Below it, every Sharpe on one axis with the surrogates' median-to-95th band, the candidate and buy-and-hold; a click on a cell draws its stored equity curve against holding. The click-through is JavaScript and no test runs it. | in the 72 |
| `report/verdict.py` | `build_verdict_context` and `render_verdict` — a validation verdict as a page: the folds side by side on one Sharpe scale that always holds zero and the bar, with the pooled figure and buy-and-hold drawn across them. `insufficient_data` gets its own treatment at the same weight as a pass, and a fold that could not be measured is an outline, never a bar of height zero. Has only ever rendered a fixture. | in the 72 |
| `report/palette.py` | The one colour ramp: viridis, sampled at ten stops, hand-coded so the page needs no plotting dependency. | in the 72 |
| `validate/walkforward.py` | `walk_forward` — the second consumer of `windows.py`, and the first caller the access counter has ever had. One continuous backtest over the held-back window, warmed up from the tail of the in-sample window, sliced into folds by date. Pooled figures carry the pass decision; per-fold ones are advisory. A non-finite pooled Sharpe is `insufficient_data`, never a pass. `write_validation_result` persists the verdict, because re-deriving one costs another touch. Records buy-and-hold over the held-back bars beside the verdict, from the frame it was already charged for. | 57 |
| `validate/candidate.py` | `read_candidate` — the winner out of a search artifact, refusing a mismatched schema version, protocol version, snapshot hash or config hash BEFORE any touch is charged. Defaults nothing: a missing field is refused rather than filled in, because a default would be recorded in the verdict as though the search had stated it. Refuses a candidate whose search did not pass the noise gate, recounting the numbers rather than trusting the word; `run` and `diff` waive that by name. | 93 |
| `logging/decisions.py` | `DecisionRecord`, `write_decision`, `write_decisions` and `read_decisions` — the schema the backtester and the live worker are both meant to emit, as append-only JSON Lines. Round-trips exactly, which is the property `diff` rests on; refuses a malformed line rather than skipping it, because a hole in a log reads as a divergence. Emitted by the live worker, by the replay and by the backtester; `diff` reads the first two. | 143 |
| `risk/limits.py` | `load_limits` — the risk layer's own reader of its own file, `configs/risk_limits.yaml`: closed schema, nothing defaulted, a repeated key refused, and percentages left as percentages because the thresholds are compared against other percentages. `check` — the veto gate, in the documented order: kill switch, position cap, daily loss by BOTH routes (realised, and equity marked down from the session open, because each can hide the other), then exposure headroom. A size limit reduces, and the exposure limit reduces to the headroom left and rejects when there is none; the kill switch and the daily loss limit always reject. An intent asking for nothing is allowed at nothing, so no loss or exposure limit can block an intent that asks for no new exposure — which is what a well-formed exit is, while a malformed one carrying a size is gated like any other request. The kill switch is the one thing that refuses even a zero-size intent, and that is deliberate, per `docs/decisions/0010`. One branch returns no verdict at all: a `start_of_day_equity` that is not positive and finite RAISES, before even the kill switch is read, because it is the denominator of the daily loss limit and an account whose session open is zero is a programming error rather than a risk decision. Its one caller is `live/worker.py`, which has never run against the venue. `daily_loss_reached` asks the same two routes without an intent, and a state flagged `daily_loss_tripped` is refused any size for the rest of its session; the worker is what sets and keeps that flag, per `docs/decisions/0013`. | 520 |
| `cli.py` | All eight commands. `run` writes the decision record BEFORE sending the order, so a venue failure never costs the log the decision; `diff` uses no network. Tested in a throwaway workspace as far as what each command writes and refuses; never past the network or a held-back read. `search --stop-file` ends the search at the next finished ceiling trial with exit code 3 and no result, which is how the app stops one. | 41 |
| `api/overview.py` | `read_overview` — what the app's first screen is told: protocol version, access counter, cached prices, kill switch, whether a venue key is set, each strategy config and its newest search. Reads only. A figure that cannot be read comes back as missing with a reason, never as a default; no reason names a directory above the project; an artifact holding `NaN`, or a file too large to be what it claims, is passed over. | 33 |
| `api/reading.py`, `api/registry.py`, `api/history.py` | How the server reads a file it did not write: size-capped, refused if it holds anything a response cannot carry (a non-finite number, text that is not Unicode, absurd nesting), and each field let through only as the kind of thing it should be. `list_strategies` and `find_strategy` — the registry is `configs/`; an id from a request is compared, never opened. `list_searches`, `search_detail`, `list_exams`, `read_graveyard` — what has been run: every search (one unreadable or in an older format is listed with the reason, not dropped), one search cell by cell with each cell's equity curve, each read of the held-back window, and the graveyard with repeats collapsed. Derives one figure, a cell's total return as a PERCENTAGE from its recorded curve; passes on neither the instrument's closes nor the buy-and-hold benchmark. | 97 |
| `api/rules.py`, `api/drafts.py` | `describe` words a parsed strategy - every indicator and operator in the vocabulary, a test holding the two lists equal - and counts its versions without building the grid. `read_source` reads `configs/<id>.source.json`, the import skill's record of a video, letting each field through only as text with control characters removed and a link only if it is `https://`. `confirm` records that the operator read the rules, against the config hash the page showed them for and the source file's fingerprint, under a lock and through a renamed temporary file; `confirmation` reports it, and a change to either file undoes it. `docs/decisions/0018`. | 69 |
| `api/window_state.py` | What the window remembers between launches: whether the tour has been seen, in `state/window.json`. The page cannot keep it, because its storage belongs to an origin whose port changes at every launch. A damaged file offers the tour again. | 13 |
| `api/jobs.py`, `api/job_runner.py` | Work the app starts and does not wait for. `JOB_KINDS` is the closed list - a search, or a search with the trials the noise gate needs - and `command_for` builds the command from a kind and the registry, never from a request. `start_job` refuses while a job runs; the child is detached, started with `-P` and without the server's token or any venue setting, and writes to `state/jobs/`. `read_job` reads a job's state from its exit code or its heartbeat file, signalling no process; progress is the search's own trial line, written by `trial_line` and read by `parse_trial_line`. `request_stop` writes a file the search looks for between trials. `docs/decisions/0017`. | 113 |
| `api/app.py`, `api/server.py` | `create_app` — seven reading routes, one that remembers the tour, one that records a confirmation, and two that start or stop a job, behind `LocalOnly`, ASGI middleware that refuses a Host or Origin naming anything but this machine and any `/api/` request without the launch's token, for HTTP and WebSockets alike, and adds a content policy to every answer. `serve` binds `127.0.0.1` only, prints the port it got, and stops when its standard input closes or cannot be read. `docs/decisions/0014`. | 64 |

**2332 tests pass** — 34 + 48 + 81 + 105 + 58 + 42 + 40 + 74 + 51 + 33 + 108 + 72 + 93 + 16 + 57 + 93 + 143 + 520 + 39 + 53 + 15 + 41 + 33 + 97 + 115 + 69 + 13 + 64, plus 125 for
`.claude/hooks/check_secrets.py`, the commit-time credential scan, which is repository tooling
rather than a module in the table. 42 of the 125 plant a `core.fsmonitor` command in a scratch repository and skip themselves where the installed git does not run one. Run `uv run pytest` to see the
count yourself.

One caveat inside `data/`: `YFinanceSource.fetch` and the vendor-frame
normalisation behind it are tested **against a stand-in for the vendor library**,
because no test may make a network call. That covers the request and the
reshaping, not what the vendor really returns. The cache path is tested against
the recorded fixture.

## What is stubbed

Nothing. No `raise NotImplementedError` remains under `src/`. `execution/venue.py`
and `data/source.py`'s `PriceSource` are `typing.Protocol`s — structural
interfaces whose methods have `...` bodies by design — which is what an interface
looks like rather than an unwritten function.

What is BUILT is not the same as what has RUN. The Trading 212 adapter has never
sent a request to Trading 212, and `strategylab run` has never been invoked
against the venue; both are tested against a mock transport and literals.
`LIMITATIONS.md` has the list of what that leaves unproven.

## What does not exist

Listed separately, because a reader scanning module names will otherwise assume
these exist:

- **Any decision that uses the benchmark.** Buy-and-hold is computed and
  recorded beside a search's winner and beside a verdict, over the same bars as
  the figure it sits next to. Nothing compares them. A `pass` means "cleared
  the noise gate and a pre-registered Sharpe bar", not "beat owning the index".
- **A noise ceiling by default.** `search` computes one only when given
  `--trials`, because each trial costs a full grid. Without it the artifact
  records `"noise_ceiling": null` and every output says nothing it printed is
  evidence of an edge.
- **A comparison of two result sets.** The report standard asks that this
  week's search can be laid beside last week's. One artifact draws one page and
  nothing diffs two.
- **Any use of the ceiling in SELECTION.** `select_candidate` never receives
  it, so a candidate inside the noise distribution is still nominated. The gate
  comes after: it stops that candidate being validated, not being chosen.
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
  review may reach it. A limit a search can relax is not a limit. It is now built
  and it imports no other loader — it carries its own duplicate-key YAML loader
  rather than the identical one in `core/config.py`, so that a change to the
  strategy loader cannot change how a limit is parsed.
- **Costs are protocol data; the fill formula is code.** A buyer crosses half the
  spread under every broker; what that spread is depends on the broker.
- **One conversion boundary.** `load_protocol` divides rates by 100, once, and
  only for the rates under `costs:`. Everything downstream of that block carries
  fractions. The risk limits are not rates — nothing multiplies them by a price —
  so `configs/risk_limits.yaml` keeps percentages, and they are compared against
  other percentages.
- **Decision logs share a schema** between backtest and live. That is what makes
  `diff` possible, and it is why the schema is defined once in `logging/`.

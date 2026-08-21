# Project rules

## What this is
A backtesting and validation pipeline for trading strategies, plus a paper-trading
worker that measures how far live results drift from what the backtest predicted.
Discovery happens on years of history. Live trading tests the simulator, not the
strategy — there will never be enough live trades to judge a strategy.

## Non-negotiable design rules
- **One strategy implementation.** `core/` is used by backtester and live worker
  alike. Never write the logic twice — divergence silently invalidates every
  backtest result.
- **No lookahead.** `core.evaluate` may read up to and including the current bar
  and nothing after it. The test that proves it ships in the same commit as the
  implementation. BOTH NOW EXIST and the suite is green - but read the docstring
  at the top of `tests/test_strategy.py` before treating this as settled. The
  test named in this rule is close to true by construction, because `evaluate`
  is handed a window ending at the bar it evaluates; the test beside it, that a
  bar's intent does not change with the history in front of it, is the one that
  catches a real leak.
- **Config stays declarative.** Strategy conditions are a flat list combined with
  AND, from a fixed vocabulary. No nesting, no OR, no expressions in YAML. When a
  proposal needs more, that is a code change with a test. A config format that
  grows conditionals is a badly-built programming language with no debugger.
- **The risk layer is untouchable.** No search, no strategy config, no review may
  modify it. Own file, own tests.
- **The protocol is written once.** Editing `protocol.yaml` after results exist
  invalidates prior comparisons. If genuinely necessary, record why in
  `docs/decisions/` and reset the access counter.
- **Live data never selects anything.** It accumulates into history for future
  searches. It does not decide which strategy is good.
- **Plateaus over peaks.** Candidates score by neighbourhood median. Edge winners
  rejected. Degenerate plateaus flagged.
- **Every search reports its noise ceiling.** A winning Sharpe means nothing until
  compared against the best Sharpe the same grid achieves on surrogate series
  with no edge in them. Use BLOCK RESAMPLING, not an i.i.d. shuffle: shuffling
  destroys the autocorrelation that a trend strategy trades, which makes the
  surrogate an unfairly weak opponent and sets the bar far too low.

## Units — the rule that has already caused one bug
Rates cross a unit boundary exactly once, and the boundary is `load_protocol`.

- `protocol.yaml` stores **percentages** under `_pct` keys. `commission_pct: 0.05`
  means 0.05%, five basis points.
- Everything downstream carries **fractions** under `_frac` names.
  `commission_frac` is `0.0005`. `CostModel` holds fractions; `apply_costs` never
  sees a percentage.
- `load_protocol` divides by 100, once. `commission_min_flat` is a cash amount,
  not a rate, and is never converted.

State which of the two a number is in **every** docstring that touches a rate,
and state the units of every numeric argument — a rate of `0.05` means nothing
until the docstring says whether that is five percent or five basis points. A
hundred-fold error does not crash; it prints plausible wrong costs forever.
`tests/test_protocol.py` asserts the conversion rather than trusting a comment.

Beware `mid = 100.00` in any cost test: at that mid a rate as a percentage and
the same rate applied to the mid are numerically identical, so an implementation
that never multiplies by the mid reproduces every figure. Parameterise over
several mids. Round-trip cost is 0.23% of notional at *every* mid.

## Working rules
- **Never fabricate results.** If a test or backtest has not run, say so. No
  unverified numbers in a README, a docstring, or a commit message. Do not write
  "there is a test asserting this" until that test exists and has been run.
- **No test makes a network call.** Live fetching is a manual command only, and
  `yfinance` is imported inside the method body so no test can pull it in. A CI
  badge that goes red on a vendor rate limit is worse than no badge.
  Be precise about what this buys: the CACHE path is tested against the recorded
  fixture in `tests/fixtures/`. The FETCH path - `YFinanceSource.fetch` and the
  vendor-frame normalisation behind it - has no test at all, and cannot get one
  without either a recorded HTTP fixture or a fake source.
- **Determinism.** Same config, same data, same result. Required of the engine,
  and the test asserting it ships with the engine. NOT YET WRITTEN.
- **Ask before adding a dependency.** Name what it does and what it replaces.
- **One component per commit**, with its tests, conventional message
  (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`).
- **Tests accompany code.** Priority: the lookahead test, cost arithmetic, Sharpe
  and drawdown, venue implementations behaving identically, and a regression test
  that a known config produces a known equity curve.
- **A test that cannot fail is not a test.** Before trusting a new assertion,
  break the code it covers and confirm the test goes red. Hand-calculate expected
  values; never paste in what the implementation happened to print.
- **Update README and docs as you go**, not at the end.
- **Type hints and docstrings throughout.**

## Secrets and privacy — this repo is public
- `.env` is gitignored, and was before the first commit.
- Never hardcode, print, or log a credential. Venue adapters mask keys in traces.
- **No absolute paths containing a username** in any committed file.
- **No personal identifiers** in configs, commit messages, docs, or fixtures.
- **No real account numbers**, not even in examples.
- `data/`, `state/`, `*.log` gitignored — decision logs contain real trading
  activity. Commit small synthetic fixtures, never live output.
- DEMO Trading 212 credentials only. A live key is a separate, later decision.
- **Pushing is manual.** Public git history is permanent. Run the pre-push audit,
  read it, then push by hand.

## File hygiene
This repo is a portfolio piece.
- No dead code, no commented-out blocks, no `test_temp.py`, no stray notebooks.
- Nothing at the root that belongs in a subdirectory.
- Anything experimental lives in a branch, not `main`.

## README standard
What a reviewer reads first, and the single most important document here.
In this order:
1. What it is — two or three sentences, no jargon undefined.
2. **Current state** — what is built and what is not, stated plainly and near
   the top. A reviewer who discovers the gap themselves reads it as overclaiming;
   a reviewer told upfront reads it as judgement.
3. Architecture, with a diagram.
4. Quickstart — three commands. **They must actually run in a clean checkout.**
5. Methodology — the in-sample/walk-forward split, the noise ceiling, and why.
6. Results, with honest caveats, or a statement that there are none yet.
7. Link to LIMITATIONS.md.

No claim in it may be unverified. If a number appears, the command that produced
it appears too.

## Working with the person
- Explain plainly. Define jargon on first use.
- One question at a time.
- Don't oversimplify to be encouraging — they want the professional version.
- No financial advice. They decide what to run.

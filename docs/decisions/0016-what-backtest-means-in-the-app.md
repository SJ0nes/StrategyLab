# 0016 — What "Backtest" means in the app, and the window it may never leave

## Context

The approved mock (`docs/prototype/`) has a Backtest page: a period choice of
"5 years", "10 years" and "All history", a Run button, four figures and a growth
chart.

The project has no backtest command. Its commands are `status`, `fetch`,
`search`, `report`, `validate`, `run`, `diff` and `serve`. A single backtest
happens only inside a search, ninety times, and the search artifact records each
one's figures and a thinned equity curve.

The price cache holds every year fetched, 2005 to 2024. The protocol splits it:
2005 to 2018 is the in-sample window, and 2019 to 2024 is held back, read only by
`validate`, with every read counted and permanent.

## Who made this decision

**The assistant**, on 2026-11-11, while building the page. The operator approved
the mock, which shows the page; he was not asked about anything below, and has
not ratified it.

## Decision

**1. Today the Backtest page shows a replay that is already on record.** It is
the newest search's candidate: its total return, Sharpe, largest drawdown, trade
count and equity curve, over the window the search covered. The page says it is
the best of the versions that search tried, and that the best of many tries
flatters itself. The Run button is drawn and disabled.

**2. The page offers one period: the one the search covered.** It does not slice
the recorded curve into "5 years" or "10 years" and recompute a Sharpe or a
drawdown from the slice. The curve is thinned to at most 400 points and rounded
for drawing. A figure recomputed from it would be a second formula on coarser
data, and would disagree with the figure the search recorded for the same
version.

**3. When the app can run a backtest, no period it offers may reach past the end
of the in-sample window.** "All history" in the mock must mean all of the
in-sample window. A backtest over 2019 or later is a read of the held-back
years that no counter records, and after it the operator has seen how the
strategy did on the years `validate` exists to protect. The limit is to be
enforced in the server, where the bars are sliced, and not in the page.

## Consequences

- The page shows less than the mock: one period, and no way to run anything.
- "All history" will not mean all the history on disk. The page must say what it
  does mean.
- A backtest run from the app will be a new kind of result, with no artifact
  schema yet. Whether one is kept on record, and whether it appears on the Tests
  page, is not decided.

## Open

- Whether shorter periods inside the in-sample window are worth offering at all.
  Each one is another look at the same data, and a strategy can be tuned to a
  period as easily as to a parameter.

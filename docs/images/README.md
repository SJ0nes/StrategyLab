# Images

Screenshots for the top-level README, taken on 2026-11-18. None of them shows
an account, a balance or a key.

## The desktop window

`app-dashboard.png`, `app-tests.png`, `app-improve.png` and `app-compare.png`
are the real window (`desktop/`), started with `STRATEGYLAB_WORKSPACE` pointing
at a throwaway copy of the project and with every `T212_*` variable removed, so
it had no venue key ("No venue key set" in the top bar) and could reach no
account. The copy held `protocol.yaml`, `configs/`, the price caches under
`data/`, the built page, and from `state/` only the hundred-trial search
artifact of 2026-11-10 and the graveyard. It held no deployment, account log,
fills log, job or confirmation, so the window shows `ma_crossover` as a draft.
No button was pressed except the menu and the Tests row that opens its detail.
The window was captured at 1440 x 810 CSS pixels, scale 1.5, over its
debugging port. The growth figures beside each curve are a simulated
backtest's equity from a 10,000 start, not an account.

**Compare needs two strategies and the project has one.** For that picture
only, the copy also held `configs/ma_cross_unfiltered.yaml`: `ma_crossover`
with its trend filter taken out, a 30-cell grid of `fast` and `slow`. It was
searched once in the copy with `strategylab search
configs/ma_cross_unfiltered.yaml`, no `--trials`, so it has **no luck test**,
and its higher in-sample score is evidence of nothing until it has one. That
config is not part of this repository.

## The noise ceiling

`noise-ceiling.png` is the "Every sharpe on one axis" figure of the page
`strategylab report` renders from the same hundred-trial artifact, opened in a
browser. The upper row is the 90 cells of the grid, the lower row the best
cell the same grid found on each of 100 block-resampled surrogates, the shaded
band runs from their median to their 95th percentile, and the heavy marker is
the candidate at 0.640. The page states the figures the README's Results table
quotes: median 0.769, 90th 1.039, 95th 1.114, maximum 1.230, and 25 of 100
beaten where 95 were needed.

# App prototype

`index.html` is a clickable mock of a desktop app for this project. Open it in a
browser; it needs nothing else.

**It is a design reference, not software.** No button in it runs anything.
Every strategy, trade, test result and number it shows is invented sample data,
and the banner at the top of the page says so. Nothing in it is a result of this
project.

**The app it describes is not built.** The prototype also shows things the
project cannot do today: several deployed strategies, live performance figures,
pause and resume, scheduled price updates, and strategies imported from a
video. Those are planned work, not current behaviour. What the project can do
today is in the top-level `README.md`.

## What it is for

The operator approved this prototype on 2026-11-11 as the reference the app is
built against. A screen in the app is finished when it matches the same screen
here, checked side by side, including what each control opens.

## Decisions it records

- A dashboard for one chosen strategy opens first; a dropdown switches strategy.
- Sections: Dashboard, Trades, Tests (what a deployed strategy has done), and
  Backtest, Improve, Compare (work on strategies).
- A strategy that is not deployed is a draft. It cannot be deployed until it has
  passed the noise-ceiling check.
- Permanent or risky actions ask for a typed phrase.
- The page scales with the window, from a laptop to a 2560-wide monitor.
- **The app shows no buy-and-hold comparison.** The operator chose to remove it,
  after being told it is the figure that shows whether a strategy beats doing
  nothing. The pipeline still records it in every search and validation
  artifact, and the reports still show it.

## Words

The prototype uses plain names for the project's terms: "luck test" for the
noise ceiling, "final exam" for walk-forward validation on the held-back
window, "looks at unseen years" for `access_log.walk_forward_touches`, "loss
lock" for the daily-loss latch, "ruled out" for the graveyard.

One name does not map cleanly. The mock's "stop all buying" control says selling
stays allowed. The project's kill switch refuses every order, exits included.
`docs/decisions/0015` lists which of the two the control should be as an open
question.

# 0014 — A desktop app, drawn first, over the same functions the commands call

## Context

Everything this project does is reached by typing a command and reading what it
prints, or by opening one of two static report pages. That suits the person who
wrote the pipeline and nobody else. The operator asked for an app.

Three designs were drawn as clickable mocks before the fourth was kept. The
first two laid the pipeline out as screens; the third turned it into numbered
steps with an explanation on each. The operator rejected all three, the third
with the reason that mattered: none of them was what he wanted to see when the
app opened. What he wanted was a trading dashboard - how a deployed strategy is
doing - with the research tools beside it rather than in front of it. The mock
that came of that is `docs/prototype/index.html`, approved on 2026-11-11.

This record fixes what the app is before any of it is written. Nothing below is
built.

## Who made this decision

Both, and the split matters because the README says which decisions the
operator ratified.

**The operator decided:**

- The app opens on a dashboard for one strategy, with a dropdown to change
  strategy. Sections: Dashboard, Trades, Tests, and Backtest, Improve, Compare.
- It is a desktop app in an Electron window, with screens in React and
  TypeScript.
- It can run every command, including the two that cannot be undone or that
  place orders. Those ask for a typed phrase first.
- It fills the window it is in, from a laptop to a 2560-wide monitor, and shows
  less rather than more.
- Nothing the commands can do today is lost; it is automated or given a place.
- It has a first-time tour.
- A strategy can arrive as a file written from a video, and is a draft until it
  has passed the checks.
- **It shows no buy-and-hold comparison.** See *What this costs*.
- The build matches the approved mock screen by screen.

**The assistant decided, and the operator has not ratified:**

- How the window talks to the pipeline (point 1 below).
- That long work runs in child processes with a record on disk (point 3).
- The two Python dependencies and where the Node toolchain is allowed to live
  (point 4).
- The plain names the mock uses for the project's terms, listed in
  `docs/prototype/README.md`.

## Decision

**1. The window is a client of a local server, and the server is a thin layer
over the functions `cli.py` already calls.** `strategylab serve` starts an HTTP
server bound to `127.0.0.1` on a port the operating system picks. The Electron
process starts it, hands it a token made for that launch, and adds that token to
each request the window sends; a request without it is refused. The page's own
script never holds the token. Nothing listens on any other address, and a
request whose Host or Origin names anything but this machine is refused before
the token is looked at.

No strategy, cost, risk, scoring or validation logic is written in TypeScript.
`CLAUDE.md` forbids writing the strategy twice because divergence silently
invalidates every result, and a second language is the easiest place for a
second copy to appear. Where a command holds logic inline today, it moves into a
function that both the command and the server call.

**2. The app may do what the commands may do, and no more.** It reads
`configs/risk_limits.yaml` and `protocol.yaml` and cannot write either, except
for the one write `validate` already makes to the access counter. It never
shows, logs or returns a venue credential; it reports only whether one is
present. It can reach only a demo venue, by the same check `run` makes.

**3. Long work runs outside the server.** A search, a ceiling or a backtest is a
child process with a record under `state/jobs/`, one at a time, so that closing
the window does not end a seven-hour run and reopening it finds the run still
there.

**4. Two new Python dependencies, and a Node toolchain that stays in its own
directory.** `fastapi` and `uvicorn` serve HTTP; they replace nothing, because
the project serves nothing today. `httpx`, already a dependency, is what the
server's tests use. Everything Node - Electron, React, the bundler, the test
runner, a charting library - lives under `desktop/` with its own lockfile, and
nothing under `src/` imports from it.

**5. Actions that cannot be taken back ask for a typed phrase.** Reading the
held-back window, deploying, pausing, stopping all buying, and placing a
practice order each show what will happen and wait for the phrase. A button
alone is too easy to press.

**6. Missing is shown as missing.** A figure the app cannot compute - a live
Sharpe from three trades, a test that has not been run - is shown as a labelled
state with its reason, never as a number that looks like a result.

## What this costs

**The app can spend the held-back read.** Until now the only way to run
`validate` was to type it. A button, even behind a typed phrase, is nearer to
hand. The counter, the refusal without a passed noise gate, and the
main-checkout rule are unchanged, and they are what the protection has always
actually been.

**Hiding buy-and-hold removes the app's one honest yardstick.** A strategy that
returns four percent while its instrument returns nine has lost to doing
nothing, and a dashboard without the comparison shows that as a green number.
The operator was told this in those terms and chose to remove it. The pipeline
still computes and records the benchmark in every search and validation
artifact, and the reports still draw it; only the app does not show it.

**Two toolchains.** A reader who wants to run the app needs Node as well as
Python. The pipeline, its commands and its tests need Python only, and stay that
way.

**The front end is not tested by CI.** The workflow runs ruff, mypy and pytest.
Tests for the window run on the machine that builds it.

**A server is a new surface.** It is local, token-guarded and bound to the
loopback address, and it is still code that accepts requests and starts
processes. `CLAUDE.md` asks for a security review before committing anything
that makes a call leaving the machine; the server and the Electron main process
are reviewed on the same terms although neither does.

## What this does not decide

The mock shows several things the pipeline cannot do: more than one deployed
strategy, live performance figures, pause and resume, scheduled price updates
and daily checks. Each needs its own design. `docs/decisions/0015` takes the
first.

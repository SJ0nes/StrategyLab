# StrategyLab desktop window

An Electron window onto the project. It is a client of the local server
(`strategylab serve`) and holds no strategy, cost, risk or validation logic:
everything it shows was read or computed by the Python code under `src/`.
`docs/decisions/0014` records the design, and `docs/prototype/` is the mock the
finished app has to match.

## What is here today

A window that reads. It opens, starts the server, and shows:

- the top bar: a strategy dropdown, whether price data is stored and up to which
  bar, and whether a venue key is set;
- the six sections of the mock in the menu;
- **Dashboard** - for a draft, as every strategy is: its rules in words, what
  the video it came from said that the config leaves out, and the path to
  deployment - confirm the rules, backtest, luck test, deploy - with a button
  at the step it has reached. Deploy is disabled;
- **Improve** - the newest search as a grid, one square per version tried, with
  each version's growth line and figures; the versions ruled out across the
  project; and every search on record, including any that could not be read;
- **Tests** - every search and every read of the held-back window on record,
  newest first, each opening to a sentence about what it found;
- **Backtest** - the replay the newest search recorded for the version it put
  forward. One period only, the one the search covered; `docs/decisions/0016`
  says why.

Trades and Compare say they are not built.

**Rules are right** on the Dashboard records that the rules on screen were
read, for the file as it was shown; no search starts until it has been
pressed, and an edit to the file means pressing it again
(`docs/decisions/0018`).

Three buttons start work, and it is real work on the project the window is
serving:

- **Search again** (Improve) and **Run backtest** (Backtest) start
  `strategylab search` with no trials. Minutes. It writes a result and appends
  to the graveyard, as the command does.
- **Run the luck test** (Improve) starts `strategylab search --trials 100`.
  About an hour here, once, on fourteen processes.

Each opens a dialog that says what it will do and how long it takes, and starts
nothing until Start is pressed. One job runs at a time. A job is its own
process: closing the window does not stop it, and the next launch shows where
it has got to. A luck test can be asked to stop; it ends after the trials in
hand, writing no result. `docs/decisions/0017` records the design.

**Deploy…**, on a draft's last step, deploys it to the practice account once
its name is typed; an unproven strategy says so. The deployed strategy's
Dashboard shows its return, largest fall and live Sharpe as the venue
reported its account at each daily check, what it decided and why, its
position, the next check and any missed, with Pause, Undeploy and Check now.
Trades lists its orders and every day's decision. **Stop all trading** in the
top bar stops the daily check; a dialog asks before trading is allowed again.
Rules: `docs/decisions/0019`.

The app trades only with the practice account set in the environment it is
started from - `T212_ENVIRONMENT=demo` and `T212_API_KEY` - and only while it
is open. It does not read `.env`.

The final exam is drawn and disabled.

The first launch offers the **tour**: a welcome, then seven stops, each lit
with a spotlight and explained in a sentence, worded for what the app does
today. *Tour* in the top bar plays it again. The server remembers that it has
been seen, because the page's own storage is lost with each launch's port.

The window never shows the buy-and-hold comparison. The search records it and
the server leaves it out (`docs/decisions/0014`).

## Where the built screens differ from the mock

Compared side by side with `docs/prototype/index.html` at 2560x1440 and
1366x768. The layout, type, colours and chart are the mock's. The differences
are all places where the mock shows something the project does not have:

| Screen | The mock | The window | Why |
| --- | --- | --- | --- |
| Top bar | Running light, "Stop all buying", Tour | Draft, prices, venue key | Nothing is deployed; those controls are later work |
| Improve | "Short" and "long" averages, "Trend filter" | `fast`, `slow`, `trend` | The names are the config's, so another strategy needs no new wording |
| Improve | A LIVE square and "Against deployed version" | Neither | Nothing is deployed |
| Improve | "Run the luck test on this version" for any square | No such button; the luck test shows for the starred square only | The luck test runs on the version a search puts forward, not on a chosen one |
| Improve | Three figures beside the chart | Five: total return, worst fall and trades are added | They are recorded for every version |
| Improve, Ruled out | Three columns | Five: how many times recorded, and when last | The record repeats, and covers the whole project |
| Backtest | 5 years, 10 years, All history | One period, the search's | `docs/decisions/0016` |
| Backtest | "Run backtest" runs one backtest | It runs a search, the same as "Search again" | A search is the grid of backtests; the project has no single-backtest command (`docs/decisions/0017`) |
| Improve | A sped-up progress bar | A bar that moves only for a luck test, one step per finished trial | A plain search reports nothing until it ends |
| Tests | Rows for single backtests and live-against-backtest checks | Rows for searches and final exams only | Those are the tests the project keeps on record |

## Trying it on a copy

Pressing a button in the window starts a real search in this repository. To
press buttons without doing that, serve a copy:

```bash
mkdir ../../strategylab-copy
cp -r ../protocol.yaml ../configs ../data ../../strategylab-copy/
mkdir -p ../../strategylab-copy/desktop && cp -r dist ../../strategylab-copy/desktop/
STRATEGYLAB_WORKSPACE=../../strategylab-copy npm start
```

The window then reads and writes the copy. The code that runs is still this
repository's, and a job is started so that Python imports nothing from the
directory it runs in. The page shown is the copy's own `desktop/dist/`, so a
copy is to be trusted as much as whoever made it.

## Running it

Needs Node 22 or later, and the Python side set up as the top-level README
describes (`uv` on `PATH`).

```bash
cd desktop
npm install
npm start        # type-checks, builds, opens the window
```

The first `npm start` downloads the Electron binary. Closing the window stops
the server it started.

```bash
npm test         # component tests, in a simulated browser; opens no window
npm run typecheck
```

## How the pieces fit

- `electron/main.ts` starts `uv run --frozen strategylab serve`, reads the port
  it announces, and opens a window on `http://127.0.0.1:<port>/`. It makes a
  token for the launch, gives it to the server in its environment, and adds it
  to each API request on the way out. The page's script never holds the token.
- `electron/findOnPath.ts` finds `uv` without ever looking in the current
  directory, so a file of that name placed in the repository cannot be run in
  its stead.
- `src/` is the React page. `src/api.ts` describes what the server returns,
  `src/words.ts` is how the window words it, and `src/components/Chart.tsx`
  draws a growth line as plain SVG, with no charting library.
- `src/app.css` is the mock's stylesheet, copied. Change the mock first.
  `src/shell.css` holds the three places the real window differs from it.

The built page is served by the Python server, from the same origin as its API,
so no request crosses an origin and there is no CORS configuration to get wrong.

## What is not tested

No test launches Electron. `electron/main.ts` - starting the server, adding the
token, refusing to navigate away - was checked by running the app and looking,
twice, on Windows. The component tests cover the page and `findOnPath`. CI runs
none of this: the workflow is Python only.

Starting a job, closing the server and finding the job still running was
checked by hand, once, on Windows, on a copy of the project: see LIMITATIONS.
No test starts a real search.

The component tests check what a page says, not how it looks. A cell that
ignored its column span, because a class name meant two things, passed every
test and was found in a screenshot. Each screen was looked at in the real
window at 2560x1440 and at 1366x768; nothing compares a screen with the mock
automatically.

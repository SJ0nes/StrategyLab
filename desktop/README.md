# StrategyLab desktop window

An Electron window onto the project. It is a client of the local server
(`strategylab serve`) and holds no strategy, cost, risk or validation logic:
everything it shows was read or computed by the Python code under `src/`.
`docs/decisions/0014` records the design, and `docs/prototype/` is the mock the
finished app has to match.

## What is here today

A window onto the whole project. It opens, starts the server, and shows:

- the top bar: a strategy dropdown, whether price data is stored and up to which
  bar, and whether a venue key is set;
- the six sections of the mock in the menu;
- **Dashboard** - for a draft, as every strategy is: its rules in words, what
  the video it came from said that the config leaves out, and the path to
  deployment - confirm the rules, backtest, luck test, deploy - with a button
  at the step it has reached, ending in Deploy; for the deployed strategy, its
  live figures (below);
- **Improve** - the newest search as a grid, one square per version tried, with
  each version's growth line and figures; the versions ruled out across the
  project; and every search on record, including any that could not be read;
- **Tests** - every search and every read of the held-back window on record,
  newest first, each opening to a sentence about what it found;
- **Backtest** - the replay the newest search recorded for the version it put
  forward. One period only, the one the search covered; `docs/decisions/0016`
  says why.

Trades and Compare are described below.

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
Trades lists its orders, what each filled at once a later check has read it,
and every day's decision. **Stop all trading** in the
top bar stops the daily check; a dialog asks before trading is allowed again.
Rules: `docs/decisions/0019`.

The app trades only with the practice account set in the environment it is
started from - `T212_ENVIRONMENT=demo`, `T212_API_KEY` and, for a newer key, `T212_API_SECRET` - and only while it
is open. It does not read `.env`.

**Take final exam…** on Tests unlocks once the newest search on the file as
it is passed the luck test. Its dialog shows the looks used so far, and starts
it only once SPEND ONE LOOK is typed; it cannot be stopped. **Compare** sets two
strategies' newest searches side by side. Rules: `docs/decisions/0020`.

The first launch offers the **tour**: a welcome, then seven stops, each lit
with a spotlight and explained in a sentence, worded for what the app does
today. The two stops about a strategy's rules and its path to deployment show
the first strategy that is not deployed, as the mock does, and the window goes
back to the strategy it showed before. *Tour* in the top bar plays it again. The server remembers that it has
been seen, because the page's own storage is lost with each launch's port.

The window never shows the buy-and-hold comparison. The search records it and
the server leaves it out (`docs/decisions/0014`).

## Where the built screens differ from the mock

Compared side by side with `docs/prototype/index.html` at 2560x1440 and
1366x768, last on 2026-11-16, on a copy of the project with two strategies
deployed, one of them paused, and one draft, and with no practice account set:
every section, the Trades, Improve and Tests tabs and an opened test, the
running, paused and draft Dashboards, the luck-test, search and allow-trading
dialogs, stop-all switched on, and the seven tour stops. Nothing that starts
work was pressed: no Start in a job dialog, no Allow trading, no Rules are
right, no Pause, Resume, Undeploy or Check now; Deploy and the final exam were
locked. The window opens on a deployed strategy, not on whichever strategy
sorts first. The layout, type, colours and chart are the mock's. The differences
are all places where the mock shows something the project does not have:

| Screen | The mock | The window | Why |
| --- | --- | --- | --- |
| Improve | "Short" and "long" averages, "Trend filter" | `fast`, `slow`, `trend` | The names are the config's, so another strategy needs no new wording |
| Improve | A LIVE square and "Against deployed version" | Neither | Not built: the deployed version is on the Dashboard |
| Improve | "Run the luck test on this version" for any square | No such button; the luck test shows for the starred square only | The luck test runs on the version a search puts forward, not on a chosen one |
| Improve | Three figures beside the chart | Five: total return, worst fall and trades are added | They are recorded for every version |
| Improve, Ruled out | Three columns | Five: how many times recorded, and when last | The record repeats, and covers the whole project |
| Backtest | 5 years, 10 years, All history | One period, the search's | `docs/decisions/0016` |
| Backtest | "Run backtest" runs one backtest | It runs a search, the same as "Search again" | A search is the grid of backtests; the project has no single-backtest command (`docs/decisions/0017`) |
| Improve | A sped-up progress bar | A bar that moves only for a luck test, one step per finished trial | A plain search reports nothing until it ends |
| Tests | Rows for single backtests and live-against-backtest checks | Rows for searches and final exams only | Those are the tests the project keeps on record |
| Dashboard, deployed | Win rate; price and result per trade | Orders sent and, on Trades, what each filled at once the next check has read it; no result per trade | The check runs after the close, so a fill is learned a check later, and the venue's fees have an undocumented sign, so nothing nets them off |
| Dashboard, deployed | Loss today and money in the market as gauges against the limits | The loss lock and the stop-all state | The account log holds equity, not the day's loss or exposure as the risk layer worked them |
| Dashboard, deployed | Return since deployed, the strategy's | With several strategies deployed, "Its own return": its closed trades chained, before charges, with the account's return beside it | The account's equity is every strategy's together; charges carry an undocumented sign, and no price for an open trade is recorded (`docs/decisions/0023`) |
| Dashboard, deployed | 1M / 3M / All on the account chart; a Recent tests card | One range; Recent decisions instead | A few weeks of checks need no range; tests are on Tests |
| Top bar | "Stop all buying… selling still allowed" | "Stop all trading" | The risk layer cannot block buying alone, and the app may not add that to it (`docs/decisions/0019`) |
| Compare | Two deployed strategies over 90 days live | Any two strategies on their newest searches, and for a deployed one its own return from its closed trades, before charges, and how many have closed; no live chart | A few weeks of closed trades are a handful of points, and an open trade has no recorded price (`docs/decisions/0023`) |

## Opening it from the desktop

After `npm install` and one `npm start` (which builds the window), make a
StrategyLab shortcut on the desktop and in the Start menu:

```powershell
powershell -ExecutionPolicy Bypass -File desktop/scripts/make-shortcut.ps1
```

It opens the built window with the app's icon (`build/icon.svg`, rendered to
`build/icon.ico`). It does not rebuild: after pulling changes, run
`npm run build` once.

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

Needs Node 22 or later (`package.json` says so under `engines`), and the Python side set up as the top-level README
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

No test launches Electron. What `electron/main.ts` decides is in
`electron/policy.ts` and tested there: the server's arguments and environment,
the port read from its ready line (a whole number from 1 to 65535, or nothing),
the requests that carry the token, where the window may go, and which links open
in the person's browser. The wiring that acts on those answers - spawning the
server, the ready timeout, closing the server with the window, the one-instance
lock - was checked by running the app and looking, twice, on Windows. The
component tests cover the page, `findOnPath` and `policy.ts`. CI runs
none of this: the workflow is Python only.

Starting a job, closing the server and finding the job still running was
checked by hand, once, on Windows, on a copy of the project: see LIMITATIONS.
No test starts a real search.

The component tests check what a page says, not how it looks. A cell that
ignored its column span, because a class name meant two things, passed every
test and was found in a screenshot. So were, on 2026-11-16, Compare's figures
running into each other at laptop width, a table scrollbar with nothing to
scroll, and a worst fall of "−0.0%". Each screen was looked at in the real
window at 2560x1440 and at 1366x768, captured through the window's debugging
port by a script that is not part of the repository, and looked at by eye;
nothing compares a screen with the mock automatically.

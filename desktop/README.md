# StrategyLab desktop window

An Electron window onto the project. It is a client of the local server
(`strategylab serve`) and holds no strategy, cost, risk or validation logic:
everything it shows was read or computed by the Python code under `src/`.
`docs/decisions/0014` records the design, and `docs/prototype/` is the mock the
finished app has to match.

## What is here today

A shell. The window opens, starts the server, and shows:

- the top bar: a strategy dropdown, whether price data is stored and up to which
  bar, and whether a venue key is set;
- the six sections of the mock in the menu;
- a Dashboard that reports the chosen strategy's real state - its newest
  search, the candidate, whether it passed the noise gate, and how many times
  the held-back window has been read.

Trades, Tests, Backtest, Improve and Compare each say they are not built.
Nothing in the window can start a search, read the held-back window, deploy a
strategy or place an order. The tour, the charts and every control in the mock
that changes something are still to come.

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
- `src/` is the React page. `src/api.ts` describes what the server returns.
- `src/app.css` is the mock's stylesheet, copied. Change the mock first.
  `src/shell.css` holds the two places the real window differs from it.

The built page is served by the Python server, from the same origin as its API,
so no request crosses an origin and there is no CORS configuration to get wrong.

## What is not tested

No test launches Electron. `electron/main.ts` - starting the server, adding the
token, refusing to navigate away - was checked by running the app and looking,
twice, on Windows. The component tests cover the page and `findOnPath`. CI runs
none of this: the workflow is Python only.

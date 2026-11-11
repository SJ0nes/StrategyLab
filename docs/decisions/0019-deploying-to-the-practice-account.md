# 0019 — Deploying to the practice account: what may go, one at a time, and what "stop all" stops

## Context

The approved mock shows deployed strategies running on the practice account,
a dashboard of their live figures, pause and resume, and a "Stop all buying"
switch. Three questions were put to the operator on 2026-11-11 and two were
left to the assistant's recommendation when he said "continue until finished":
what the stop switch means, and whether a strategy that failed the luck test
may be deployed at all. The third - the order work is done in - he answered.

Facts that bound the answers:

- `strategylab run` already trades a search's candidate whether or not it
  passed the noise gate, and says so by name (`_candidate_or_exit`). Paper
  trading tests the simulator, not the strategy; there will never be enough
  live trades to judge a strategy (`CLAUDE.md`).
- The risk layer is untouchable. Its kill switch lives in
  `configs/risk_limits.yaml`, and no part of the app may edit that file
  (`docs/decisions/0014`).
- The live worker trades one instrument, VUAG.L, and holds one position. The
  venue reports one position for that instrument for the whole account.
- The operator chose (0015) that the risk limits apply to the account as a
  whole.

## Who made this decision

**The assistant**, on 2026-11-11, on the operator's instruction to go with its
recommendations. None of the four points below is ratified by him.

## Decision

**1. A strategy that has not passed the luck test may be deployed to the
practice account, and is labelled "Unproven" wherever it appears.** This is
what `run` already allows. The label says what the luck test found; deploying
does not change it. Deploying needs the rules confirmed for the file as it is
now, a search on that file that put a version forward, the practice account
set (`T212_ENVIRONMENT=demo` and a key), and the strategy's name typed into
the dialog. A live account is not offered.

**2. One strategy is deployed at a time.** Two strategies trading one
instrument from one account would share one position: one strategy's exit
would close the other's entry, and neither strategy's figures would be its
own. A second deployment is refused until the first is undeployed. Running
several needs a position ledger per strategy, which is later work.

**3. "Stop all trading" stops the app, not the risk layer.** It is a switch in
`state/`: while it is on, no daily check runs, so nothing is bought or sold.
It does not touch `configs/risk_limits.yaml`, and it does not stop
`strategylab run` typed at the command line. The kill switch in the risk layer
is still edited by hand; the top bar shows its state as the risk layer reads
it. The mock's "Stop all buying… selling still allowed" is not built: nothing
in the risk layer can block buying while allowing selling, and the app may not
add it there.

**4. The daily check runs only while the app is open.** Once a weekday, after
17:30 UTC - after the London close in both winter and summer time - the
server starts `strategylab run` for the deployed strategy, unless it is paused,
"stop all" is on, or a check has already run for that day. A weekday with no
check is shown as missed. Nothing runs while the app is closed, and the app
says so.

## Consequences

- An unproven strategy can place practice orders. The figures it earns are a
  test of the simulator and of the plumbing, and the app must not present them
  as evidence the strategy works.
- Compare, which the mock shows as two deployed strategies side by side, can
  only compare strategies on their recorded backtests until point 2 changes.
- A check missed because the app was closed is not made up later: the next
  check trades the next bar.

## Open

- A per-strategy position ledger, and with it several deployments.
- Whether the daily check should run from a background service when the app is
  closed.

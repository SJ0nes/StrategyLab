# 0022 — Several deployed strategies: equal shares, a ledger each, one check at a time

## Context

`docs/decisions/0019` point 2 allowed one deployed strategy at a time, because
every strategy trades the one execution instrument the protocol names (VUAG.L),
and the venue reports one net position for it. With two strategies, one
strategy's exit would sell the other's units, and an entry would be skipped
because the venue already showed a holding.

`docs/decisions/0015` left two questions open that a second strategy makes
real: who goes first when two want new exposure on the same bar, and how two
strategies holding one instrument are told apart.

## Who made this decision

**The operator**, on 2026-11-16, chose how the money is split, between two
options put to him with a worked example of each: equal shares, or first come,
first served. He chose equal shares.

**The assistant** decided everything else below, on the operator's instruction
to build several strategies at once. None of it is ratified by him.

## Decision

**1. Equal shares (the operator's).** With N strategies deployed, each may ask
for at most the account's exposure ceiling divided by N. A strategy's own
request is scaled into its share: a strategy that asks for X percent of the
account is passed to the risk layer as asking for min(X, ceiling) / N percent.
With the ceiling at 95 percent and two strategies, each full-size entry asks
for 47.5 percent. The ceiling is read from `configs/risk_limits.yaml`
(`max_open_exposure_pct`), not written into the code. N counts every deployed
strategy, paused ones included, so pausing one does not change another's size.
With one strategy deployed nothing is scaled, and a check behaves as before.

This supersedes the "first come, trimmed by the gate" reading of
`docs/decisions/0015` point 1. The gate still sees the whole account and still
trims; with equal shares it only trims when a strategy already holds more than
its share, for example one deployed alone and then joined by a second.

**2. The decision log keeps the strategy's own request.** `requested_size_pct`
is what the strategy asked for before its share was applied, because `diff`
compares it with a replay that knows nothing of shares. The scaled request is
what the gate answered, and `approved_size_pct` is the gate's answer to it; the
share used is recorded on the check's account line.

**3. A ledger per strategy, derived from the logs.** Each check's account line
names the strategy's id. A strategy holds the units its own filled orders add
up to: buys add, sells take away, read from `state/account-live.jsonl` (which
orders it sent) and `state/fills-live.jsonl` (what each filled at). Nothing new
is written to keep it, and no line is rewritten.

- A strategy enters only when its own ledger is flat, and exits by selling
  exactly the units its ledger holds.
- **A strategy with a sent order whose fill is not yet recorded sends nothing**
  until the fill is known. Its decision is still recorded.
- **Before any order, the ledgers are added up and compared with the venue's
  position.** If they differ by more than a millionth of a unit, nothing is
  sent, and the check says by how much. Units bought or sold by hand in the
  Trading 212 app cause this, and so does an order the venue never reports.

**4. Each strategy decides each bar once, in its own log.** A check for a
deployed strategy writes to `state/live/<strategy id>/decisions-live.jsonl`,
and the one-decision-per-bar guard (`docs/decisions/0021`) reads that file, so
one strategy's decision never stops another's. `strategylab run` typed without
a strategy id writes to `state/decisions-live.jsonl` and uses the venue's
position, as it always did.

**5. One check at a time, in a fixed order.** The scheduler starts the checks
due today one after another, in order of strategy id, and the app's job runner
already refuses a second job while one is running. Each strategy gets one check
a weekday. Two checks never run at once: they share the price cache and the
session file.

**6. One account, one loss lock.** The session file and the daily loss latch
stay account-wide, as `docs/decisions/0015` point 2 says.

**7. A strategy that holds units cannot be undeployed.** Its units would belong
to nobody and every later check would find the ledgers and the venue disagreeing.
It can be paused. A strategy deployed while others hold most of the ceiling is
accepted, and trades smaller, or not at all, until room is freed.

**8. Live figures with several strategies are the account's.** The dashboard's
return, Sharpe and largest fall come from the account's equity at each check,
which with several strategies is all of them together. The page says so.

## Consequences

- Each strategy trades at most 1/N of the ceiling, so a strategy's live size is
  smaller than its backtest assumed. Returns in percent are not affected by
  scale; costs with a minimum charge would be, and the protocol has none.
- Adding a strategy shrinks the others' next entries, not their open positions.
- Selling by hand in the Trading 212 app while strategies hold units stops every
  check from sending orders until the venue and the ledgers agree again.
- `docs/decisions/0019` point 2, one strategy at a time, no longer holds.

## Open

- A way to settle a disagreement between the ledgers and the venue other than
  trading by hand until they match.
- Each strategy's own return, from its fills, rather than the account's.

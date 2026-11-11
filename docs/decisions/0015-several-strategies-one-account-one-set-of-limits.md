# 0015 — Several strategies share one account, and one set of limits

## Context

The live worker runs one strategy and holds at most one position. The risk
layer's limits - the largest single request, the total exposure, the loss in a
day -
were written with that one strategy in mind, though each is stated as a
percentage of the account's equity, not of a strategy's share of it.

The app in `docs/decisions/0014` shows a dropdown of deployed strategies. Before
a second strategy can run, something has to say what the limits mean when two
strategies trade one account.

Nothing below is built. One strategy exists, it has not been deployed, and its
only candidate failed the noise gate.

## Who made this decision

**The operator**, on 2026-11-11, between two options put to him with a worked
example of each: limits that apply to each strategy alone, or to all of them
together. He chose together.

The assistant wrote down what follows from that choice. The consequences listed
under *Decision* are implications of it and are not separately ratified; the
questions under *Open* are not decided at all.

## Decision

**The limits in `configs/risk_limits.yaml` apply to the account as a whole.**

**1. Exposure is counted across every strategy.** With a ceiling of 95 percent,
two strategies that each ask for 60 percent do not both get it: whatever the
first is allowed, the second is offered what remains, here 35. The alternative -
each strategy holding its own 95 - asks for 120 percent of an account, which a
venue either refuses or lends against, and this project does not borrow.

**2. The daily loss is the account's loss.** It is measured, as now, against
equity at the start of the session. When it reaches the limit, no strategy may
open new exposure for the rest of the session, including one that lost nothing
that day. The latch in `docs/decisions/0013` is one latch, not one per strategy.

**3. The kill switch stops every strategy, exits included.** It already refuses
every intent while it is set - one that would close a position as much as one
that would open it. With several strategies it refuses all of theirs. That is
what the switch is, and this record does not soften it.

**4. Nothing short of the kill switch blocks an exit.** The loss lock and the
exposure ceiling refuse new exposure only, as they do now, so one strategy's bad
day never traps another in a position.

**5. The risk layer stays one file with its own tests, called before every
order.** It is handed the whole account, as it is today, and is not given a
per-strategy mode. A strategy's own size request is still its own; the gate
trims it.

## What this costs

**One strategy can starve another.** If the first to act takes most of the
ceiling, the second trades smaller than its backtest assumed or not at all. Its
live results will then differ from its replay for a reason that is not the
simulator's fault, and `diff` has to be able to say so - a trimmed size is
already recorded as a risk verdict, and the comparison already leaves the risk
fields out.

**A backtest of one strategy no longer describes how it will trade.** Each
strategy is searched and validated alone, as if it had the account to itself.
Run beside others it gets less. Nothing in this project measures the combined
behaviour, and a set of strategies that each passed alone has not been shown to
be acceptable together.

**The loss lock is blunter.** A bad day in one strategy stops buying in all of
them. That is the conservative direction and it is the one chosen.

## Open

These are not decided, and each needs an answer before the code that depends on
it is written.

- **Who goes first.** When two strategies want new exposure on the same bar,
  some rule orders them. It must be fixed and recorded, not left to whichever
  process the operating system ran first, or the same day replayed twice gives
  two different accounts.
- **Whether a strategy that has not passed the noise gate may be deployed.**
  The approved mock locks deployment behind the gate. By that rule nothing is
  deployable today, because the only candidate failed. Either the practice
  account is allowed to run an unproven strategy under a label that says so, or
  the dashboard stays empty until something passes.
- **What the app's "stop all buying" control is.** The approved mock says it
  stops buying and leaves selling allowed. The kill switch does not do that: it
  refuses exits too. Either the control is the kill switch and the mock's
  wording is wrong, or it is a new, milder switch that the risk layer does not
  have. `docs/prototype/README.md` maps the control to the kill switch, which is
  the first reading; the operator has not been asked.
- **Positions held in the same instrument by two strategies.** A venue reports
  one net position per instrument. Two strategies trading one ticker cannot be
  told apart from the venue's side, so the project would have to keep its own
  ledger and reconcile it.

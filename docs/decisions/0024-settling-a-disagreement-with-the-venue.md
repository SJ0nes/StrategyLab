# 0024 — Settling a disagreement between the strategies' units and the venue's

## Context

`docs/decisions/0022` gives each deployed strategy the units its own fills add
up to, and stops every daily check from sending an order while the strategies'
units together differ from the venue's position. Units bought or sold by hand in
the Trading 212 app therefore stop every check. 0022 listed a way to settle such
a difference, other than trading by hand until the two match, as open.

## Who made this decision

**The operator**, on 2026-11-17, chose how a difference is settled, between three
options put to him with a worked example of each: he names the strategy the
difference belongs to; the difference becomes units owned by no strategy, to be
traded away by a check run by hand; or nothing changes and he trades by hand
until the two match. He chose to name the owner.

**The assistant** decided everything else below. None of it is ratified by the
operator.

## Decision

**1. The operator names the owner (the operator's).** On the Dashboard of a
deployed strategy, the operator gives it the whole difference by typing its
name. A line is appended to `state/corrections-live.jsonl` and the ledger
(`live/ledger.py`) adds it to that strategy's units. No order is sent, and no
earlier line of any log changes. The operator's example: the records say a
strategy holds 10 units, Trading 212 holds 8 because 2 were sold by hand; a
correction of -2 leaves it 8, the checks resume, and its next exit sells 8.

**2. Every reader counts corrections.** `read_ledger` takes the corrections log
as a required argument, so the daily check, undeploying and the settlement
itself cannot count units without it. A damaged correction line stops them, as
a damaged fill does.

**3. The venue's position is the newest check's.** Each check records the
venue's units in the account log. The settlement reads that line and makes no
call to the venue. It is offered only when that line is newer than every fill
and correction recorded, no order waits for its fill, and no job is running;
otherwise the ledger may already count something the recorded position does
not, and the window says to run a check first.

**4. The difference is worked out again when it is settled.** The request
carries the difference the window showed, and the server refuses it if the one
it finds now differs, so a second press finds nothing to settle. The whole
difference goes to one deployed strategy; one that would be left holding fewer
than zero units is refused. No split between strategies is built.

**5. A strategy's own return survives a correction.** A correction that takes
units from an open trade leaves the trade open with fewer units, as a price
ratio does not depend on how many. A trade that a correction opens, adds units
to, or closes has no price for some of its units, so it is left out of the
return and counted; the trades after it still count (`docs/decisions/0023`).

## Consequences

- Not settled by this: a strategy left holding fewer than zero units (the
  window says to trade in the venue's own app), units owned by no strategy from
  a check run by hand, and an order whose fill is never reported.
- A correction is only as right as the operator's choice of owner. The line
  records both totals and the check it settled, so a wrong one can be found;
  it is undone by another correction, not by editing the log.

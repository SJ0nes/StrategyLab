# 0023 — Each strategy's own return, from its own closed trades

## Context

`docs/decisions/0022` point 8 made the dashboard's live figures the account's:
with several strategies deployed, each check records the account's equity,
which is all of them together, so the return, Sharpe and largest fall say
nothing about any one strategy. 0022 listed "each strategy's own return, from
its fills" as open.

Each strategy's fills are already known: the account log says which strategy
sent each order, and the fills log says what each order filled at
(`src/strategylab/live/ledger.py`).

## Who made this decision

**The operator**, on 2026-11-17, chose how the return is worked out, between
three options put to him with a worked example of each: per trade, chained; the
profit on the strategy's share of the account; or money only, with no
percentage. He chose per trade.

**The assistant** decided everything else below. None of it is ratified by the
operator.

## Decision

**1. Per trade, chained (the operator's).** A trade is a filled buy and the
filled sell that closes it. Its return is the sell's price over the buy's, less
one, and the trades are chained: (1 + r1) x (1 + r2) x ... - 1. The figure does
not depend on how large the strategy's share of the account is, so it can be
read beside the backtest's. Both prices are the same instrument's from the same
venue, so the ratio does not depend on whether the venue quotes in pounds or
pence.

**2. Before charges, with the charges listed.** The operator's option was put
to him as "after charges". The venue reports each charge with a sign its
documentation does not give and no real reply has shown
(`execution/venue.py`, `Charge`), so subtracting them would be a guess at the
sign. The return is shown before charges; every non-zero charge on a counted
fill is listed beside it, with the venue's sign, and the page says they are not
taken off. When a real reply shows the sign, this point can be revisited.

**3. Closed trades only.** No price for an open position is recorded anywhere
the project writes: the account log keeps the units held, not their price. An
open trade is reported as open, with what it was bought at, and adds nothing to
the return. The backtest's `trade_count` leaves an open position out for the
same reason. A price from the cached bars was not used: whether those bars and
the venue quote in the same unit has not been checked, and a hundredfold error
would print a plausible figure.

**4. One owner mapping.** Which strategy sent an order is read by
`sent_orders` in `live/ledger.py`, which the ledger also uses, so the ledger
and the return never disagree about whose an order is. Only orders sent at or
after the deployment count.

**5. No pairing is guessed.** The daily check is long-only and holds one
position, so a strategy's fills alternate. A sale before any purchase since
deploying, two purchases in a row, or a sale of a different number of units
than were bought gives no return, with the reason.

**6. Shown first when several strategies are deployed.** The Dashboard's first
card becomes "Its own return", with the closed-trade count and the account's
return beside it. Sharpe, the largest fall and the position stay the account's,
and the note above the cards says so. With one strategy the account's return is
that strategy's, and the card is unchanged.

## Consequences

- A strategy that has not closed a trade shows no return, however long it has
  held, until it sells.
- Compare does not show live figures yet; a strategy's own return is what it
  would compare.

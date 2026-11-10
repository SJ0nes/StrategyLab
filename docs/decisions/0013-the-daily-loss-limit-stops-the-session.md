# 0013 — The daily-loss limit stops the session, and the worker is what remembers

## Context

`docs/decisions/0010` fixed what the risk gate refuses. It left the gate a pure
function of the intent, the account and the limits, and recorded the
consequence: the daily-loss limit did not latch. A worker refused at a loss of
three percent whose equity then recovered was permitted a sized intent on the
very next call. `LIMITATIONS.md` listed it, and the module said in its own
docstring that "stops for the day" had been written in four places and was wrong
in all four.

There was a second gap behind the first. The gate looks at the loss only when
an intent carries a size. A limit reached on a bar where the strategy said
"hold" was never seen at all.

## Who made this decision

The assistant, under the operator's standing instruction of 2026-11-10 to
finish the project without asking. It changes the risk layer, which `CLAUDE.md`
says no search, strategy config or review may modify. This is none of those: it
is a code change with its own tests, in the risk layer's own file. It has not
been ratified by the operator and reverting one commit removes it.

## Decision

**1. The gate stays pure, and honours a flag it is handed.**
`PortfolioState.daily_loss_tripped` refuses any intent carrying a size, however
far the account has recovered. The gate still holds no state of its own, so it
is still testable as a function and still gives the same answer for the same
arguments.

**2. The worker asks on every run, and remembers.** `daily_loss_reached` puts
the gate's own two routes and its own inclusive comparison behind a question
that needs no intent. The worker asks it before consulting the strategy, sets
the latch if the answer is yes, and writes it to `state/session.json` before
anything else happens - so a run that dies between there and the order does not
forget it.

**3. It lasts a session, not longer.** A new day starts a new session with a
new open and no latch. The limit is a daily one; a loss yesterday is not a
reason to refuse today.

**4. An exit still passes.** The latch is a limit on new exposure, like the
limit it remembers. An intent asking for no size is allowed, because blocking
the exit would forbid the one action that stops the loss being measured.

**5. The kill switch still comes first** and is still the only thing that
refuses an exit.

**6. A caller that never sets the flag gets the old behaviour.** The field
defaults to false. Nothing in the gate can tell a caller that looked and found
nothing from one that never looked, which is the same limit as `as_of` being
unread.

## Consequences

**The latch is only as durable as the session file.** A file that cannot be
read starts a new session at current equity, which has always reset the
denominator and now resets the latch with it. Deleting `state/session.json`
during a session clears the latch too, and nothing detects it. One case fails
the other way: a file that reads but whose latch is not a boolean is taken as
tripped, because a latch that might be set is safer read as set. The first
version read it as an unreadable file, which cleared it, and a review caught
that before it was committed.

**The realised route is still never exercised live.** The worker reports a
realised profit and loss of zero, because the venue gives a lifetime figure and
no ledger differences it. So in live use the latch trips on the mark-down from
the session open, and on nothing else.

**The first run of a day still defines its open.** A loss taken before the
worker's first run is invisible to the limit and therefore to the latch.

**The wiring in `strategylab run` is untested.** The latch is set and saved in
the command, past the point where the command talks to the venue, and no test
goes there. `tests/test_worker.py` covers the pieces and the order they are used
in; it does not cover the command using them.

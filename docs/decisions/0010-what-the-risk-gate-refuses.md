# 0010 — What the risk gate refuses: a size, not an action, and a switch that does not unwind

## Context

`risk/limits.py` was a signature and a docstring contract. The contract fixed the
four limits, their evaluation order, that a limit may reduce a size rather than
reject, that the kill switch and the daily loss limit always reject, and that the
kill switch rejects "every intent regardless of any other limit".

It left two questions open, and both decide whether a real position can be closed.
Neither is visible from the outside: a gate making the opposite choice on either
one answers just as plausibly on every intent a backtest would ever hand it, and
the difference shows up only on a losing day with a position open. That is the
condition under which this project writes a decision record rather than a comment.

A third question — what a zero limit means — turned out to have no good answer and
is recorded here because refusing it is a decision too.

## Decision

**1. The exemption for a risk-reducing intent is keyed on the SIZE requested, not
on the action.**

An intent asking for no new exposure is allowed at zero, and no limit expressed as
a percentage of equity is consulted. `HOLD` and `EXIT_LONG` both carry
`size_pct` of zero, so both pass.

The alternative — exempting `EXIT_LONG` and `HOLD` by action — was rejected
because `Intent.size_pct` being zero for those two actions is a contract on
whoever builds an intent and nothing enforces it. `Intent` has no
`__post_init__`; a `HOLD` carrying fifty percent is constructible today. Keying on
the action would wave that through on the strength of its name. Keying on the size
means a malformed intent meets every limit, which is the safe direction.

The consequence is a claim this project has to state carefully, and initially
stated wrongly in three places. **A well-formed exit is never blocked by a loss
or exposure limit; an exit is not.** An `EXIT_LONG` carrying a non-zero size is
refused by a reached daily-loss limit exactly like an entry, and the reason
written into the decision log must not say "an exit is still permitted", because
on that path it has just refused one.

Why the exemption exists at all: a daily-loss limit that blocked a
position-closing intent would forbid the one action that stops the loss it is
measuring.

**2. The kill switch refuses a risk-reducing intent too. It stops the wire; it
does not flatten the account.**

This follows the contract's "every intent regardless of any other limit" rather
than softening it, and the consequence is deliberate: while the switch is set, an
open position cannot be closed through this gate, and unwinding it is a manual act
at the venue.

The alternative — a kill switch that blocks entries and permits exits — is a
defensible design and was rejected for what the control means. A human throwing a
switch on an automated system is saying *stop sending orders*, not *liquidate on
my behalf*. A switch that unwinds is a second trading policy hiding inside a
safety control, and it would execute at whatever price the market offers at the
moment somebody panics.

Two costs are accepted, and both are written in `LIMITATIONS.md` rather than left
for a reader to discover:

- The position is exposed for as long as the switch is set. This is the reason the
  switch is documented as set by a human and cleared by a human: it is not a
  circuit breaker, and nothing should ever set it automatically.
- While it is set, `risk_verdict` stops distinguishing "refused" from "nothing was
  asked for", because a `HOLD` records as a rejection. A log reader separates them
  by the `action` field on the same record.

**3. A limit of zero is refused at configuration time, and not because it would
refuse everything.**

That was the first justification written for it and it is false for two of the
three fields. Measured, with the guard bypassed:

| limit set to zero | what a sized entry gets |
|---|---|
| `max_position_pct` | **ALLOW at a size of 0.0** — an approved order for nothing |
| `max_open_exposure_pct` | REJECT, on every day |
| `max_daily_loss_pct` | REJECT on a flat or losing day, ALLOW on a winning one |

So zero means three different useless things. It is refused because none of them
is a policy anybody wants, and because a config that wants everything refused
should say `kill_switch: true`, which is a line an operator can find. The
position-cap row is the one that matters: an ALLOW at a permitted size of zero
would contradict the guarantee that an allowance of zero belongs only to a request
for zero, so the type-level refusal of a zero limit is what makes that guarantee
true. The two are one guarantee and neither is safe alone.

## The range is an invariant of the type, not a precondition on callers

`load_limits` validated the file and `check` trusted whatever `RiskLimits` it was
handed. That was enough for a file and not enough for the type: measured before
`RiskLimits.__post_init__` existed, `RiskLimits(max_position_pct=-5.0)` against a
healthy account returned `ALLOW` with `limited_size_pct=-5.0` — an approved order
for minus five percent of equity, which `logging/decisions.py` accepts as a finite
number and would have written into an append-only log. `-math.inf` returned
`ALLOW` at `-inf`, which that module refuses at the point of *writing* rather than
of deciding. A `nan` limit never binds at all, because every comparison against it
is False.

`protocol.Windows.__post_init__` made the same call for the same reason, in its
own words: "a claim that depends on every constructor being called carefully is
not a guarantee. Here it is an invariant of the type instead."

`kill_switch` lost its `= False` default in the same change. The one omission the
type made cheap was the permissive one, in the module whose whole subject is
refusing things.

## Consequences

- The gate is honest about being bypassable. `check` takes a `RiskLimits` and
  never calls `load_limits`, so file separation stops a *search* editing a limit
  and does not stop a *caller* ignoring the file. That is a convention backed by
  one obvious place to go, not a mechanism — the same sentence `LIMITATIONS.md`
  already had to write about the window accessor.
- Nothing re-reads the file, so a human clearing the kill switch mid-session has
  no effect until whatever holds the object reads it again. The first live worker
  owns that obligation and inherits no help with it.
- One input raises rather than answering: a `start_of_day_equity` that is not
  positive and finite. It is the denominator of the daily-loss limit and nothing
  can tell whether such an account is down three percent or thirty. The check
  precedes even the kill switch, so it fires with the switch set.
- Everything above was decided against tests and nothing else. `strategylab run`
  is a stub and the backtester deliberately does not gate, so no intent has been
  through this gate outside `tests/test_risk.py`.

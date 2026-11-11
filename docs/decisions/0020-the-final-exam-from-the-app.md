# 0020 — The final exam from the app, and what Compare compares

## Context

The operator chose full control from the app, with permanent actions behind a
typed confirmation (`docs/decisions/0014`), and said to build the final-exam
button. The final exam is `strategylab validate`: it reads the held-back years
once, adds one to `access_log.walk_forward_touches` in `protocol.yaml`, and
that read cannot be taken back. `validate` already refuses a candidate whose
search did not pass the noise gate.

The mock's Compare page sets two deployed strategies' live results side by
side. Only one strategy may be deployed (`docs/decisions/0019`).

## Who made this decision

**The assistant**, on 2026-11-11. The operator asked for the button; he was not
asked about anything below and has not ratified it.

## Decision

**1. The final exam is a job kind of its own, started only through its own
route, with the phrase `SPEND ONE LOOK` typed.** Not the strategy's name, which
is what deploying asks for: the habit of one must not do the other. The dialog
shows how many looks have been used and that this one is permanent. It cannot
be stopped once started.

**2. It is locked unless the newest readable search on the strategy file as it
is now passed the luck test.** The job records that search, and runs
`strategylab validate state/<that search>`. `validate` checks the gate again.

**3. It is refused in a git worktree.** A worktree carries its own copy of
`protocol.yaml`, and a look spent there never reaches the count on the main
checkout (`CLAUDE.md`). A directory with no `.git` at all - a copy made to try
the app on - is not refused; a look spent there is spent in that copy.

**4. No test, and no check by the assistant, runs it.** Its tests start a
stand-in that records the command. The assistant never takes the final exam on
the operator's repository.

**5. Compare compares strategies on their newest searches, not on live
results.** The version each search put forward, side by side: score, return,
worst fall, trades, luck test. A better score on old prices is marked, and the
page says that alone is often luck.

## Consequences

- One button in the app now spends something that cannot be recovered. It is
  behind a passed luck test and a typed phrase, and nothing else.
- Compare shows nothing until there are two strategies.

## Open

- Whether a look spent in a copy of the project should be refused too.

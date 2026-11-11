# 0017 — What the app's buttons start, and how a job outlives the window

## Context

The approved mock (`docs/prototype/`) has four buttons that start work:
"Search again" and "Run the luck test on this version" on Improve, "Run
backtest" on Backtest, and "Take final exam" on Tests. The project has two
commands that do research work: `search`, with or without `--trials`, and
`validate`.

A plain search of the committed grid takes minutes. A search with a
hundred-trial noise ceiling took 49 minutes on fourteen processes on the
operator's machine, once. Neither fits inside one HTTP request, and the second
is longer than anyone keeps a window open on purpose.

This record covers the first three buttons. The final exam is `validate`, which
spends a held-back read, and is deliberately left for its own record and its own
phase (`docs/decisions/0014`).

## Who made this decision

**The assistant**, on 2026-11-11. The operator approved the mock and asked for
every command to have a button. He was not asked about the mapping below and
has not ratified it.

## Decision

**1. Each button starts a command that already exists. The app adds no new kind
of research.**

| Button | What it starts |
| --- | --- |
| Search again (Improve) | `search <config>` with no trials |
| Run backtest (Backtest) | The same. A search is the grid of backtests, and the page shows the replay of the version it puts forward |
| Run the luck test (Improve) | `search <config> --trials 100` |

**2. There is no luck test "on this version".** The mock offers one for any
square of the grid. The noise gate compares the version a search put forward
against what the same search finds on resampled prices; the version is chosen
by plateau score, with edge winners and drawdown breaches turned away first. A
luck test on a hand-picked square would skip that selection, and picking the
square after seeing the grid is the selection the gate exists to price. The
button is one per search, not one per square.

**3. The luck test runs the number of trials the gate needs to decide, and the
app offers no other number.** Fewer cannot pass or fail the gate
(`docs/decisions/0012`), and a button that runs for most of an hour and then
says "not decided" is a trap.

**4. A job is a child process, and the app starts one at a time.** The graveyard
is not safe for two writers, and two results started in the same second would
take the same file name. A second request while one runs is refused, with the
running job named.

**5. The job outlives the window.** It is started detached from the server, with
its output going to a file under `state/jobs/`. Closing the window stops the
server and not the job. On the next launch the app reads the job's record and
its log and shows where it has got to.

**6. What a job may be is a closed list in the server.** A request names a kind
from that list and a strategy id; the server looks the id up in the registry and
builds the command itself. No part of a command line is taken from a request.
`validate` is not in the list.

**7. Stopping a job is asked for, not forced.** The app writes a file the search
looks for between trials, and the search stops at the next one without writing a
result. A plain search has no trials and cannot be stopped this way; it takes
minutes, and is left to finish.

## Consequences

- "Run backtest" and "Search again" do the same thing. That is honest about what
  the project has, and it reads as duplication.
- The luck test's duration cannot be chosen. On a slower machine it is slower.
- A job started and then forgotten keeps the machine busy with nothing on screen
  until the app is opened again.
- A stopped luck test leaves nothing behind but its log: no partial result is
  written, because a ceiling from fewer trials than asked for is a different
  measurement.
- Whether a job is running is read from a heartbeat file and not from the
  process. A search whose carrier process dies runs on unseen.
- Progress is read from the job's log, so the log's wording becomes something
  code depends on. One function writes the line and one reads it, tested
  together.

## What was checked

On a copy of the project, on Windows, once each: a search started from the
real window survived the window closing, finished, and was shown when the
window was reopened; a luck test asked to stop after three of a hundred trials
ended as stopped, with no result, 11 minutes 40 seconds later, on a machine
doing other work at the time. A Python file planted in the copy was not
imported by the job. LIMITATIONS lists what this does not show.

## Open

- Whether a job should also be visible to, or stoppable from, the command line.
- Whether a finished job should raise a notification when the window is closed.

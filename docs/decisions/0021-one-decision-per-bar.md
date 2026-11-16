# 0021 — One live decision per bar, instead of an exchange calendar

## Context

The scheduler starts the daily check on every weekday after 17:30 UTC
(`docs/decisions/0019`). It has no exchange calendar, so on a London bank
holiday the check still runs. The newest bar it can fetch is then the previous
session's, which the previous check already decided on. Until now the check
decided that bar a second time and wrote a second live record for it; the
one-position rule kept a repeated entry from buying twice, but a decision was
still being made on nothing new. The same happens on a normal weekday if the
price vendor has not yet published the day's bar.

Three ways to stop it were weighed:

- **A calendar library** (`holidays`, `exchange_calendars`). It is a new
  dependency, and `CLAUDE.md` says to ask before adding one.
- **A list of holiday dates written into the code.** It goes stale every year,
  and one-off closures, such as a state funeral, cannot be worked out ahead of
  time.
- **The bar itself as the evidence.** If the newest bar is one the live log
  has already decided, no session has happened since, whatever the reason.

## Who made this decision

**The assistant**, on 2026-11-16, under the operator's instruction to build its
recommendations. The operator was not asked about the choice below and has not
ratified it.

## Decision

`strategylab run` decides at most once per bar. After fetching prices and
reading the account, and after recording any fills, it looks in
`state/decisions-live.jsonl` for a live record of the newest bar
(`live.worker.already_decided`). If there is one, it says that no new bar has
arrived and when that bar was decided, and ends with exit code 0, having written
no decision, no account line and sent no order. A decision log that cannot be
read stops the check with exit code 1, before anything is decided.

The scheduler is unchanged: it still starts a check on a holiday, so the day is
not shown as missed, and that check ends at once.

## Consequences

- A bank holiday, or a late price, costs one check that does nothing. If the
  vendor publishes a day's bar after a check that found none, that bar is never
  decided on its own day; the next check decides the next bar.
- `compare` still takes the last record when a bar has two, because logs
  written before this change can hold two.
- The day still gets no account line, so the live account chart has no point
  for a holiday. That is the truth: nothing was checked.
- No new dependency and nothing to keep up to date.

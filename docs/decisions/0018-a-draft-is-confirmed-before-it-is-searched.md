# 0018 — A draft's rules are confirmed before it is searched, and its source sits beside it

## Context

The operator asked (2026-11-11) to be able to point Claude Code at a video with
`/watch`, have it write a strategy file, and see that strategy in the app as a
draft with a path to deployment. The approved mock (`docs/prototype/`) shows
that path: read from video, you confirm the rules, backtest, luck test, deploy.

Two things the mock shows have nowhere to live in the project today. A
strategy config holds rules and a description, and its loader refuses any other
field, so there is no place for the video's link, the time in the video of each
rule, or the things the video said that the config cannot express. And nothing
records that a person has read a strategy's rules.

## Who made this decision

**The assistant**, on 2026-11-11, while building the draft page. The operator
asked for the import and approved the mock; he was not asked about anything
below and has not ratified it.

## Decision

**1. Where a strategy came from is a second file beside it,
`configs/<id>.source.json`.** The import skill writes it. It holds the link,
title and channel; each rule as the video states it, with its time; and every
thing left out, with the reason. The config format is not changed: adding
fields to it would widen the closed schema that `docs/decisions` and the
`declarative-config` skill keep closed, for data the engine never reads. The
registry lists `*.yaml` only, so the second file is never taken for a strategy.
A strategy written by hand has none.

**2. A person confirms the rules, for one version of the file.** The draft page
shows the rules as the server words them from what the loader read - not from
the file's text, and not from the video - and a *Rules are right* button. The
confirmation is recorded in `state/confirmed.json` against the config's content
hash, so any edit the loader can see undoes it.

**3. No job starts for a strategy whose rules are not confirmed for its file as
it is now.** The server refuses; the button is not the only check. That includes
the committed `ma_crossover`, which must be confirmed once before its first
search from the app. The command line is unaffected: `strategylab search` runs
whatever it is given.

**4. A failed luck test offers no second try.** The noise ceiling is seeded, so
the same file gives the same answer; a second run changes nothing but the
clock, and a run with another seed until one passes is the luck the test
measures. The page says to change the rules instead. The server does not refuse
the job - it would only repeat itself - but the page does not offer it.

## Consequences

- The committed strategy needs a click before the app can search it again.
- A confirmation lives in `state/`, which is not committed. A fresh clone, or a
  deleted `state/`, needs the rules confirmed again.
- The confirmation says a person pressed a button. It cannot say they compared
  the rules with the video.
- The source file is written by an assistant from a video. Nothing checks it
  against the video either; the page says the reading can be wrong.
- The skill has been checked only for its two commands, run by hand on a
  scratch copy. It has not been run on a real video.

## Open

- Whether drafts read from videos should be committed. The operator decides
  per draft; the skill commits nothing.
- Whether the indicator vocabulary should grow. Each addition is a code change
  in `core/` with its tests, made when a video the operator wants needs it.

# 0006 — What the read-only audits actually catch, and how often to run them

## Context

`.claude/hooks/audit-dispatch.ps1` blocks the end of any turn that changed
`src/`, `tests/`, `configs/`, `protocol.yaml` or `pyproject.toml` until four
read-only auditors have run: `contract-auditor`, `arithmetic-checker`,
`drift-checker` and `docs-auditor`. Each writes its raw reply to `state/audit/`.

That gate has now fired on every component this project has built. The evidence
of what it found is in `git log`: each round is followed by a `fix:` commit whose
message names what was wrong. Read those messages rather than trusting the
summary below — the audit reports themselves are overwritten each round and
`state/` is gitignored, so the commit history is the only durable record.

A pattern is visible in that history and is worth acting on.

## The pattern

**The arithmetic survives. The prose does not.**

Every time the `arithmetic-checker` has independently recomputed a hand-derived
expected value in this repository, the value has been correct. Not once has it
found a wrong number, a sign error or a unit mismatch.

Every round has found something untrue in what was written *about* the code:

- a test described as covering lookahead that could not fail at all
- a docstring claiming a helper made a wrong-column read detectable when a
  constant offset cancelled it exactly
- a claim that one expression was "the whole" of the lookahead guarantee when
  three reads could reach the future
- superseded paragraphs left standing beside the paragraphs that replaced them,
  twice, in the same file
- figures quoted from a mutation harness that was never committed
- a hundredfold error inside a paragraph warning about hundredfold errors

The errors are not in the verification. They are in the claims about how good
the verification is, and they run in one direction: overstating.

## What that means for the audits

The two findings the audits produced that nothing else could have are worth
naming, because they define what these agents are for.

**Mutation testing cannot validate an expected value.** It proves a test notices
when the implementation changes. If a target value is derived wrongly and the
code is written to agree with it, every mutation is still caught and the number
is still wrong. Only an independent derivation closes that, which is why
`arithmetic-checker` is forbidden from reading `src/`.

**Mutation testing cannot see a case the suite never exercises.** Every
hand-derived Sharpe used two observations, and at `n = 2` the divisor `n - 1`
*is* 1 — so "divide by `n-1`" and "divide by the literal 1" are the same code
path and mutating one into the other changes nothing to detect. The hole was
invisible to the technique that was supposed to be checking it.

## Decision

**Keep the gate. Vary what it dispatches, by what changed.**

- **`arithmetic-checker` on any commit introducing a hand-derived number.** This
  is the one that keeps paying, for the two reasons above. It is cheap to run
  alone and it has never been wasted.
- **`docs-auditor` before a push rather than per commit.** Its findings are
  cumulative prose drift. Catching them within the hour of writing has no value
  over catching them before they become public.
- **`contract-auditor` and `drift-checker` on new modules, not on corrections.**
  On a commit that only fixes previous findings they mostly re-verify counts
  already checked against the test collector.

A full four-agent round on a correction commit has consistently returned
wording. A full round on a new component has consistently returned defects.

## Consequences

The hook is NOT changed to enforce this. It still asks for all four, and the
operator or the session decides. Encoding the cadence in the hook would make a
judgement call look like a rule, and the judgement depends on what the change-set
contains — which the hook cannot see.

What the hook does now carry is a pointer to this record, so the decision is one
file away from the moment it has to be made.

**One thing no auditor can check.** These agents audit the repository. They do
not audit the summary of their findings that reaches the operator, and at least
once in this project an auditor's claim was relayed as established and turned out
not to reproduce when tested. The weakest link is the layer between the reports
and the reader. The reports on disk are the record; a summary of them is not.

---

## Evidence from the `search/` sessions (added 2026-08-22)

The Decision above is unchanged. What follows is data it did not have, recorded
because the next session faces the same cadence choice and this record is what it
will read. Whether the Decision should change is not settled here.

**The claim "the arithmetic survives, the prose does not" is now half wrong.**
Across `expand_grid`, `plateau.py` and `graveyard.py` the arithmetic half held
perfectly: every hand-derived number the `arithmetic-checker` recomputed was
correct, again, including four axis enumerations, a four-value warm-up set and
two threshold brackets. But the audits also found real DEFECTS IN CODE, which
this record said they had not:

- lookbacks of `0` and `-10` expanded silently, because a documented at-least-one-bar
  floor was claimed and not implemented;
- a `TypeError` escaped a contract admitting only `ValueError`, twice, in two
  different modules;
- an error message replaced itself with one about integer digit limits;
- `is_edge_winner` skipped its own documented refusal for every parameter after
  the first one found on an edge, so the same malformed cell raised or did not
  according to parameter order;
- a graveyard refusal created the file, then raised, leaving a zero-byte file
  that a reader counts as a search with no rejections in it;
- a `NaN` fingerprint inverted `is_degenerate`'s answer for a one-cell
  neighbourhood.

**The failure mode that actually recurred was counting and claiming**, not
arithmetic: stale test counts in four documents, a miscounted dataclass, a wrong
span in a paragraph about checkable timestamps, two false statements about what
is untested, and a scope claim that put a later roadmap item inside an earlier
one. These are prose failures, as the record predicted — but they are prose
ABOUT COUNTS, and the cheapest guard against them is `drift-checker`, which
recomputes rather than reads.

**The convergence signal is real and worth naming.** Four rounds on `expand_grid`
went: silent wrong behaviour, silent wrong behaviour, docstring precision,
docstring precision. Rounds three and four found nothing that produced a wrong
grid. That shape — behavioural findings first, precision findings after — is a
better stopping rule than a fixed number of rounds.

**Two findings did not reproduce.** One compared a claim against `.claude/setup.md`,
which is the prompt that generated a stub rather than the stub itself; `git show`
settled it. Both were recorded as not-reproduced rather than fixed. This is the
"one thing no auditor can check" above, arriving exactly as described, and the
only defence that worked was checking before relaying.

---

## Addendum, 2026-08-22: six rounds on `search/loop.py`

The search loop was the hardest test this record has had. It is recorded here
because it both confirms the thesis above and complicates it.

**Confirmed, again and emphatically: the arithmetic never broke.** Across six
rounds `arithmetic-checker` recomputed every hand-derived value in
`tests/test_search_loop.py` — plateau medians, grid sizes, warm-ups, bar counts
— and disagreed with none of them. Every defect found in six rounds was code or
prose.

**Complicated: `contract-auditor` on a CORRECTION commit found behavioural
defects, not wording, four rounds running.** The record above says a full round
on a correction returns wording. That did not hold here, and the reason is
worth knowing: each correction ADDED GUARD LOGIC, and new guard logic is new
behaviour whatever the commit prefix says. The cadence rule should be read as
"audit new behaviour", not "audit new modules" — a `fix:` commit that adds a
check is a new-behaviour commit.

**The convergence signal from `expand_grid` did not appear.** Rounds went:
behavioural, behavioural, behavioural, behavioural, behavioural, behavioural.
The loop did not converge, because each round's fix created the next round's
surface. Six rounds in, the findings were still real — the last one caught a
check that was STRICTER than the engine it guarded and would have refused valid
searches. What stopped it was a judgement that continuing is the operator's
call, not a signal from the findings.

**Three defects were self-inflicted by fixing.** A window guard that read the
held-back window while validating it; a value check stricter than the engine; a
justification comment asserting a hole that measurement showed did not exist.
All three arrived in code written to make the component safer. That is the
sharpest lesson here: the fix is as likely to carry the defect as the original.

**Two findings did not reproduce, and one was actively wrong.** An auditor
reported that a numeric check refused `int64`, `float32` and nullable columns;
testing showed it accepted all three. Another reported a defect already fixed
while it was running. Both were recorded as such rather than acted on. The
defence remains the one named above: check before relaying.

**Two MEASUREMENT HARNESSES were themselves broken**, which is a failure mode
this record had not previously named. A mutation checker grepped pytest output
for `1 passed` while `addopts = "-q"` plus an explicit `-q` suppressed the
summary line, so it reported every mutation as caught. Its replacement used a
byte diff to detect whether a mutation applied, and `sed -i` rewrites CRLF to
LF, so a pattern that never matched still "changed" the file. Both harnesses
produced confident, wrong results, and neither was the code under test. A
harness needs positive confirmation that it did what it claims — grep for the
mutated text, not for the absence of a diff.

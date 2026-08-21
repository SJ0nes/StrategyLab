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

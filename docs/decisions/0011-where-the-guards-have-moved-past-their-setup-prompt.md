# 0011 — Where the guards have moved past their setup prompt

## Context

`.claude/guards.md` is the prompt that installed this repository's hooks, its
permission allowlist, its four auditors and its four skills. It is kept exactly
as it was run, because it is the record of what was first installed.

The guards have changed since. `drift-checker` compares a prompt against the
repository, so each change reads to it as drift, and a reader who lays
`guards.md` beside `.claude/settings.json` finds the same differences with no
explanation. This record lists them, so that each is a decision someone can
disagree with rather than a discrepancy someone has to rediscover.

## Decision

**`guards.md` is not edited to match.** Rewriting a prompt after the fact makes
it describe a session that never happened. The differences live here instead.

**1. `uv add` is no longer auto-approved.** The prompt's allowlist includes it;
`CLAUDE.md` says to ask before adding a dependency, naming what it does and what
it replaces. An allowlist entry answers that question before it is asked. Both
entries, `Bash` and `PowerShell`, are gone. `uv sync` and `uv init` remain: the
first installs what the lock file already names, and the second adds nothing.

**2. Every `Bash` rule has a `PowerShell` twin, and the shell hooks match both
tools.** The prompt was written for one shell. On Windows the agent has two, and
a guard on one of them is a guard with a door beside it.

**3. There is a seventh hook: `check_secrets.py`.** The prompt's commit gate
proves that `.gitignore` names `.env` and reads nothing else. The new hook reads
what a commit would write: staged files, modified tracked files, and untracked
files when the command also adds. It is Python where the other six are
PowerShell, because it has to parse a shell command and the parsing has tests,
`tests/test_check_secrets.py`, which no PowerShell hook has.

**4. Each hook finds its script through a fallback.** The prompt's commands name
their script through `CLAUDE_PROJECT_DIR`. A session started in another directory
and moved into this one keeps the old value, the script is not found, the command
exits 127, and Claude Code ignores every exit code except 2. All six guards
stopped running that way without a word, which is how it was found. Each command
now falls back to the repository that contains the working directory.

**5. The auditors write their own report.** The prompt gives them read, grep and
glob and nothing else. They now hold `Write`, restricted by instruction to one
path under `state/audit/`, because a report retyped by the session that
dispatched the auditor is a summary of a summary. `docs-auditor` also holds
`Bash`, to run the quickstart rather than read it.

**6. An auditor quotes the line, and reads this directory first.** A finding
without a quoted file and line is a lead. A finding that only disputes a
recorded decision is dropped; code that no longer does what a record says is
still reported.

**7. The pre-push audit suppresses known harmless shapes, and counts them.** The
prompt asks for a scan for account-number-shaped strings. Run literally, that
reports every ISO date in the repository and can never come back clean, and a
gate that cannot go clean gets scrolled past. Each suppression is a named shape
in the hook with the reason beside it, and the report prints how many matches
were suppressed. The credential scan exempts no matched shape. It does skip four whole files - the two rulebooks and the two hook scripts, which hold the patterns themselves - and prints them as skipped. Two were
tried for a line that reads a key from the environment, and review showed each
also hid a real key - one given as a fallback, one left in a trailing comment.
The line was renamed at the source instead.

## Consequences

**The fallback in 4 has a cost that was reviewed and accepted.** If the
session's working directory is some other repository that carries a script at
the same path, that repository's script is what runs. The alternative is guards
that silently do not run in a session that changed directory, which is the
failure this project already had.

**The credential scan fails closed and the PowerShell guards do not.** If
`check_secrets.py` is present and Python cannot run it, the command is blocked.
A missing PowerShell still exits 127 and lets the action through, because three
of those guards are `Stop` hooks, and a `Stop` hook that blocks on its own fault
never lets a turn end.

**`check_secrets.py` runs git in the session's own repository and nowhere
else.** Git obeys the config of the repository it runs in, and `core.fsmonitor`
alone names a command it will execute, so running git in a directory is
trusting it. The hook reads the command to find where each commit runs and
where each `cd` goes. A commit aimed at any other repository, a command that
passes through one, and a destination that is not a directory on disk as
written - which is what `~` or a variable looks like before the shell expands
it - are all refused rather than scanned. Its `git diff` calls ignore
submodules, because otherwise git runs a status inside every nested repository
the index registers.

That design was reached the hard way and the history is worth keeping. The
first version followed a commit into whatever repository the command named and
scanned it there. Three review rounds each found a way to make the guard run
git somewhere the command itself never had: a repository passed through in a
subshell, one already left by `popd`, one named by `--work-tree`. Each fix
added parsing and each round found the next gap, which is the pattern
`docs/decisions/0006` describes. What ended it was removing the thing being
defended: the hook no longer looks anywhere else, so there is nothing to trick
it into. `tests/test_check_secrets.py` plants a `core.fsmonitor` command in a
second repository and asserts it never runs.

**The cost is refusals of honest commands.** The parser does not model a
subshell, a `popd` or a `cd -`, so a command that visits another repository and
comes home before committing is refused like one that commits there. A session
in this repository cannot commit into another one at all. Both cost a retry.

**It still reads a command, and a command can hide.** Anything the parser never
sees - `eval`, an alias, a variable set on an earlier line - is beyond it, and a
variable set through PowerShell's `Set-Item` gets past the refusal of `GIT_DIR`
and its relatives, which matches an equals sign. Refusing every mention of those
names was considered and rejected: a commit message that explains this very
limit would be refused for containing them. It is a guard against an accident,
not against an agent trying to get past it, which could edit the hook instead.

**The layer that does not guess is pre-commit.** `detect-secrets` and
`detect-private-key` in `.pre-commit-config.yaml` read the real index at the
real commit, in any clone where `pre-commit install` has been run. Measured on
2026-11-10 with a fabricated 31-character value: `detect-secrets` refused it
assigned to a Trading 212 key name unquoted in a `.txt` file, unquoted in a
`.yaml` file, and quoted in a `.py` file. It did not refuse the low-entropy
placeholder `abcdefghijkl` in any of them, which `check_secrets.py` does,
because it goes by the name rather than by how random the value looks.

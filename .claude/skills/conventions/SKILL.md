---
name: conventions
description: Use before writing any code, committing anything, or creating any file in this repository - the house rules on commit granularity, commit message format, type hints and docstrings, dead code, file placement, and where experimental work lives. Read this instead of inferring conventions from surrounding files.
---

# House conventions

This repository is a portfolio piece. A reviewer reads the README, the commit history, and
LIMITATIONS.md before they read any code, and the person whose name is on it must be able to
vouch for every line. Both facts drive everything below.

## Commits

**One component per commit, with its tests.** A component and the tests that prove it land
together or not at all. A commit that adds a module and a later commit that adds its tests
reads, to anyone scrolling the history, as code that shipped untested.

Never bundle unrelated changes. If you fixed a typo while implementing a feature, that is a
second commit.

**Conventional commit messages.** The prefix is not decoration; it is how the history is read
at a glance.

| Prefix  | For |
|---------|-----|
| `feat:` | New capability someone can use |
| `fix:`  | Corrected behaviour that was wrong |
| `test:` | Tests only, no behaviour change |
| `docs:` | Documentation only |
| `chore:`| Tooling, config, dependencies, housekeeping |
| `ci:`   | Pipeline and automation |

The subject line says what changed and stays under about seventy characters. If the change
needs justification, the body carries it - what the alternative was and why it was rejected is
worth more to a future reader than a restatement of the diff.

`git push` is never automated. It stays a manual act, and it is preceded by the pre-push audit
(`.claude/hooks/pre-push-audit.ps1`).

## Code

**Type hints throughout.** Every function signature, every argument, every return. Not partial
annotation; mypy is a gate, not a suggestion.

**Docstrings throughout.** Every public function, class, and module. A docstring states what
the function does, what its arguments mean *including their units*, what it returns, and what
it raises. Units are not optional in this project - a rate of `0.05` means nothing until the
docstring says whether that is five percent or five basis points, and a `contract-auditor`
finding of "unit disagreement" is a real bug caught late.

The docstring is a contract. It is audited against the implementation. Do not write a docstring
describing what you intend to implement; describe what the code does.

**No dead code.** No commented-out blocks, no `if False:`, no unreachable branches, no
functions kept "in case". Version control is the place for code that is not currently used.

**No stray artifacts.** No notebooks, nothing matching `*_temp*`, `*_old*`, or `scratch*`. The
hygiene gate blocks the turn on these, but the rule exists whether or not the gate is watching.

## Files

Nothing sits at the repository root that belongs in a subdirectory. The root holds project-level
files only: `README.md`, `LIMITATIONS.md`, `pyproject.toml`, `protocol.yaml`, `.gitignore`, and
their peers. Source lives under `src/`, tests under `tests/`, data under `data/`, generated
state under `state/`, reports under `reports/`.

`data/`, `state/`, and `reports/` are ignored by git except for their `.gitkeep`. Nothing
generated is ever committed.

Never write an absolute path into a file the repository carries. Home-directory paths - the
macOS user root, the Linux one, the Windows one under a drive letter - all embed your username,
and this repository will be read by strangers. Paths are relative to the repository root, or
derived at runtime. The hygiene gate blocks the turn on any it finds.

## Finishing a change

**Say what was left.** If part of a change turned out to be blocked, finish the rest and state
what was dropped and why. A silently narrowed change reads as a complete one.

**Write each fact where it belongs.** What is always true goes in `CLAUDE.md`. What is true
today goes in the README's *Current state*. Depth that only one kind of work needs goes in
`docs/`. `CLAUDE.md` is read on every turn, so it carries no status snapshot.

## Experiments

Anything experimental lives on a branch. `main` holds only work that is finished, tested, and
documented. An experiment that turns out well gets merged as a proper component with its tests
and a conventional commit; an experiment that does not is simply abandoned on its branch, which
is the point of having one.

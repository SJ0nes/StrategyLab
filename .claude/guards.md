You are setting up the enforcement machinery for a quantitative strategy research project. This session writes **configuration only**. It does not build the project.

You act — you never instruct. You create the files, you run the proofs, you report what you actually observed.

Hard rules for this session:
- **Create nothing outside `.claude/`, `.gitignore`, and the git repository itself.** No source, no protocol, no tests, no README. If you find yourself writing Python, stop.
- **Every guard must be proven to fire before this session ends.** A hook that looks correct and silently never fires is worse than no hook, because it will be trusted. Writing a guard is half the job; demonstrating it works is the other half.
- **Never fabricate a result.** If a proof has not run, say so. Report failures.
- **Verify paths and schema against the installed version.** Do not assume a directory layout or a frontmatter field from memory. Check `claude --version` and the local documentation before writing config, and say what you found.
- **Stop at the end of Phase 4.** Do not continue into the project build. That is a separate session with a separate prompt.

---

## PHASE 0: VERSION AND LAYOUT

Say: "Step 1 of 4: Checking versions and confirming where configuration lives."

```bash
claude --version && git --version && python3 --version
```

Confirm, against the installed version rather than assumption, the correct location and schema for:

- settings (`settings.json`) and its `permissions` and `hooks` keys
- subagent definition files and their frontmatter fields
- skill files and their directory

State plainly what you found and where these live on this machine. If any of them differ from what this prompt assumes below, follow the installed version and say so.

---

## PHASE 1: REPOSITORY FLOOR

Say: "Step 2 of 4: Git and the ignore file, before anything else exists."

```bash
git init
```

Write `.gitignore`:

```
.env
data/*
!data/.gitkeep
state/*
!state/.gitkeep
reports/
*.log
__pycache__/
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
htmlcov/
```

```bash
git check-ignore .env && echo "env ignored"
```

That must succeed before any commit is ever made in this repository.

---

## PHASE 2: HOOKS

Say: "Step 3 of 4: The hooks. These are the rules that hold when nobody is watching."

Write the settings file with a permissions allowlist and the hooks below.

**Permissions allowlist** — so the operator is not asked to approve the same commands repeatedly:

```
Bash(uv run pytest:*)
Bash(uv run ruff:*)
Bash(uv run mypy:*)
Bash(uv sync:*)
Bash(uv add:*)
Bash(uv init:*)
Bash(git add:*)
Bash(git commit:*)
Bash(git status:*)
Bash(git log:*)
Bash(git diff:*)
```

Note: `git push` is deliberately absent. It stays manual.

**Hook 1 — secrets guard.** `PreToolUse`, matching the file tools (write, edit, and read). Exit 2 when the target path matches `.env` (but not `.env.example`). Exit 2 is the code that blocks; any other non-zero exit is logged and ignored, which would let the write through while appearing to have run.

**Hook 2 — commit gate.** `PreToolUse` on Bash, matching `git commit`. Exit 2 unless `.gitignore` exists and contains `.env`. Blocks the failure mode your rules call permanent: a secret in git history.

**Hook 3 — lint and types.** `PostToolUse` on write and edit, for `*.py` under `src/` and `tests/`. Run ruff and mypy on the file. `PostToolUse` cannot undo the write — exit 2 here to surface the failure to the model so it fixes it, rather than to prevent anything.

**Hook 4 — test gate.** `Stop`. Run the test suite; exit 2 while anything fails, so the turn cannot end on a red suite. **This hook must exit 0 when `tests/` is absent or empty**, or the first session deadlocks on its first turn. Gate it on the real condition and return 0 once it clears — a `Stop` hook that always exits 2 is an infinite loop.

**Hook 5 — hygiene gate.** `Stop`. Exit 2 if any tracked file contains an absolute path with a username (`/Users/`, `/home/` followed by a name, `C:\Users\`), or if a new root-level file appears that is not on the allowed list, or if any tracked file matches `*_temp*`, `*_old*`, `scratch*`, or `*.ipynb`. This repository is a portfolio piece and the file hygiene rules must hold without supervision.

**Hook 6 — audit dispatch.** `Stop`. Invoke the read-only subagents from Phase 3 and have each write its raw output to `state/audit/<name>.txt`. Raw output to disk, not a summary into the conversation — the operator reads the files.

**Hook 7 — pre-push audit.** A command the operator runs manually, writing to `state/audit/pre-push.txt`: `git log --oneline`, `git diff --stat` against the remote, and a scan for usernames, absolute paths, and account-number-shaped strings across all tracked files. Pushing is never automated; a public git history is permanent.

### Prove every hook fires

Do not skip this. For each of hooks 1 through 5, construct the condition, run it, and report what actually happened:

```bash
# Hook 1 — must be BLOCKED
# attempt to write a file at .env

# Hook 2 — must be BLOCKED
# temporarily move .gitignore aside, attempt a commit, restore it

# Hook 3 — must SURFACE an error
# write a .py file with an unused import and a type error

# Hook 4 — must EXIT 0 (tests/ does not exist yet)
# end a turn and confirm the session is not stuck

# Hook 5 — must be BLOCKED
# attempt to write scratch_notes.py at the repository root
```

Clean up every artifact afterwards and confirm the working tree is clean.

Report each result as observed, including any that did not fire. A guard that did not fire is the finding, not an inconvenience.

---

## PHASE 3: SUBAGENTS AND SKILLS

Say: "Step 4 of 4: The auditors and the house conventions."

**Four read-only subagents.** Each gets read, grep, and glob only — no write, no edit, no bash beyond what reading requires. Each writes raw findings to `state/audit/`. Descriptions must be specific and action-oriented; a vague description means the agent is never invoked, which fails silently.

| Agent | Job |
|---|---|
| `contract-auditor` | Given a module, compare the implementation against its docstring contract. Report every discrepancy, including behaviour the contract forbids. |
| `arithmetic-checker` | Given a test file, recompute every hardcoded expected value independently and report disagreements. Do not read the implementation — that defeats the purpose. |
| `drift-checker` | Compare the session prompt against the repository. Report what the prompt claims exists versus what is on disk. |
| `docs-auditor` | Every public function has a docstring. No claim in README is unbacked by something in the repository. LIMITATIONS.md reflects what actually exists. The quickstart commands run in a clean checkout — execute them and report. |

**Three skills.** These carry the material every session needs, so no session has to re-read a long prompt to get them:

- **`conventions`** — one component per commit with its tests; conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`); type hints and docstrings throughout; no dead code, no commented-out blocks, no stray notebooks, nothing at the root that belongs in a subdirectory; anything experimental lives in a branch.
- **`declarative-config`** — strategy conditions are a flat list combined with AND, drawn from a fixed vocabulary the engine knows. No nesting, no OR, no expressions in YAML. Anything more expressive is a code change with a test, never a config feature. A config format that grows conditionals is a badly-built programming language with no debugger.
- **`cost-arithmetic`** — a fill is computed as follows, and this is authoritative:
  - The executed price moves against you by **half the spread plus slippage**. A buyer crosses half the spread, not all of it.
  - **Commission is a cash charge on the executed notional, not a price adjustment.** It is reported in the itemised breakdown and never folded into the executed price.
  - Worked example, mid 100.00, commission 0.05%, spread 0.03%, slippage 0.05%:
    - BUY → executed **100.065** (half-spread 0.015 + slippage 0.05); commission **0.0500** charged separately
    - SELL → executed **99.935** (half-spread 0.015 + slippage 0.05); commission **0.0500** charged separately
    - Round trip on 100.00 notional → **0.23** total, being 0.13 of price impact and 0.10 of commission (**0.23%**)
  - Buy and sell executed prices are symmetric about the mid. Costs scale linearly with notional. A zero-cost model returns the input price unchanged and a zero breakdown.
  - The formula above is market structure and belongs in code. The *rates* are broker parameters and belong in `protocol.yaml`. Rates are fixed before any search runs and are never chosen at analysis time.

- **`report-design`** — how the HTML reports look and behave. These are tools the operator returns to, not documents produced once, so this skill is mostly about interaction and only briefly about styling.

  **Interaction**
  - Hovering a grid cell reveals its metrics — Sharpe, trade count, drawdown — with no page load. Clicking it shows that cell's equity curve. Cell to curve in one interaction, because that is the question asked most often.
  - The noise ceiling is drawn on the **same axis** as the result, never in a table beneath it. If a winner sits inside the noise distribution, that must be visible rather than computed.
  - Fold results sit side by side so an inconsistent walk-forward reads as a shape.
  - Two result sets can be loaded and compared, so this week's search can be diffed against last week's.
  - `insufficient_data` is displayed as prominently as a pass or a fail. Burying an honest non-result is the one thing that makes a research tool untrustworthy.
  - Every number is traceable: protocol version, fixture hash, and the command that produced it, available on the figure itself.

  **Technique**
  - Self-contained HTML. Charts are hand-built inline SVG — no CDN, no bundler, no build step. Files stay small and open instantly, and speed is most of what makes a tool feel good.

  **Styling** — restrained, consistent across every report so they read as one product.
  - Heatmaps use a perceptually uniform sequential ramp. Never a rainbow ramp: it invents boundaries that are not in the data, and this project's entire argument is about telling a broad plateau from an isolated spike.
  - One accent colour beyond the heatmap ramp. Tabular numerals everywhere a figure appears. One font stack, one spacing scale.

---

## HAND-OFF

Print this, then **stop**:

```
GUARDS INSTALLED — not yet active.

Hooks live in settings, which Claude Code reads at startup. They take
effect on the NEXT launch, not this one.

Review before relaunching:
  □ Every blocking hook exits 2. Not 1. Anything other than 0 or 2 is
    logged and ignored, and the action proceeds.
  □ The test gate exits 0 when tests/ is empty.
  □ The secrets guard matches write, edit, AND read — and does not
    catch .env.example.
  □ Each subagent description is specific enough to actually trigger.
  □ Working tree is clean; no proof artifacts left behind.

Proof results from this session are above. Read them.

Then:
  /exit
  claude
  > Read .claude/setup.md and execute it.
```

Do not begin the project build. That is the next session.

---

## GUARDRAILS

- Configuration only. No source, no tests, no project files.
- Every guard proven, every proof reported as observed.
- Exit 2 blocks. Any other non-zero exit is logged and ignored. Getting this wrong produces a guard that appears to work and does nothing.
- Verify paths and schema against the installed version rather than assuming.
- No credentials, no absolute paths containing a username, in any file written here.
- If any step fails, **stop**. Plain-English what failed plus the single most likely fix. Never retry blindly.
- **Stop at the end of Phase 4.**

# Hook 6 - audit dispatch.  Stop.  Asks the model to run the four read-only auditors and have
# each write its RAW output to state/audit/<name>.txt.  Raw output to disk, not a summary into
# the conversation - the operator reads the files.
#
# A Stop hook cannot spawn a subagent itself; exit 2 is the only channel back into the turn.
# Two things keep that from looping: stop_hook_active short-circuits the immediate re-entry,
# and the dispatch is recorded against the state of src/ and tests/, so any one change-set
# is dispatched exactly once and a documentation edit does not dispatch at all.
. "$PSScriptRoot/_common.ps1"

$in = Read-HookInput
if ($in -and $in.stop_hook_active) { exit 0 }

$proj = Get-ProjectDir $in
Set-Location -LiteralPath $proj

# Nothing has been built yet - there is nothing for an auditor to read.
if (-not (Test-Path -LiteralPath (Join-Path $proj 'src'))) { exit 0 }

# The change-set signature covers the code and the inputs the tests read - and
# nothing else.
#
# It used to cover the commit id and the whole working tree, which meant every
# documentation edit re-dispatched four auditors over source none of them had
# read since the last time they passed it. A gate that fires on a reworded
# sentence is a gate the operator learns to clear without reading.
#
# The signature is CONTENT, not commit state: a SHA1 over the hash of every
# tracked-or-untracked file under $scoped. That matters, because a signature
# built from HEAD's tree plus `git status` changes when a change-set is
# committed, so the same content dispatched twice - once dirty, once committed.
#
# configs/ and protocol.yaml are in scope because they are not documentation:
# tests/test_strategy_config.py reads configs/ma_crossover.yaml and
# tests/test_protocol.py reads protocol.yaml, so an edit to either can turn a
# test red. Note the consequence: incrementing the walk-forward counter rewrites
# protocol.yaml and will dispatch. That is the right answer - a touch of the
# held-back window is exactly the event that deserves four auditors.
#
# The top-level entry names are folded in as well, so a NEW top-level directory
# is visible even though its contents are out of scope. Renaming or adding one
# is the case a path-scoped signature is otherwise blind to.
#
# The cost, which is deliberate: a change to README.md, LIMITATIONS.md or docs/
# dispatches nobody, and the docs-auditor is a manual call. Dispatch it by hand
# when documentation changes on its own.
$scoped = @('src', 'tests', 'configs', 'protocol.yaml')

$parts = New-Object System.Collections.Generic.List[string]
$parts.Add('toplevel:' + ((@(& git ls-tree --name-only HEAD 2>$null) | Sort-Object) -join ','))
foreach ($f in (@(& git ls-files --cached --others --exclude-standard -- $scoped 2>$null) |
                Sort-Object)) {
    if (-not $f) { continue }
    if (Test-Path -LiteralPath $f -PathType Leaf) {
        $parts.Add($f + ':' + (Get-FileHash -LiteralPath $f -Algorithm SHA1).Hash)
    } else {
        # Tracked but gone from the working tree: a deletion has to move the
        # signature too, or removing a test would silently clear the gate.
        $parts.Add($f + ':absent')
    }
}
$sig = [BitConverter]::ToString(
           [Security.Cryptography.SHA1]::Create().ComputeHash(
               [Text.Encoding]::UTF8.GetBytes(($parts -join "`n")))) -replace '-', ''

$auditDir = Join-Path $proj 'state/audit'
New-Item -ItemType Directory -Force -Path $auditDir | Out-Null
$marker = Join-Path $auditDir '.last-dispatch'
if ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $sig)) {
    exit 0
}
Set-Content -LiteralPath $marker -Value $sig -Encoding ascii

Deny @"
Audit dispatch: code or a tested input changed (src/, tests/, configs/, protocol.yaml) and this
change-set has not been audited. Before ending the turn, dispatch all four auditors in parallel.
Each writes its OWN raw reply to state/audit/:

  contract-auditor    -> state/audit/contract-auditor.txt
  arithmetic-checker  -> state/audit/arithmetic-checker.txt
  drift-checker       -> state/audit/drift-checker.txt
  docs-auditor        -> state/audit/docs-auditor.txt

Each auditor holds Write and its instructions name the single path it may write. Do NOT
transcribe their replies yourself: retyping a reply makes you a summary layer between the
auditor and the operator, which is exactly what writing the raw file to disk exists to prevent.
Confirm the four files were written and changed, then tell the operator what was found in a few
lines. The files are what they read.
This dispatch fires once per change-set, so it will not repeat this turn.
"@

# Hook 6 - audit dispatch.  Stop.  Asks the model to run the four read-only auditors and have
# each write its RAW output to state/audit/<name>.txt.  Raw output to disk, not a summary into
# the conversation - the operator reads the files.
#
# A Stop hook cannot spawn a subagent itself; exit 2 is the only channel back into the turn.
# Two things keep that from looping: stop_hook_active short-circuits the immediate re-entry,
# and the dispatch is recorded against the current HEAD + working-tree state, so any one
# change-set is dispatched exactly once.
. "$PSScriptRoot/_common.ps1"

$in = Read-HookInput
if ($in -and $in.stop_hook_active) { exit 0 }

$proj = Get-ProjectDir $in
Set-Location -LiteralPath $proj

# Nothing has been built yet - there is nothing for an auditor to read.
if (-not (Test-Path -LiteralPath (Join-Path $proj 'src'))) { exit 0 }

$head  = (@(& git rev-parse HEAD 2>$null) -join '')
$dirty = (@(& git status --porcelain 2>$null) -join "`n")
$sha   = [BitConverter]::ToString(
             [Security.Cryptography.SHA1]::Create().ComputeHash(
                 [Text.Encoding]::UTF8.GetBytes($dirty))) -replace '-', ''
$sig   = "$head|$sha"

$auditDir = Join-Path $proj 'state/audit'
New-Item -ItemType Directory -Force -Path $auditDir | Out-Null
$marker = Join-Path $auditDir '.last-dispatch'
if ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $sig)) {
    exit 0
}
Set-Content -LiteralPath $marker -Value $sig -Encoding ascii

Deny @"
Audit dispatch: this change-set has not been audited. Before ending the turn, dispatch all four
read-only auditors and copy each one's RAW reply - verbatim, not a summary - into state/audit/:

  contract-auditor    -> state/audit/contract-auditor.txt
  arithmetic-checker  -> state/audit/arithmetic-checker.txt
  drift-checker       -> state/audit/drift-checker.txt
  docs-auditor        -> state/audit/docs-auditor.txt

The auditors hold no write tool by design, so writing those four files is your job. Run them in
parallel. Tell the operator what was found; the files are what they read.
This dispatch fires once per change-set, so it will not repeat this turn.
"@

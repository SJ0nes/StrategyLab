# Hook 4 - test gate.  Stop.  Exit 2 while the suite is red, so a turn cannot end on a red suite.
# Exits 0 when tests/ is absent or holds no test files - otherwise the first session of a fresh
# repository deadlocks on its first turn with nothing to run.
. "$PSScriptRoot/_common.ps1"
. "$PSScriptRoot/_toolchain.ps1"

$in   = Read-HookInput
$proj = Get-ProjectDir $in
Set-Location -LiteralPath $proj

$testDir = Join-Path $proj 'tests'
if (-not (Test-Path -LiteralPath $testDir)) { exit 0 }
$testFiles = @(Get-ChildItem -LiteralPath $testDir -Recurse -File -Filter 'test_*.py' -ErrorAction SilentlyContinue)
if ($testFiles.Count -eq 0) { exit 0 }

$pytest = Resolve-PyTool 'pytest'
if (-not $pytest) {
    Deny "Test gate could not run: pytest is not available but tests/ holds $($testFiles.Count) test file(s). Run 'uv sync' (or 'uv add --dev pytest') so the suite can be executed."
}

$r = Invoke-PyTool $pytest @('-q')

if ($r.Code -eq 0) {
    # Clear the breaker so a later red suite gets its full run of attempts again.
    Clear-Breaker $proj 'test-gate'
    exit 0
}

$sig = (($r.Output -split "`n") | Where-Object { $_ -match '^(FAILED|ERROR)' }) -join '|'
$b   = Step-Breaker $proj 'test-gate' $sig

$tail = (($r.Output -split "`n") | Select-Object -Last 30) -join [Environment]::NewLine
if ($b.Released) {
    [Console]::Error.WriteLine("TEST GATE RELEASED after $($b.Limit) consecutive identical failures. The suite is STILL RED. This is not a pass - the gate stepped aside so the session is not deadlocked." + [Environment]::NewLine + $tail)
    exit 0
}
Deny ("Test gate: the suite is red (attempt $($b.Count) of $($b.Limit)). Fix it before ending the turn." + [Environment]::NewLine + $tail)

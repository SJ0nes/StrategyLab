# Hook 2 - commit gate.  PreToolUse on the shell tools, fires only on `git commit`.
# Blocks with exit 2 unless .gitignore exists and carries a bare `.env` line.
# The failure mode this exists for is permanent: a secret written into git history.
. "$PSScriptRoot/_common.ps1"

$in = Read-HookInput
if (-not $in) { exit 0 }
if (@('Bash', 'PowerShell') -notcontains [string]$in.tool_name) { exit 0 }

$cmd = [string]$in.tool_input.command
if (-not $cmd) { exit 0 }
if ($cmd -notmatch '\bgit\b[^;&|]*\bcommit\b') { exit 0 }

$proj   = Get-ProjectDir $in
$ignore = Join-Path $proj '.gitignore'

if (-not (Test-Path -LiteralPath $ignore)) {
    Deny "BLOCKED by commit gate: .gitignore is missing. Restore it with a bare '.env' line before committing - a secret committed once stays in the history forever."
}
$lines = @(Get-Content -LiteralPath $ignore -ErrorAction SilentlyContinue | ForEach-Object { $_.Trim() })
if ($lines -notcontains '.env') {
    Deny "BLOCKED by commit gate: .gitignore exists but has no bare '.env' line. Add it before committing."
}
exit 0

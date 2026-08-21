# Hook 3 - lint and types.  PostToolUse on Write / Edit / MultiEdit, for *.py under src/ or tests/.
# PostToolUse cannot undo a write. Exit 2 here does not prevent anything - it surfaces the
# failure to the model so it fixes the file it just wrote.
. "$PSScriptRoot/_common.ps1"
. "$PSScriptRoot/_toolchain.ps1"

$in = Read-HookInput
if (-not $in) { exit 0 }
if (@('Write', 'Edit', 'MultiEdit') -notcontains [string]$in.tool_name) { exit 0 }

$proj = Get-ProjectDir $in
$rel  = Get-RelativePath ([string]$in.tool_input.file_path) $proj
if ($rel -notmatch '\.py$') { exit 0 }
if ($rel -notmatch '^(src|tests)/') { exit 0 }

Set-Location -LiteralPath $proj
if (-not (Test-Path -LiteralPath $rel)) { exit 0 }

$ruff = Resolve-PyTool 'ruff'
$mypy = Resolve-PyTool 'mypy'

if (-not $ruff -or -not $mypy) {
    # Before the project environment exists there is nothing to lint against, so stay quiet.
    # Once pyproject.toml is present, a missing toolchain is a real problem worth surfacing.
    if (Test-Path -LiteralPath (Join-Path $proj 'pyproject.toml')) {
        Deny "Lint gate could not run: ruff and/or mypy are not available. Run 'uv sync' so the toolchain exists, then re-check $rel."
    }
    exit 0
}

$problems = @()
$r = Invoke-PyTool $ruff @('check', $rel)
if ($r.Code -ne 0) { $problems += ("ruff:" + [Environment]::NewLine + $r.Output) }
$m = Invoke-PyTool $mypy @($rel)
if ($m.Code -ne 0) { $problems += ("mypy:" + [Environment]::NewLine + $m.Output) }

if ($problems.Count -gt 0) {
    $nl = [Environment]::NewLine
    Deny ("Lint/type check failed on $rel - fix it now, the write has already landed." + $nl + $nl + ($problems -join $nl))
}
exit 0

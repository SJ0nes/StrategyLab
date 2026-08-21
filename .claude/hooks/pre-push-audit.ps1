# Hook 7 - pre-push audit.  NOT wired to any event.  The operator runs this by hand:
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .claude/hooks/pre-push-audit.ps1
#
# Pushing is never automated. A public git history is permanent.
$ErrorActionPreference = 'Continue'
$proj = if ($env:CLAUDE_PROJECT_DIR) { $env:CLAUDE_PROJECT_DIR } else { (Get-Location).Path }
Set-Location -LiteralPath $proj

$outDir = Join-Path $proj 'state/audit'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$out = Join-Path $outDir 'pre-push.txt'

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('PRE-PUSH AUDIT')
$lines.Add('generated: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
$lines.Add('')

$lines.Add('=== git log --oneline ===')
foreach ($l in @(& git log --oneline 2>&1)) { $lines.Add([string]$l) }
$lines.Add('')

$lines.Add('=== git diff --stat vs remote ===')
$upstream = (& git rev-parse --abbrev-ref '@{upstream}' 2>$null)
if ($upstream) {
    $lines.Add("upstream: $upstream")
    foreach ($l in @(& git diff --stat "$upstream...HEAD" 2>&1)) { $lines.Add([string]$l) }
} else {
    $lines.Add('no upstream configured - nothing to diff against. Every commit above is unpushed.')
}
$lines.Add('')

# Same assembled-pattern trick as the hygiene gate: written literally, this script would
# report itself on every run.
$U = 'U' + 'sers'
$scans = [ordered]@{
    'absolute path (unix /Users)'  = ('/' + $U + '/')
    'absolute path (windows)'      = ('C:' + '\\' + $U + '\\')
    'absolute path (unix /home)'   = ('/ho' + 'me/[A-Za-z0-9._-]+')
    'account-number-shaped string' = '\b\d[\d -]{7,19}\d\b'
    'credential-shaped assignment' = '(?i)\b(api[_-]?key|secret|token|password|passwd)\s*[:=]\s*\S{8,}'
}
$exempt = @('.claude/guards.md', '.claude/setup.md', '.claude/hooks/pre-push-audit.ps1', '.claude/hooks/hygiene-gate.ps1')

$lines.Add('=== scan of all tracked files ===')
$hits = 0
foreach ($f in @(& git ls-files 2>$null)) {
    if (-not $f) { continue }
    $n = ($f -replace '\\', '/')
    if ($exempt -contains $n) { $lines.Add("skipped (rulebook, scanned by eye): $n"); continue }
    if (-not (Test-Path -LiteralPath $f)) { continue }
    if ((Get-Item -LiteralPath $f).Length -gt 2MB) { $lines.Add("skipped (over 2MB): $n"); continue }
    $text = Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue
    if (-not $text) { continue }
    foreach ($k in $scans.Keys) {
        foreach ($m in [regex]::Matches($text, $scans[$k])) {
            $hits++
            $lines.Add("HIT  $n  [$k]  -> " + $m.Value)
        }
    }
}
$lines.Add('')
$lines.Add("total hits: $hits")
if ($hits -eq 0) {
    $lines.Add('CLEAN - nothing found. Read the log and the diff above, then push by hand.')
} else {
    $lines.Add('NOT CLEAN - resolve every hit above before pushing. History is permanent.')
}

Set-Content -LiteralPath $out -Value ($lines -join [Environment]::NewLine) -Encoding ascii
Write-Output "pre-push audit written to state/audit/pre-push.txt ($hits hit(s))"

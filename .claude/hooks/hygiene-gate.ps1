# Hook 5 - hygiene gate.  Stop.  Exit 2 on anything that would embarrass this repository in
# front of a reviewer: a machine-specific absolute path, a stray root-level file, or a
# scratch/temp/notebook artefact. This repository is a portfolio piece, so the file hygiene
# rules have to hold without supervision.
. "$PSScriptRoot/_common.ps1"

$in   = Read-HookInput
$proj = Get-ProjectDir $in
Set-Location -LiteralPath $proj

# Anything at the repository root that is not on this list is a finding.
$AllowedRoot = @(
    '.claude', '.git', '.github', '.gitignore', '.gitattributes',
    '.env', '.env.example', '.python-version', '.pre-commit-config.yaml',
    '.venv', '.ruff_cache', '.mypy_cache', '.pytest_cache',
    'README.md', 'CLAUDE.md', 'LIMITATIONS.md', 'LICENSE', 'CHANGELOG.md',
    'pyproject.toml', 'uv.lock', 'protocol.yaml', 'Makefile',
    'src', 'tests', 'data', 'state', 'reports', 'docs', 'configs'
)

$findings = @()

# --- root-level entries ----------------------------------------------------------------
# Tracked files plus untracked-but-not-ignored ones: a stray file is a finding the moment it
# appears, not once somebody commits it.
$all = @(& git ls-files --cached --others --exclude-standard 2>$null) | Where-Object { $_ }
$roots = $all | ForEach-Object { ($_ -replace '\\', '/').Split('/')[0] } | Sort-Object -Unique
foreach ($e in $roots) {
    if ($AllowedRoot -notcontains $e) { $findings += "root-level entry not on the allowed list: $e" }
}

# --- forbidden filenames ---------------------------------------------------------------
foreach ($f in $all) {
    $leaf = ($f -replace '\\', '/').Split('/')[-1]
    if ($leaf -like '*_temp*' -or $leaf -like '*_old*' -or $leaf -like 'scratch*' -or $leaf -like '*.ipynb') {
        $findings += "scratch/temp/notebook artefact: $f"
    }
}

# --- machine-specific absolute paths -----------------------------------------------------
# Scanned across everything git would carry - tracked, staged, and untracked-but-not-ignored -
# so a leaked path is a finding before the first commit, not after it.
# The patterns are assembled from parts on purpose: written out literally, this script would
# match itself on every run and the gate would never go green.
$U    = 'U' + 'sers'
$pats = @( ('/' + $U + '/'), ('C:' + '\\' + $U + '\\'), ('/ho' + 'me/[A-Za-z0-9._-]+') )

# The two session prompts quote these patterns as specifications of what to look for.
# They are the rulebook, not a leak, so they are the one exemption.
$ScanExempt = @('.claude/guards.md', '.claude/setup.md')

foreach ($f in $all) {
    $n = ($f -replace '\\', '/')
    if ($ScanExempt -contains $n) { continue }
    if (-not (Test-Path -LiteralPath $f)) { continue }
    if ((Get-Item -LiteralPath $f).Length -gt 2MB) { continue }
    $text = Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue
    if (-not $text) { continue }
    foreach ($p in $pats) {
        $m = [regex]::Match($text, $p)
        if ($m.Success) {
            $findings += ("absolute machine path in tracked file " + $n + ": matched '" + $m.Value + "'")
            break
        }
    }
}

$nl = [Environment]::NewLine
if ($findings.Count -eq 0) {
    Clear-Breaker $proj 'hygiene-gate'
    exit 0
}

$detail = "  - " + ($findings -join ($nl + "  - "))
$b = Step-Breaker $proj 'hygiene-gate' ($findings -join '|')
if ($b.Released) {
    [Console]::Error.WriteLine("HYGIENE GATE RELEASED after $($b.Limit) consecutive identical findings. They are STILL OPEN - the gate stepped aside so the session is not deadlocked." + $nl + $detail)
    exit 0
}
Deny ("Hygiene gate: $($findings.Count) finding(s), attempt $($b.Count) of $($b.Limit). Fix them before ending the turn." + $nl + $detail)

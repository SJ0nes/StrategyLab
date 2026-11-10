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
    # KNOWN BLIND SPOT, stated because a scanner whose reach is unknown is worse
    # than one whose reach is small. This needs 9 characters, so a BARE 8-digit
    # run - which is exactly the length of a UK bank account number - does not
    # match. Lowering it to 8 was tried and rejected: it produced 166 hits on
    # this repository, all of them uv.lock file sizes and hand-computed cost
    # figures, which is the same unusable gate this fix exists to remove. An
    # 8-digit account number written with a sort code, a space or a hyphen does
    # match. A bare one does not, and no automated check here will catch it.
    # The negative lookbehind keeps the decimal TAIL of a long float out of this.
    # 25.170068027210885 in a hand-derivation comment offered 170068027210885 -
    # fifteen digits - and the audit duly reported it as an account number.
    'account-number-shaped string' = '(?<![\w.])\d[\d -]{7,19}\d\b'
    'credential-shaped assignment' = '(?i)(?<![a-z0-9])(api[_-]?key|secret|token|password|passwd)[ \t]*[:=][ \t]*\S{8,}'
}
$exempt = @('.claude/guards.md', '.claude/setup.md', '.claude/hooks/pre-push-audit.ps1', '.claude/hooks/hygiene-gate.ps1')

# Shapes that satisfy a scan pattern and are not the thing it hunts for.
#
# This exists because the account-number pattern - a digit, then 7 to 19 of
# [digit, space, hyphen], then a digit - matches every ISO date in the
# repository. 2005-01-01 is a digit, eight of that class, a digit. The audit
# reported 53 hits, every one of them a date or a column of filler inside a
# binary fixture, and closed with NOT CLEAN. A gate that cannot go clean is not
# a gate; it is a paragraph the operator scrolls past, and the one real hit it
# ever produces will be scrolled past with the rest.
#
# Every suppression is COUNTED and reported below. A filter nobody can see is
# how a real hit disappears.
$benign = @{
    'account-number-shaped string' = @(
        '^\d{4}-\d{2}-\d{2}$',  # ISO calendar date
        # TWO OR MORE spaces is column alignment, not digit grouping. The
        # hand-derivation tables in tests/ are full of it - "100    120    90" -
        # and an account number groups with single spaces.
        '\s\s',
        # A space-padded hyphen is an arithmetic operator, not a separator.
        # "(10500 - 7857.142857)" is a comment showing its working. Account
        # numbers and sort codes never pad their hyphens.
        '\s-\s',
        # A YEAR RANGE. "2005-2018" is a digit, eight of the class, a digit -
        # the same shape as an ISO date and just as harmless. The two windows
        # this project is built around are written that way in prose dozens of
        # times, and every one of them was reported as a possible account
        # number. Both years must be plausible calendar years, so this does not
        # suppress an eight-digit run that merely happens to contain a hyphen.
        '^(19|20)\d{2}-(19|20)\d{2}$',
        # A TIMESTAMP's date and hour. "2024-03-15 00" is the front of
        # "2024-03-15 00:00:00" - the scan stops at the colon. A date, one
        # space and two digits is a time of day, not a grouped number.
        '^\d{4}-\d{2}-\d{2} \d{2}$',
        # THE DIGIT ALPHABET. "0123456789" is what str.lstrip() is handed to
        # strip digits, and what a fake key in a test is padded with. Ten
        # digits in counting order is a placeholder. Eleven are not exempt.
        '^0123456789$',
        # A POWER OF TEN. "1000000000" is the integer part of 1000000000.0, a
        # magnitude a test picked for being large. A one followed only by
        # zeros, and nothing else, so 10000000001 still hits.
        '^10+$'
    )
}

# THE CREDENTIAL SCAN HAS NO EXEMPTIONS, and two were tried. One tested the matched
# text and hid a real key given as a fallback; its replacement tested the whole
# line and still hid a key left in a trailing comment. Both were caught in
# review before they were committed. A line that trips this scan is renamed at
# the source instead - see the comment where `run` reads its key in cli.py.

$lines.Add('=== scan of all tracked files ===')
$hits = 0
$suppressed = 0
foreach ($f in @(& git ls-files 2>$null)) {
    if (-not $f) { continue }
    $n = ($f -replace '\\', '/')
    if ($exempt -contains $n) { $lines.Add("skipped (rulebook, scanned by eye): $n"); continue }
    if (-not (Test-Path -LiteralPath $f)) { continue }
    # Reading a parquet as text produces byte soup, and the digit runs in that
    # soup are not strings anybody wrote. .gitattributes already says which
    # files are binary, so ask it rather than keeping a second list here.
    if ((@(& git check-attr binary -- $f 2>$null) -join '') -match 'binary:\s*set') {
        $lines.Add("skipped (binary per .gitattributes): $n"); continue
    }
    if ((Get-Item -LiteralPath $f).Length -gt 2MB) { $lines.Add("skipped (over 2MB): $n"); continue }
    $text = Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue
    if (-not $text) { continue }
    foreach ($k in $scans.Keys) {
        foreach ($m in [regex]::Matches($text, $scans[$k])) {
            $isBenign = $false
            if ($benign.ContainsKey($k)) {
                foreach ($b in $benign[$k]) {
                    if ($m.Value -match $b) { $isBenign = $true; break }
                }
            }
            if ($isBenign) { $suppressed++; continue }
            $hits++
            $lines.Add("HIT  $n  [$k]  -> " + $m.Value)
        }
    }
}
$lines.Add('')
$lines.Add("benign shapes suppressed: $suppressed (see the `$benign table in this hook)")
$lines.Add("total hits: $hits")
if ($hits -eq 0) {
    $lines.Add('CLEAN - nothing found. Read the log and the diff above, then push by hand.')
} else {
    $lines.Add('NOT CLEAN - resolve every hit above before pushing. History is permanent.')
}

Set-Content -LiteralPath $out -Value ($lines -join [Environment]::NewLine) -Encoding ascii
Write-Output "pre-push audit written to state/audit/pre-push.txt ($hits hit(s))"

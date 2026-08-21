# Shared helpers for StrategyLab guard hooks.
# Exit-code contract for every caller: 0 = allow, 2 = block.
# Any other non-zero exit is logged by Claude Code and IGNORED - the action proceeds.
# ASCII only: Windows PowerShell 5.1 reads these files as ANSI, so non-ASCII would corrupt.

function Read-HookInput {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
    try { return ($raw | ConvertFrom-Json) } catch { return $null }
}

function Get-ProjectDir {
    param($HookInput)
    if ($env:CLAUDE_PROJECT_DIR) { return (($env:CLAUDE_PROJECT_DIR -replace '\\', '/').TrimEnd('/')) }
    if ($HookInput -and $HookInput.cwd) { return (([string]$HookInput.cwd -replace '\\', '/').TrimEnd('/')) }
    return (((Get-Location).Path -replace '\\', '/').TrimEnd('/'))
}

# Turn an absolute or relative tool path into a forward-slash path relative to the project root.
function Get-RelativePath {
    param([string]$Path, [string]$ProjectDir)
    if (-not $Path) { return '' }
    $p = ($Path -replace '\\', '/')
    if ($p.ToLower().StartsWith(($ProjectDir.ToLower() + '/'))) {
        $p = $p.Substring($ProjectDir.Length + 1)
    }
    # NB: TrimStart() takes a character set, so './' would eat the leading dot of `.env`.
    return ($p -replace '^\./', '')
}

# Deny: message goes to stderr, exit 2. Exit 2 is the only code that blocks.
function Deny {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
    exit 2
}

# Circuit breaker for the Stop gates. A Stop hook that blocks forever on a condition the model
# cannot clear burns the session with no way out, so count consecutive IDENTICAL failures and
# release after $Limit - loudly, never silently, because a released gate is not a passed gate.
function Step-Breaker {
    param([string]$ProjectDir, [string]$Name, [string]$Signature, [int]$Limit = 5)
    $dir  = Join-Path $ProjectDir 'state'
    $path = Join-Path $dir ".$Name-breaker"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $count = 0
    if (Test-Path -LiteralPath $path) {
        $parts = (Get-Content -LiteralPath $path -Raw) -split '~~~', 2
        if ($parts.Count -eq 2 -and $parts[1].Trim() -eq $Signature.Trim()) { $count = [int]$parts[0] }
    }
    $count++
    Set-Content -LiteralPath $path -Value ("$count~~~$Signature") -Encoding ascii
    return @{ Count = $count; Released = ($count -ge $Limit); Limit = $Limit }
}

function Clear-Breaker {
    param([string]$ProjectDir, [string]$Name)
    Remove-Item -LiteralPath (Join-Path $ProjectDir "state/.$Name-breaker") -ErrorAction SilentlyContinue
}

# Hook 1 - secrets guard.  PreToolUse on Read / Write / Edit / MultiEdit / NotebookEdit,
# plus the two shell tools for the narrow case of a command that would print .env out loud.
# Blocks with exit 2.  .env.example / .env.sample / .env.template / .env.dist are allowed.
. "$PSScriptRoot/_common.ps1"

$in = Read-HookInput
if (-not $in) { exit 0 }
$proj = Get-ProjectDir $in
$tool = [string]$in.tool_name
$ti   = $in.tool_input

# Basename test: `.env`, `.env.local`, `.env.production` are secrets; template variants are not.
$TemplateSuffixes = @('example', 'sample', 'template', 'dist')
function Test-IsSecretPath {
    param([string]$Path)
    if (-not $Path) { return $false }
    $leaf = Split-Path -Leaf ($Path -replace '\\', '/')
    if ($leaf -notmatch '^\.env(\..+)?$') { return $false }
    foreach ($s in $TemplateSuffixes) {
        if ($leaf -ieq ".env.$s") { return $false }
    }
    return $true
}

if (@('Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit') -contains $tool) {
    $candidates = @()
    if ($ti) {
        foreach ($f in @('file_path', 'notebook_path', 'path')) {
            if ($ti.PSObject.Properties.Name -contains $f) { $candidates += [string]$ti.$f }
        }
    }
    foreach ($c in $candidates) {
        if (Test-IsSecretPath $c) {
            $rel = Get-RelativePath $c $proj
            Deny "BLOCKED by secrets guard: $tool on '$rel'. Environment files holding real credentials are never read or written by the agent. Put anything that belongs in the repository into .env.example instead."
        }
    }
    exit 0
}

if (@('Bash', 'PowerShell') -contains $tool) {
    $cmd = [string]$ti.command
    if (-not $cmd) { exit 0 }
    # Only commands that would print .env into the transcript, or overwrite it by redirection.
    # Deliberately narrow, so that `git check-ignore .env` and `grep .env .gitignore` still work.
    # The lookarounds stop `.env.example` and `settings.env` from matching.
    $readers = 'cat|type|more|less|head|tail|Get-Content|gc|Write-Output'
    $readPat = '(^|[;&|]\s*)(' + $readers + ')\s[^;&|]*?(?<![\w.\-])\.env(?![\w.\-])'
    if ($cmd -match $readPat) {
        Deny "BLOCKED by secrets guard: this command would print the contents of .env. Secrets are never echoed into the transcript."
    }
    if ($cmd -match '>\s*[^\s;&|]*(?<![\w.\-])\.env(?![\w.\-])') {
        Deny "BLOCKED by secrets guard: this command redirects output into .env. The agent does not write environment files."
    }
}
exit 0

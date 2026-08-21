# Resolve how to run a Python dev tool. uv is the project's runner; `py -m` is the fallback
# for the window before `uv sync` has happened. Returns $null when neither can run the tool.

function Resolve-PyTool {
    param([string]$Tool)
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        return @{ Exe = 'uv'; Args = @('run', $Tool) }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -m $Tool --version *> $null
        if ($LASTEXITCODE -eq 0) { return @{ Exe = 'py'; Args = @('-m', $Tool) } }
    }
    return $null
}

function Invoke-PyTool {
    param($Runner, [string[]]$Extra)
    $all = @($Runner.Args) + @($Extra)
    $out = & $Runner.Exe @all 2>&1 | Out-String
    return @{ Code = $LASTEXITCODE; Output = $out }
}

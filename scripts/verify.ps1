[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [switch]$Full,
    [switch]$ModelSmoke,
    [switch]$SkipMcp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message =="
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "+ $FilePath $($Arguments -join ' ')"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
    }
}

function Invoke-Captured {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "+ $FilePath $($Arguments -join ' ')"
    $output = @(& $FilePath @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host ([string]$line)
    }
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $FilePath"
    }
    return @($output | ForEach-Object { [string]$_ })
}

function Find-RunDirectory {
    param([string[]]$Output)

    foreach ($line in $Output) {
        if ($line -match '^Run directory:\s*(.+)$') {
            return $Matches[1].Trim()
        }
    }
    throw "Could not identify the run directory from command output."
}

function Resolve-RunDirectory {
    param(
        [string]$ProjectDir,
        [string]$RunDirectory
    )

    if ([System.IO.Path]::IsPathRooted($RunDirectory)) {
        return (Resolve-Path -LiteralPath $RunDirectory).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path $ProjectDir $RunDirectory)).Path
}

function Assert-SingleSmoke {
    param([string]$RunDirectory)

    $trace = Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $RunDirectory "trace.json") | ConvertFrom-Json
    $usage = Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $RunDirectory "usage.json") | ConvertFrom-Json
    $patch = Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $RunDirectory "patch.diff")
    if ($trace.stop_reason -ne "final_answer") {
        throw "Real-model smoke did not complete: $($trace.stop_reason)"
    }
    if ([int]$usage.summary.llm_calls -lt 1) {
        throw "Real-model smoke recorded no LLM call."
    }
    if ($patch.Trim()) {
        throw "Read-only real-model smoke produced a candidate patch."
    }
    Write-Host "Validated single-agent evidence: $RunDirectory"
}

function Assert-FanoutSmoke {
    param([string]$RunDirectory)

    $summaryPath = Join-Path $RunDirectory "fanout\fanout_summary.json"
    $summary = Get-Content -Encoding UTF8 -Raw -LiteralPath $summaryPath | ConvertFrom-Json
    $patch = Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $RunDirectory "patch.diff")
    if ($summary.status -ne "passed" -or $summary.final_decision -ne "PASS") {
        throw "Real-model fanout did not pass: status=$($summary.status), decision=$($summary.final_decision)"
    }
    if (-not $summary.results -or @($summary.results | Where-Object { $_.status -ne "completed" }).Count -gt 0) {
        throw "Real-model fanout has incomplete workers."
    }
    if (@($summary.results | Where-Object { @($_.touched_files).Count -gt 0 }).Count -gt 0) {
        throw "Read-only real-model fanout modified a worker workspace."
    }
    if ([int]$summary.metrics.llm_calls -lt (@($summary.results).Count + 1)) {
        throw "Real-model fanout is missing worker or finalizer LLM usage."
    }
    if ([int]$summary.metrics.finalizer_llm_calls -lt 1) {
        throw "Real-model fanout finalizer did not run."
    }
    if ($patch.Trim()) {
        throw "Read-only real-model fanout produced a candidate patch."
    }
    Write-Host "Validated live fanout evidence: $RunDirectory"
}

$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectDir ".venv-win\Scripts\python.exe"
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$verifyDir = Join-Path $projectDir ".agent_forge\verify"
New-Item -ItemType Directory -Force -Path $verifyDir | Out-Null
$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING

Push-Location $projectDir
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Write-Host "NanoHarness Windows verification"
    Write-Host "Python: $PythonPath"
    & $PythonPath --version

    Write-Step "Package import smoke"
    Invoke-Native -FilePath $PythonPath -Arguments @(
        "-c",
        "import agent_forge; from agent_forge.harness import Harness, RunRequest, RunResult; print('agent_forge import: OK')"
    )

    Write-Step "Installed forge CLI help"
    $forgePath = Join-Path (Split-Path -Parent $PythonPath) "forge.exe"
    if (Test-Path -LiteralPath $forgePath) {
        Invoke-Native -FilePath $forgePath -Arguments @("--help")
    }
    else {
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "agent_forge", "--help")
    }

    if (-not $Full -and -not $ModelSmoke) {
        Write-Host ""
        Write-Host "Minimal verification passed. Use -ModelSmoke for the API path or -Full for the complete regression suite."
        return
    }

    if ($Full) {
        Write-Step "Compile Python files"
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "compileall", "-q", "agent_forge", "tests")

        Write-Step "Static type contracts"
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "mypy", "--version")
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "mypy", "agent_forge")

        Write-Step "Public CLI doctor"
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "agent_forge", "doctor")

        Write-Step "Public CLI skills"
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "agent_forge", "skills", "list")

        Write-Step "Unit regression suite"
        Invoke-Native -FilePath $PythonPath -Arguments @("-m", "unittest", "discover", "tests")

        if (-not $SkipMcp) {
            Write-Step "Offline MCP verification"
            & (Join-Path $PSScriptRoot "verify_mcp.ps1") -PythonPath $PythonPath
        }
    }

    if ($env:DEEPSEEK_API_KEY) {
        Write-Step "Real-model read-only smoke"
        $singleOutput = Invoke-Captured -FilePath $PythonPath -Arguments @(
            "-m", "agent_forge", "run",
            "Call read_file exactly once for pyproject.toml, then explain the package entrypoint and Python version requirement without modifying files",
            "--provider", "deepseek",
            "--approval-mode", "locked",
            "--max-steps", "4",
            "--workspace", ".",
            "--execution-mode", "worktree",
            "--network-policy", "deny",
            "--no-keep-worktree",
            "--output-root", (Join-Path $verifyDir "runs")
        )
        $singleRun = Resolve-RunDirectory -ProjectDir $projectDir -RunDirectory (Find-RunDirectory -Output $singleOutput)
        Assert-SingleSmoke -RunDirectory $singleRun

        if ($Full) {
            Write-Step "Real-model two-worker fanout smoke"
            $fanoutOutput = Invoke-Captured -FilePath $PythonPath -Arguments @(
                "-m", "agent_forge", "run",
                "Review runtime and safety evidence in parallel without modifying files",
                "--agent-mode", "fanout",
                "--fanout-plan", "examples/fanout-plan.sample.json",
                "--max-workers", "2",
                "--provider", "deepseek",
                "--approval-mode", "locked",
                "--max-steps", "8",
                "--workspace", ".",
                "--execution-mode", "worktree",
                "--network-policy", "deny",
                "--no-keep-worktree",
                "--output-root", (Join-Path $verifyDir "runs")
            )
            $fanoutRun = Resolve-RunDirectory -ProjectDir $projectDir -RunDirectory (Find-RunDirectory -Output $fanoutOutput)
            Assert-FanoutSmoke -RunDirectory $fanoutRun
        }
    }
    else {
        Write-Step "Real-model smoke skipped"
        Write-Host "DEEPSEEK_API_KEY is not set. Offline verification is complete."
    }

    Write-Host ""
    Write-Host "Verification passed."
    Write-Host "Artifacts are under $verifyDir"
}
finally {
    if ($null -eq $previousPythonUtf8) {
        Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONUTF8 = $previousPythonUtf8
    }
    if ($null -eq $previousPythonIoEncoding) {
        Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }
    Pop-Location
}

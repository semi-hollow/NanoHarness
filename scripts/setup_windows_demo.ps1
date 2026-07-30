[CmdletBinding()]
param(
    [string]$PythonPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

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

$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$setupScript = Join-Path $PSScriptRoot "setup_windows_local.ps1"
$venvPython = Join-Path $projectDir ".venv-win\Scripts\python.exe"

if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
    throw @"
Git was not found. The deterministic repair demo creates a temporary repository.
Install Git for Windows, reopen this folder, and run setup_windows_demo.cmd again:
https://git-scm.com/download/win
"@
}

$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING

Push-Location $projectDir
try {
    Write-Host "NanoHarness Windows offline demo setup"
    Write-Host "No API key is required and these checks make no LLM calls."

    $setupArguments = @{
        WithDev = $true
    }
    if ($PythonPath) {
        $setupArguments["PythonPath"] = $PythonPath
    }
    & $setupScript @setupArguments

    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Windows demo interpreter was not created: $venvPython"
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    Write-Host ""
    Write-Host "== Verify governed repair and resume =="
    Invoke-Native -FilePath $venvPython -Arguments @(
        "examples\debug_lab\run.py",
        "governed"
    )

    Write-Host ""
    Write-Host "== Verify coordinated workers, merge, and pytest =="
    Invoke-Native -FilePath $venvPython -Arguments @(
        "examples\debug_lab\run.py",
        "coordinated"
    )

    Write-Host ""
    Write-Host "Windows offline demo setup succeeded."
    Write-Host "Open this repository in PyCharm and run:"
    Write-Host "  1. NanoHarness Windows Offline 1 - Governed Repair"
    Write-Host "  2. NanoHarness Windows Offline 2 - Coordinated Agents"
    Write-Host "  3. NanoHarness Windows Offline 3 - Workbench"
    Write-Host ""
    Write-Host "This path proves runtime control, scoped fanout, merge, tests, and evidence."
    Write-Host "It does not claim real-model quality or official SWE-bench results."
}
finally {
    $env:PYTHONUTF8 = $previousPythonUtf8
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    Pop-Location
}

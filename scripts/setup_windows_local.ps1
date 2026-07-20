[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [switch]$WithDev,
    [switch]$WithBench,
    [switch]$SkipVerify
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

function Test-PythonCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArguments = @()
    )

    try {
        $output = & $FilePath @PrefixArguments -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $output) {
            return $false
        }
        $parts = ([string]$output).Trim().Split(".")
        return [int]$parts[0] -eq 3 -and [int]$parts[1] -ge 10
    }
    catch {
        return $false
    }
}

function Resolve-PythonCommand {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        $resolved = (Resolve-Path -LiteralPath $ExplicitPath).Path
        if (-not (Test-PythonCommand -FilePath $resolved)) {
            throw "The requested Python is not usable or is older than 3.10: $resolved"
        }
        return [PSCustomObject]@{ FilePath = $resolved; PrefixArguments = @() }
    }

    $bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if ((Test-Path -LiteralPath $bundledPython) -and (Test-PythonCommand -FilePath $bundledPython)) {
        return [PSCustomObject]@{ FilePath = $bundledPython; PrefixArguments = @() }
    }

    $perUser311 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if ((Test-Path -LiteralPath $perUser311) -and (Test-PythonCommand -FilePath $perUser311)) {
        return [PSCustomObject]@{ FilePath = $perUser311; PrefixArguments = @() }
    }

    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        foreach ($selector in @("-3.11", "-3.12", "-3.10", "-3")) {
            if (Test-PythonCommand -FilePath $launcher.Source -PrefixArguments @($selector)) {
                return [PSCustomObject]@{
                    FilePath = $launcher.Source
                    PrefixArguments = @($selector)
                }
            }
        }
    }

    foreach ($name in @("python3.11", "python", "python3")) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $candidate -and (Test-PythonCommand -FilePath $candidate.Source)) {
            return [PSCustomObject]@{ FilePath = $candidate.Source; PrefixArguments = @() }
        }
    }

    throw @"
Python 3.10 or newer was not found. Python 3.11 is recommended.
Install the official 64-bit Windows build, then rerun this script:
https://www.python.org/downloads/release/python-3119/
"@
}

$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $projectDir "pyproject.toml"))) {
    throw "Could not locate pyproject.toml from $PSScriptRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectDir "agent_forge"))) {
    throw "Could not locate agent_forge from $PSScriptRoot"
}

Push-Location $projectDir
try {
    Write-Host "NanoHarness Windows setup"
    Write-Host "Project directory: $projectDir"

    # Windows uses its own environment so a macOS/WSL .venv can remain untouched.
    $venvDir = Join-Path $projectDir ".venv-win"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"

    if (-not (Test-Path -LiteralPath $venvPython)) {
        if ((Test-Path -LiteralPath $venvDir) -and (Get-ChildItem -Force -LiteralPath $venvDir | Select-Object -First 1)) {
            throw "The existing .venv-win has no usable Windows interpreter; refusing to overwrite it."
        }
        $python = Resolve-PythonCommand -ExplicitPath $PythonPath
        Write-Step "Create Windows virtual environment"
        Invoke-Native -FilePath $python.FilePath -Arguments @($python.PrefixArguments + @("-m", "venv", ".venv-win"))
    }

    if (-not (Test-PythonCommand -FilePath $venvPython)) {
        throw "The Windows virtual environment is not usable: $venvPython"
    }

    Write-Host "Venv Python: $venvPython"
    & $venvPython --version

    Write-Step "Install NanoHarness core in editable mode"
    Invoke-Native -FilePath $venvPython -Arguments @("-m", "pip", "install", "-e", ".")

    if ($WithDev -and $WithBench) {
        Write-Step "Install optional development and benchmark dependencies"
        Invoke-Native -FilePath $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev,bench]")
    }
    elseif ($WithDev) {
        Write-Step "Install optional development dependencies"
        Invoke-Native -FilePath $venvPython -Arguments @("-m", "pip", "install", "-e", ".[dev]")
    }
    elseif ($WithBench) {
        Write-Step "Install optional benchmark dependencies"
        Invoke-Native -FilePath $venvPython -Arguments @("-m", "pip", "install", "-e", ".[bench]")
    }

    if (-not $SkipVerify) {
        Write-Step "Run Windows verification"
        & (Join-Path $PSScriptRoot "verify.ps1") -PythonPath $venvPython
    }

    Write-Host ""
    Write-Host "Setup succeeded."
    Write-Host "Activate: .\.venv-win\Scripts\Activate.ps1"
    Write-Host "Verify:   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1"
}
finally {
    Pop-Location
}

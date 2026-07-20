[CmdletBinding()]
param(
    [string]$PythonPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonPath) {
    $PythonPath = Join-Path $projectDir ".venv-win\Scripts\python.exe"
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$outputDir = Join-Path $projectDir ".agent_forge\verify"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$previousPath = $env:PATH
$previousProvider = $env:AGENT_FORGE_WEB_PROVIDER
$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING

Push-Location $projectDir
try {
    $env:PATH = "$(Split-Path -Parent $PythonPath);$previousPath"
    $env:AGENT_FORGE_WEB_PROVIDER = "offline"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    Write-Host "== MCP tool discovery =="
    $toolsPath = Join-Path $outputDir "mcp_tools.windows.json"
    $toolOutput = & $PythonPath -m agent_forge.mcp.builtin_server --workspace . --list-tools
    if ($LASTEXITCODE -ne 0) {
        throw "MCP tool discovery failed with exit code $LASTEXITCODE"
    }
    $toolOutput | Set-Content -Encoding UTF8 -LiteralPath $toolsPath

    Write-Host "== MCP protocol and registry =="
    $verification = @'
import json
import sys

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.mcp_config import MCPConfigLoader
from agent_forge.tools.mcp_stdio import MCPStdioClient, MCPStdioServerSpec
from agent_forge.tools.registry import ToolRegistry

spec = MCPStdioServerSpec(
    name="forge",
    command=sys.executable,
    args=["-m", "agent_forge.mcp.builtin_server", "--workspace", "."],
    cwd=".",
    env={"AGENT_FORGE_WEB_PROVIDER": "offline"},
)
client = MCPStdioClient(spec)
tools = client.discover_tools()
names = sorted(tool["name"] for tool in tools)
print("discovered:", ", ".join(names))
required = {"repo_policy", "current_time", "web_search", "web_fetch"}
missing = required.difference(names)
if missing:
    raise SystemExit(f"missing MCP tools: {sorted(missing)}")

search = client.call_tool("web_search", {"query": "agent tool protocol", "max_results": 1})
search_text = "\n".join(
    item.get("text", "")
    for item in search.get("content", [])
    if isinstance(item, dict)
)
if "provider: offline" not in search_text:
    raise SystemExit("offline web_search did not return the expected provider marker")

registry = ToolRegistry()
report = MCPConfigLoader(WorkspaceSandbox(".")).load_into(registry, "mcp_tools.json")
registered = [row.name for row in report.tools if row.registered]
print("registered:", ", ".join(sorted(registered)))
observation = registry.execute("forge.current_time", {})
if not observation.success or "utc_time:" not in observation.content:
    raise SystemExit(f"current_time failed: {observation.content}")

print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
'@

    $verification | & $PythonPath -
    if ($LASTEXITCODE -ne 0) {
        throw "MCP protocol verification failed with exit code $LASTEXITCODE"
    }

    Write-Host "MCP verification passed."
}
finally {
    $env:PATH = $previousPath
    if ($null -eq $previousProvider) {
        Remove-Item Env:AGENT_FORGE_WEB_PROVIDER -ErrorAction SilentlyContinue
    }
    else {
        $env:AGENT_FORGE_WEB_PROVIDER = $previousProvider
    }
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

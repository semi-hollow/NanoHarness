#!/usr/bin/env bash
set -Eeuo pipefail

# Purpose:
#   Verify the MCP path without requiring external services:
#     1. start the built-in stdio MCP server,
#     2. discover tool schemas,
#     3. call a tool directly through MCPStdioClient,
#     4. load the same server through mcp_tools.example.json into ToolRegistry.
#
# Network behavior:
#   This script intentionally leaves AGENT_FORGE_WEB_PROVIDER=offline. It proves
#   the MCP protocol and tool registration path on company machines. For live
#   lookup, run the same config with:
#     AGENT_FORGE_MCP_ALLOW_NETWORK=1 AGENT_FORGE_WEB_PROVIDER=duckduckgo ...

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
  echo "Missing .venv. Run scripts/setup_macos_local.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

export AGENT_FORGE_WEB_PROVIDER="${AGENT_FORGE_WEB_PROVIDER:-offline}"

python -m agent_forge.mcp.builtin_server --workspace . --list-tools >/tmp/agent_forge_mcp_tools.json

python - <<'PY'
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
search_text = "\n".join(item.get("text", "") for item in search.get("content", []) if isinstance(item, dict))
if "provider: offline" not in search_text:
    raise SystemExit("offline web_search did not return the expected provider marker")

registry = ToolRegistry()
report = MCPConfigLoader(WorkspaceSandbox(".")).load_into(registry, "mcp_tools.example.json")
registered = [row.name for row in report.tools if row.registered]
print("registered:", ", ".join(sorted(registered)))
observation = registry.execute("forge.current_time", {})
if not observation.success or "utc_time:" not in observation.content:
    raise SystemExit(f"current_time failed: {observation.content}")

print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
PY

echo "MCP verification passed."

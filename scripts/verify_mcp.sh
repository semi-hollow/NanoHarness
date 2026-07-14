#!/usr/bin/env bash
set -Eeuo pipefail

# 用途：
#   在不依赖外部服务的情况下验证 MCP 路径：
#     1. 启动内置 stdio MCP server；
#     2. 发现工具 schema；
#     3. 通过 MCPStdioClient 直接调用工具；
#     4. 通过 mcp_tools.json 把同一 server 加载到 ToolRegistry。
#
# 网络行为：
#   本脚本有意保持 AGENT_FORGE_WEB_PROVIDER=offline，用于在受限机器上证明 MCP 协议和
#   工具注册路径。需要真实联网查询时，使用同一配置并增加：
#     AGENT_FORGE_MCP_ALLOW_NETWORK=1 AGENT_FORGE_WEB_PROVIDER=duckduckgo ...

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "Could not find a usable Python interpreter." >&2
    exit 1
  fi
fi

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export AGENT_FORGE_WEB_PROVIDER="${AGENT_FORGE_WEB_PROVIDER:-offline}"

"${PYTHON_BIN}" -m agent_forge.mcp.builtin_server --workspace . --list-tools >/tmp/agent_forge_mcp_tools.json

"${PYTHON_BIN}" - <<'PY'
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
report = MCPConfigLoader(WorkspaceSandbox(".")).load_into(registry, "mcp_tools.json")
registered = [row.name for row in report.tools if row.registered]
print("registered:", ", ".join(sorted(registered)))
observation = registry.execute("forge.current_time", {})
if not observation.success or "utc_time:" not in observation.content:
    raise SystemExit(f"current_time failed: {observation.content}")

print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
PY

echo "MCP verification passed."

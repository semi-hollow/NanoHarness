import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_forge.runtime.observation import Observation
from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.adapters.mcp_style_adapter import MCPStyleToolAdapter, MCPStyleToolSpec
from agent_forge.tools.mcp_stdio import MCPStdioClient, MCPStdioServerSpec, MCPStdioTool
from agent_forge.tools.registry import ToolRegistry


@dataclass(frozen=True)
class MCPToolRegistration:
    """Result of one configured MCP-style tool registration."""

    # Tool name after optional server prefixing.
    name: str

    # Whether the tool was registered.
    registered: bool

    # Why it was accepted or skipped.
    reason: str


@dataclass(frozen=True)
class MCPConfigReport:
    """Audit report for a loaded MCP-style config file."""

    # Path that was loaded.
    path: str

    # Registered/skipped tool rows.
    tools: list[MCPToolRegistration] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return JSON-safe data for CLI/session reports."""

        return {
            "path": self.path,
            "tools": [
                {
                    "name": tool.name,
                    "registered": tool.registered,
                    "reason": tool.reason,
                }
                for tool in self.tools
            ],
        }


class MCPConfigLoader:
    """Load local MCP-style tool definitions into the ToolRegistry.

    This is a local, safe subset of the MCP idea: a config file can describe
    tools, schemas, handlers, and allowlists. The loader converts those specs
    into Agent Forge ``Tool`` objects so AgentLoop and ToolRouter stay unchanged.

    Why it exists:
        External tools should be discovered and registered at the tool boundary,
        not hard-coded into AgentLoop. This loader is the bridge from a portable
        config file to local ``Tool`` instances.

    Method map:
        ``load_into`` is the public entry.
        ``_register_stdio_server`` starts discovery for command-backed servers.
        ``_resolve_command`` keeps Python MCP servers inside the active venv.
        ``_handler`` supports the local handler subset used by the bundled MCP config.
    """

    def __init__(self, sandbox: WorkspaceSandbox):
        """Use the same sandbox as built-in tools for file-backed handlers."""

        self.sandbox = sandbox

    def load_into(
        self,
        registry: ToolRegistry,
        config_path: str | Path,
        allowed_tools: list[str] | None = None,
    ) -> MCPConfigReport:
        """Parse config and register allowed tools."""

        path = Path(config_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        explicit_allowlist = set(allowed_tools or data.get("allowed_tools") or [])
        rows: list[MCPToolRegistration] = []

        for server in data.get("servers", []):
            if not server.get("enabled", True):
                rows.append(MCPToolRegistration(str(server.get("name", "unknown")), False, "server disabled"))
                continue
            server_name = str(server.get("name") or "local")
            prefix = bool(server.get("prefix_tool_names", True))
            if server.get("transport") == "stdio" or server.get("command"):
                rows.extend(
                    self._register_stdio_server(
                        registry,
                        server,
                        explicit_allowlist,
                        server_name,
                        prefix,
                        config_dir=path.parent,
                    )
                )
                continue
            for raw_tool in server.get("tools", []):
                raw_name = str(raw_tool.get("name") or "")
                if not raw_name:
                    rows.append(MCPToolRegistration("", False, "tool missing name"))
                    continue
                name = f"{server_name}.{raw_name}" if prefix and "." not in raw_name else raw_name
                if explicit_allowlist and name not in explicit_allowlist and raw_name not in explicit_allowlist:
                    rows.append(MCPToolRegistration(name, False, "not in MCP allowlist"))
                    continue
                handler_name = str(raw_tool.get("handler") or "echo")
                handler = self._handler(handler_name)
                if handler is None:
                    rows.append(MCPToolRegistration(name, False, f"unsupported handler: {handler_name}"))
                    continue
                spec = MCPStyleToolSpec(
                    name=name,
                    description=str(raw_tool.get("description") or f"MCP-style tool {name}"),
                    input_schema=raw_tool.get("input_schema") or {"type": "object", "properties": {}},
                )
                registry.register(MCPStyleToolAdapter(spec, handler).to_tool())
                rows.append(MCPToolRegistration(name, True, "registered"))

        return MCPConfigReport(str(path), rows)

    def _register_stdio_server(
        self,
        registry: ToolRegistry,
        server: dict[str, Any],
        explicit_allowlist: set[str],
        server_name: str,
        prefix: bool,
        config_dir: Path,
    ) -> list[MCPToolRegistration]:
        """Discover and register tools from a command-backed stdio server."""

        rows: list[MCPToolRegistration] = []
        command = self._resolve_command(str(server.get("command") or ""))
        if not command:
            return [MCPToolRegistration(server_name, False, "stdio server missing command")]
        cwd = self._resolve_server_cwd(server.get("cwd"), config_dir)
        spec = MCPStdioServerSpec(
            name=server_name,
            command=command,
            args=[str(arg) for arg in server.get("args", [])],
            cwd=cwd,
            env={str(k): str(v) for k, v in (server.get("env") or {}).items()},
            timeout_seconds=float(server.get("timeout_seconds") or 10.0),
            prefix_tool_names=prefix,
        )
        client = MCPStdioClient(spec)
        try:
            tools = client.discover_tools()
        except Exception as exc:
            return [MCPToolRegistration(server_name, False, f"stdio discovery failed: {exc}")]

        for remote_tool in tools:
            remote_name = str(remote_tool.get("name") or "")
            local_name = f"{server_name}.{remote_name}" if prefix and "." not in remote_name else remote_name
            if explicit_allowlist and local_name not in explicit_allowlist and remote_name not in explicit_allowlist:
                rows.append(MCPToolRegistration(local_name, False, "not in MCP allowlist"))
                continue
            registry.register(MCPStdioTool(client, local_name, remote_name, remote_tool))
            rows.append(MCPToolRegistration(local_name, True, "registered stdio tool"))
        if not tools:
            rows.append(MCPToolRegistration(server_name, False, "stdio server returned no tools"))
        return rows

    def _resolve_command(self, command: str) -> str:
        """Resolve portable Python command names for stdio MCP servers."""

        if command in {"python", "python3"}:
            return sys.executable
        return command

    def _resolve_server_cwd(self, raw_cwd: Any, config_dir: Path) -> str:
        """Resolve a stdio server working directory.

        Defaulting to the sandbox workspace root keeps the MCP server aligned
        with the agent's active workspace. If a config supplies ``cwd``, relative
        paths are resolved next to the config file so configs stay portable.
        """

        if not raw_cwd:
            return str(self.sandbox.workspace_root)
        cwd = Path(str(raw_cwd))
        if not cwd.is_absolute():
            cwd = config_dir / cwd
        return str(cwd.resolve())

    def _handler(self, handler_name: str):
        """Return one supported safe local handler."""

        if handler_name == "echo":
            return lambda args: json.dumps(args, ensure_ascii=False, sort_keys=True)
        if handler_name == "read_text":
            return self._read_text
        if handler_name == "repo_policy":
            return self._repo_policy
        return None

    def _read_text(self, args: dict[str, Any]) -> Observation:
        """Read one sandboxed text file through an MCP-style tool."""

        path = args.get("path", "")
        if not isinstance(path, str) or not path:
            return Observation("mcp.read_text", False, "invalid arguments: missing path")
        try:
            safe_path = self.sandbox.ensure_safe_path(path)
            return Observation("mcp.read_text", True, safe_path.read_text(encoding="utf-8")[:3000])
        except Exception as exc:
            return Observation("mcp.read_text", False, f"mcp read_text failed: {exc}")

    def _repo_policy(self, args: dict[str, Any]) -> str:
        """Return a short project-policy answer from FORGE.md."""

        topic = str(args.get("topic") or "").lower()
        policy_file = self.sandbox.workspace_root / "FORGE.md"
        if not policy_file.exists():
            return "FORGE.md not found"
        text = policy_file.read_text(encoding="utf-8")
        if not topic:
            return text[:3000]
        lines = [line for line in text.splitlines() if topic in line.lower()]
        return "\n".join(lines[:20]) or text[:1200]

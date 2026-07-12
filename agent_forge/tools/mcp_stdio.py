import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from agent_forge.runtime.observation import Observation
from agent_forge.tools.base import Tool


@dataclass(frozen=True)
class MCPStdioServerSpec:
    """Configuration for one stdio JSON-RPC tool server."""

    # Server name used for trace/report/tool prefixing.
    name: str

    # Executable to start.
    command: str

    # Command arguments.
    args: list[str] = field(default_factory=list)

    # Optional working directory for the server process. This matters for tools
    # such as repo_policy that need a stable repository root even when the user
    # starts the CLI from a different directory.
    cwd: str = ""

    # Non-secret environment additions.
    env: dict[str, str] = field(default_factory=dict)

    # Per request timeout.
    timeout_seconds: float = 10.0

    # Prefix remote tools as ``server.tool`` to avoid collisions.
    prefix_tool_names: bool = True


class MCPStdioClient:
    """Small stdio JSON-RPC client for external tool servers.

    The implementation supports the subset Agent Forge needs: initialize,
    tools/list, and tools/call. It keeps the process lifetime short and
    per-request so a broken tool server cannot poison the main AgentLoop.

    Why it exists:
        MCP is useful only if the agent can cross a real process/protocol
        boundary. This client proves discovery and invocation without pulling a
        large MCP SDK into the learning project.

    Method map:
        ``discover_tools`` reads remote schemas.
        ``call_tool`` invokes one remote tool.
        ``_session_call`` owns process lifetime.
        ``_read_response`` handles newline JSON and Content-Length framing.
    """

    def __init__(self, spec: MCPStdioServerSpec) -> None:
        """Store server launch configuration."""

        self.spec = spec

    def discover_tools(self) -> list[dict[str, Any]]:
        """Return tool definitions advertised by the server."""

        response = self._session_call(
            [
                ("initialize", {"clientInfo": {"name": "agent-forge", "version": "schema"}, "capabilities": {}}),
                ("tools/list", {}),
            ]
        )
        result = response.get("result") or {}
        tools = result.get("tools") or []
        return [tool for tool in tools if isinstance(tool, dict) and tool.get("name")]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one remote tool and return the JSON-RPC result."""

        response = self._session_call(
            [
                ("initialize", {"clientInfo": {"name": "agent-forge", "version": "schema"}, "capabilities": {}}),
                ("tools/call", {"name": tool_name, "arguments": arguments or {}}),
            ]
        )
        return response.get("result") or {}

    def _session_call(self, calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        """Start server, send JSON-RPC calls, return the last response."""

        env = os.environ.copy()
        env.update(self.spec.env)
        proc = subprocess.Popen(
            [self.spec.command, *self.spec.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=self.spec.cwd or None,
        )
        last_response: dict[str, Any] = {}
        try:
            for index, (method, params) in enumerate(calls, start=1):
                request = {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
                assert proc.stdin is not None
                proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                proc.stdin.flush()
                last_response = self._read_response(proc, index)
                if "error" in last_response:
                    return last_response
            return last_response
        finally:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                if proc.stdout is not None:
                    proc.stdout.close()
                if proc.stderr is not None:
                    proc.stderr.close()
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                proc.kill()

    def _read_response(self, proc: subprocess.Popen, request_id: int) -> dict[str, Any]:
        """Read newline-delimited JSON or Content-Length framed JSON."""

        assert proc.stdout is not None
        deadline = time.time() + self.spec.timeout_seconds
        buffered_headers: list[str] = []
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                if buffered_headers:
                    content_length = self._content_length(buffered_headers)
                    if content_length is not None:
                        raw = proc.stdout.read(content_length)
                        response = json.loads(raw)
                        if response.get("id") == request_id:
                            return response
                    buffered_headers = []
                continue
            if stripped.startswith("Content-Length:"):
                buffered_headers.append(stripped)
                continue
            try:
                response = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if response.get("id") == request_id:
                return response
        stderr = ""
        if proc.stderr is not None:
            try:
                stderr = proc.stderr.read(1000)
            except Exception:
                stderr = ""
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": "timeout", "message": stderr or "no response"}}

    def _content_length(self, headers: list[str]) -> int | None:
        """Extract Content-Length from framed transport headers."""

        for header in headers:
            if header.lower().startswith("content-length:"):
                try:
                    return int(header.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None


class MCPStdioTool(Tool):
    """ToolRegistry-compatible wrapper around one remote stdio tool."""

    def __init__(self, client: MCPStdioClient, local_name: str, remote_name: str, spec: dict[str, Any]) -> None:
        """Store remote schema and server client."""

        self.client = client
        self.name = local_name
        self.remote_name = remote_name
        self.description = str(spec.get("description") or f"External tool {local_name}")
        self.input_schema = spec.get("inputSchema") or spec.get("input_schema") or {"type": "object", "properties": {}}

    def schema(self) -> dict:
        """Expose remote JSON Schema through the local tool protocol."""

        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.input_schema.get("properties", {}),
            "required": self.input_schema.get("required", []),
            "metadata": {"source": "mcp_stdio", "remote_name": self.remote_name},
        }

    def execute(self, arguments: dict) -> Observation:
        """Call the remote tool and normalize the response into Observation."""

        try:
            result = self.client.call_tool(self.remote_name, arguments or {})
        except Exception as exc:
            return Observation(self.name, False, f"mcp stdio call failed: {exc}")
        if result.get("isError"):
            return Observation(self.name, False, _content_to_text(result.get("content")))
        return Observation(self.name, True, _content_to_text(result.get("content") or result))


def _content_to_text(content: Any) -> str:
    """Normalize MCP content arrays or raw JSON values into readable text."""

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    return str(content)

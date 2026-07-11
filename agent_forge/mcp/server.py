"""Minimal stdio MCP-style server used by Agent Forge tools.

This module implements the small subset of MCP that matters for this project:
``initialize``, ``tools/list``, and ``tools/call`` over newline-delimited
JSON-RPC. It is intentionally compact, but it is not a fake adapter. The agent
starts it as a separate process, discovers tool schemas, calls tools by name,
and receives MCP-style content blocks.

Why keep a project-owned server instead of only documenting MCP?

* It proves the full control plane: config -> subprocess -> discovery ->
  ToolRegistry -> AgentLoop -> observation.
* It gives protocol-level evidence for external tools without requiring a
  third-party service to be running.
* It keeps the startup path simple: ``python -m agent_forge.mcp.builtin_server``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO


ToolHandler = Callable[[dict[str, Any]], "MCPToolResult | str | dict[str, Any]"]


@dataclass(frozen=True)
class MCPToolResult:
    """Result returned by one MCP tool handler.

    ``content`` follows the MCP convention of a list of content blocks. Agent
    Forge currently consumes text blocks, but keeping the list shape makes the
    boundary compatible with richer results later.

    ``is_error`` is distinct from JSON-RPC errors. A JSON-RPC error means the
    protocol request was invalid; ``is_error=True`` means the tool ran and
    produced a failed observation the agent can reason about.
    """

    content: list[dict[str, Any]]
    is_error: bool = False

    @classmethod
    def text(cls, text: str, *, is_error: bool = False) -> "MCPToolResult":
        """Create a text-only MCP tool result."""

        return cls(content=[{"type": "text", "text": text}], is_error=is_error)


@dataclass(frozen=True)
class MCPToolDefinition:
    """Tool schema and handler exposed by the built-in MCP server."""

    # Short remote name. The client may prefix it as ``server.tool`` locally.
    name: str

    # Human/model-facing description. This is part of tool routing quality.
    description: str

    # JSON Schema object used by the model to form arguments.
    input_schema: dict[str, Any]

    # Python handler that runs inside the MCP server process.
    handler: ToolHandler = field(repr=False)

    def to_mcp_schema(self) -> dict[str, Any]:
        """Return the schema shape expected by ``tools/list`` clients."""

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class AgentForgeMCPServer:
    """Line-oriented JSON-RPC server for built-in Agent Forge MCP tools."""

    def __init__(self, tools: list[MCPToolDefinition], *, name: str = "agent-forge-mcp"):
        """Index tool definitions by name for fast ``tools/call`` dispatch."""

        self.name = name
        self.tools = {tool.name: tool for tool in tools}

    def run(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        """Serve JSON-RPC requests until stdin closes.

        The stdio client starts a fresh process per request group. That short
        lifetime is deliberate: a broken external tool server cannot leak state
        across agent runs or hang the main process indefinitely.
        """

        in_stream = stdin or sys.stdin
        out_stream = stdout or sys.stdout
        for line in in_stream:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                response = self.handle_request(request)
            except Exception as exc:
                response = self._error_response(None, -32700, f"parse failure: {exc}")
            out_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            out_stream.flush()

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Dispatch one JSON-RPC request."""

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "initialize":
            return self._result_response(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": self.name, "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return self._result_response(
                request_id,
                {"tools": [tool.to_mcp_schema() for tool in self.tools.values()]},
            )
        if method == "tools/call":
            return self._handle_tool_call(request_id, params)
        return self._error_response(request_id, -32601, f"unsupported method: {method}")

    def _handle_tool_call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Run one registered tool and package the handler output."""

        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error_response(request_id, -32602, "tool arguments must be an object")
        tool = self.tools.get(name)
        if tool is None:
            return self._error_response(request_id, -32602, f"unknown tool: {name}")
        try:
            result = self._normalize_tool_result(tool.handler(arguments))
        except Exception as exc:
            # Tool failures become observations, not protocol crashes. The
            # optional debug flag is useful during local MCP-server development
            # without dumping stack traces into normal agent traces.
            message = f"tool failed: {exc}"
            if os.getenv("AGENT_FORGE_MCP_DEBUG"):
                raise
            result = MCPToolResult.text(message, is_error=True)
        payload: dict[str, Any] = {"content": result.content}
        if result.is_error:
            payload["isError"] = True
        return self._result_response(request_id, payload)

    def _normalize_tool_result(self, raw: MCPToolResult | str | dict[str, Any]) -> MCPToolResult:
        """Accept friendly handler return values while preserving MCP shape."""

        if isinstance(raw, MCPToolResult):
            return raw
        if isinstance(raw, str):
            return MCPToolResult.text(raw)
        return MCPToolResult.text(json.dumps(raw, ensure_ascii=False, sort_keys=True))

    def _result_response(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        """Build a JSON-RPC success response."""

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        """Build a JSON-RPC protocol error response."""

        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

ToolHandler = Callable[[dict[str, Any]], "MCPToolResult | str | dict[str, Any]"]


@dataclass(frozen=True)
class MCPToolResult:

    content: list[dict[str, Any]]
    is_error: bool = False

    @classmethod
    def text(cls, text: str, *, is_error: bool = False) -> "MCPToolResult":

        return cls(content=[{"type": "text", "text": text}], is_error=is_error)


@dataclass(frozen=True)
class MCPToolDefinition:

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler = field(repr=False)

    def to_mcp_schema(self) -> dict[str, Any]:

        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class AgentForgeMCPServer:

    def __init__(self, tools: list[MCPToolDefinition], *, name: str = "agent-forge-mcp") -> None:

        self.name = name
        self.tools = {tool.name: tool for tool in tools}

    # 主要入口：运行 stdio JSON-RPC 循环并只分发已注册 MCP 方法。
    def run(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        """运行 stdio JSON-RPC 读取、分发和响应循环。"""

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

            message = f"tool failed: {exc}"
            if os.getenv("AGENT_FORGE_MCP_DEBUG"):
                raise
            result = MCPToolResult.text(message, is_error=True)
        payload: dict[str, Any] = {"content": result.content}
        if result.is_error:
            payload["isError"] = True
        return self._result_response(request_id, payload)

    def _normalize_tool_result(self, raw: MCPToolResult | str | dict[str, Any]) -> MCPToolResult:

        if isinstance(raw, MCPToolResult):
            return raw
        if isinstance(raw, str):
            return MCPToolResult.text(raw)
        return MCPToolResult.text(json.dumps(raw, ensure_ascii=False, sort_keys=True))

    def _result_response(self, request_id: Any, result: dict[str, Any]) -> dict[str, Any]:

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_response(self, request_id: Any, code: int, message: str) -> dict[str, Any]:

        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

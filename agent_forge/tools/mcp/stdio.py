"""短生命周期 MCP stdio JSON-RPC transport 与 Tool Adapter。

系统角色：为 discovery/call 启动进程、发送 initialize + request、解析 line-delimited 或
Content-Length response，再把远端结果归一化为 Observation。
输入：ServerSpec/method/arguments；输出：JSON-RPC result 或 Tool Observation。
相邻边界：Config Loader 控制注册/allowlist；Runtime Tool Governance 控制可见与授权；
本文件只负责 transport。

折叠导航：1 server contract；2 session calls；3 response framing；4 Tool adapter/content。
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from agent_forge.runtime.domain.conversation import Observation
from agent_forge.tools.base import Tool


# region 1. Server 契约
@dataclass(frozen=True)
class MCPStdioServerSpec:

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    prefix_tool_names: bool = True
# endregion 1. Server contract 结束


class MCPStdioClient:

    def __init__(self, spec: MCPStdioServerSpec) -> None:

        self.spec = spec

# region 2. 短生命周期 session 调用
    def discover_tools(self) -> list[dict[str, Any]]:
        """启动短生命周期 stdio 会话，依次调用 ``initialize`` 和 ``tools/list``。

        这里只读取带名称的远端 Tool 描述；注册、本轮可见性和权限仍由上层 Runtime 决定。
        """

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
        """启动短生命周期 stdio 会话，初始化后发送一次 ``tools/call``。

        本方法只负责 JSON-RPC 传输并返回远端结果，不替代 Runtime 的参数校验、授权或脱敏。
        """

        response = self._session_call(
            [
                ("initialize", {"clientInfo": {"name": "agent-forge", "version": "schema"}, "capabilities": {}}),
                ("tools/call", {"name": tool_name, "arguments": arguments or {}}),
            ]
        )
        return response.get("result") or {}

    def _session_call(self, calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:

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
            # 同一短会话内按 request id 串行发送；error 立即结束，不继续后续 call。
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
    # endregion 2. Short-lived session calls 结束

    # region 3. Response framing：兼容 Content-Length 与一行 JSON
    def _read_response(self, proc: subprocess.Popen, request_id: int) -> dict[str, Any]:

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

        for header in headers:
            if header.lower().startswith("content-length:"):
                try:
                    return int(header.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None
    # endregion 3. Response framing 结束


# region 4. Remote Tool adapter 与 content projection
class MCPStdioTool(Tool):

    def __init__(self, client: MCPStdioClient, local_name: str, remote_name: str, spec: dict[str, Any]) -> None:

        self.client = client
        self.name = local_name
        self.remote_name = remote_name
        self.description = str(spec.get("description") or f"External tool {local_name}")
        self.input_schema = spec.get("inputSchema") or spec.get("input_schema") or {"type": "object", "properties": {}}

    def schema(self) -> dict:

        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.input_schema.get("properties", {}),
            "required": self.input_schema.get("required", []),
            "metadata": {"source": "mcp_stdio", "remote_name": self.remote_name},
        }

    def execute(self, arguments: dict) -> Observation:

        try:
            result = self.client.call_tool(self.remote_name, arguments or {})
        except Exception as exc:
            return Observation(self.name, False, f"mcp stdio call failed: {exc}")
        if result.get("isError"):
            return Observation(self.name, False, _content_to_text(result.get("content")))
        return Observation(self.name, True, _content_to_text(result.get("content") or result))


def _content_to_text(content: Any) -> str:

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
# endregion 4. Tool adapter/content 结束

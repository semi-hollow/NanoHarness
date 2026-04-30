# 19-how-mcp-style-tool-adapter-works

MCP-style adapter 的目标是让外部工具进入 Agent Forge 的统一 ToolRegistry，而不是让 agent loop 关心工具来源。

阅读顺序：

1. `MCPStyleToolSpec`：描述工具名、说明和 input_schema。
2. `ToolAdapter`：抽象的 `to_tool()`。
3. `MCPStyleToolAdapter`：把 handler 包装成 Agent Forge `Tool`。
4. `tests/test_tool_adapter.py`：看如何注册和执行 mock external tool。

边界：这不是完整 MCP 协议，不包含 transport、server lifecycle 或 JSON-RPC session。

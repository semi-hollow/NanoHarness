# 21-mcp-style-tool-adapter

V2 新增 `agent_forge/tools/adapters/mcp_style_adapter.py`。它是一个本地 tool adapter 设计，不是完整 MCP 协议实现。

## 包含什么

- `ToolAdapter`：抽象类，定义 `to_tool()`。
- `MCPStyleToolSpec`：本地 spec，包含 name、description、input_schema。
- `MCPStyleToolAdapter`：把 mock external tool 包成 Agent Forge `Tool`。

## 能做什么

Adapter 生成的 tool 可以注册进 `ToolRegistry`：

```python
spec = MCPStyleToolSpec(
    name="mock_lookup",
    description="mock external lookup",
    input_schema={"properties": {"query": {"type": "string"}}, "required": ["query"]},
)
tool = MCPStyleToolAdapter(spec, lambda args: "found:" + args["query"]).to_tool()
registry.register(tool)
```

执行结果仍是统一的 `Observation`，因此 agent loop 不需要知道工具来自内置实现还是外部 adapter。

## 不是什么

它不处理 MCP transport、server lifecycle、capabilities negotiation、resource subscription 或 JSON-RPC session。V2 的目标是先把“外部工具如何进入 Agent Forge 的 tool schema 和 execution contract”讲清楚。

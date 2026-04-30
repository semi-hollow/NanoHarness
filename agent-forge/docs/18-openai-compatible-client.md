# 18-openai-compatible-client

V2 新增 `OpenAICompatibleLLMClient`，用于连接实现 OpenAI chat completions 形态的模型网关或兼容服务。它只使用 Python 标准库的 `urllib` 和 `json`，不强依赖第三方 SDK。

## 环境变量

- `AGENT_FORGE_BASE_URL`：例如 `https://gateway.example.com/v1`
- `AGENT_FORGE_API_KEY`：网关 key
- `AGENT_FORGE_MODEL`：模型名

不设置这些变量时，默认 demo 仍使用 `MockLLMClient`。显式传 `--llm openai` 但环境变量不完整时，CLI 会回退到 MockLLMClient。

## Tool Call 解析

客户端支持常见响应：

- `choices[0].message.content`
- `choices[0].message.tool_calls[].function.name`
- `choices[0].message.tool_calls[].function.arguments`
- legacy `message.function_call`

`arguments` 可以是 JSON 字符串或 object。解析失败不会抛到 agent loop 外层，而是返回：

```python
AgentResponse(content=None, tool_calls=[], error={"type": "invalid_response", ...})
```

## 设计边界

这不是完整 provider SDK。V2 只证明三件事：可选真实 LLM、tool call parse、invalid response recovery。生产环境建议放到 model gateway 后面统一做 auth、routing、rate limit、fallback、audit 和 cost。

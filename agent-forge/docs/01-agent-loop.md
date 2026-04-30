# 01-agent-loop

Agent Loop 是把 LLM 变成执行系统的核心。

```text
task -> context_assembly -> plan -> llm_call -> action -> tool_call -> observation -> next step/final
```

## 核心对象

- `Message`：用户、assistant、tool 的消息。
- `ToolCall`：模型要求执行的工具名和参数。
- `Observation`：工具执行结果。
- `AgentState`：task、workspace、iteration、messages、observations、status。

## 为什么不是普通 ChatGPT

普通聊天只生成文本。Agent Loop 会把模型输出变成受控动作，并把真实工具结果回传给下一轮。

## 防失控机制

- `max_steps` 防无限循环；
- repeated tool call 检测防 doom loop；
- invalid LLM response 结构化返回；
- output guardrail 防虚假成功；
- trace 记录每一步。

代码证据：`agent_forge/runtime/agent_loop.py`。

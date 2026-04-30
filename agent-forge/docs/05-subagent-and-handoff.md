# 05-subagent-and-handoff

Handoff 是 agent 之间的责任交接，不是工具调用。

```python
Handoff(
    from_agent="SupervisorAgent",
    to_agent="CodingAgent",
    reason="implement_plan",
    payload={...},
)
```

## Payload 包含什么

- phase
- task
- relevant_files
- modified_files
- test_result
- review_result
- retry_count

## 为什么要 trace

如果 multi-agent 失败，需要知道是谁把任务交给谁、交接时带了什么状态、失败发生在哪个阶段。

代码证据：

- `agent_forge/agents/handoff.py`
- `agent_forge/agents/supervisor_agent.py`
- `tests/test_handoff.py`

## 面试怎么讲

Tool call 是对外部能力的调用；handoff 是角色之间的控制流和状态传递。二者都要可追踪，但语义不同。

# 02-workflow-vs-agent

Workflow 和 Agent 都能完成任务，但适用场景不同。

## Workflow

固定路径：

```text
plan -> code -> test -> review -> final
```

优点是稳定、可预测、好测试。缺点是不适合开放任务。

代码：`agent_forge/workflows/coding_workflow.py`。

## Agent

动态路径：

```text
LLM decides next tool or final answer
```

优点是灵活。缺点是需要安全边界、max steps、trace 和 eval。

代码：`agent_forge/runtime/agent_loop.py`。

## 面试怎么讲

生产里经常混用：大阶段用 workflow 管住，阶段内用 agent 处理不确定性。Agent Forge 保留两种模式，就是为了展示这个 trade-off。

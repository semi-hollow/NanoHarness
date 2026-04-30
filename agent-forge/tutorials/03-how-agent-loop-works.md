# 03-how-agent-loop-works

## 1. 这篇解决什么问题
解释 Agent Forge 如何从任务走到最终答案。

## 2. 先给结论
AgentLoop 每轮做 context assembly、plan summary、llm call、tool action、observation，然后决定继续或停止。

## 3. 最小概念
ReAct 在工程里不是暴露长 chain-of-thought，而是记录结构化 `plan -> action -> observation`。

## 4. 对应代码在哪里
`agent_forge/runtime/agent_loop.py`、`planner.py`、`state.py`、`stop_condition.py`。

## 5. 运行一下看效果
运行 single demo 后看 `agent_forge_trace.json`，确认有 `context_assembly`、`plan`、`action`、`observation`。

## 6. 常见坑
没有 max_steps 和 repeated tool call 检测，Agent 可能进入 doom loop。

## 7. 面试怎么说
我把 Agent 设计成受控执行循环，而不是一次性问答；每一步都有 trace 证据。

## 8. 下一步学什么
读 `04-how-tool-calling-works.md`。

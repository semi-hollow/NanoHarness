# 08-how-handoff-works

## 1. 这篇解决什么问题
解释 handoff 和 tool call 的区别。

## 2. 先给结论
Tool call 是执行动作；handoff 是角色之间的责任交接。

## 3. 最小概念
Handoff 包含 `from_agent`、`to_agent`、`reason`、`payload`。

## 4. 对应代码在哪里
`agent_forge/agents/handoff.py` 和 `supervisor_agent.py` 的 `_handoff()`。

## 5. 运行一下看效果
multi demo 的 trace 里有 `event_type=handoff`，payload 包含 phase、task、files、test_result。

## 6. 常见坑
payload 太薄会让下游 agent 缺上下文；payload 太大又会制造噪音。

## 7. 面试怎么说
我把 handoff 做成 traceable event，这样能排查责任交接和失败位置。

## 8. 下一步学什么
读 `09-how-context-builder-works.md`。

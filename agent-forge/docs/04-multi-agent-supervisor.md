# 04-multi-agent-supervisor

多 Agent 不是为了炫技，而是为了职责分离。

```text
SupervisorAgent
  -> PlannerAgent
  -> CodingAgent
  -> TesterAgent
  -> ReviewerAgent
```

## Supervisor 为什么存在

Supervisor 负责状态机和 handoff，不让 subagent 互相乱调。V2 中它使用 `TaskPhase`：

- planning
- coding
- testing
- reviewing
- done / failed

代码：`agent_forge/agents/supervisor_agent.py`。

## Subagent 分工

- Planner：产出计划，不改文件。
- Coding：读代码、patch。
- Tester：跑 unittest，总结结果。
- Reviewer：看 diff/test result，给 review 结论。

## 风险

多 Agent 会增加延迟、状态同步成本和责任模糊。简单任务不应该强行多 Agent。

面试讲法：我用多 Agent 展示 handoff 和 role boundary，但保留 workflow/single-agent 说明并非所有任务都需要多 Agent。

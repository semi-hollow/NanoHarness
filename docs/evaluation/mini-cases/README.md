# Mini Agent 应用案例

这些 case 刻意保持小型。它们不是 benchmark leaderboard，也不能替代 SWE-bench
形态的 Coding Agent evidence。

它们覆盖更广泛的 Agent application engineering 问题：

- Research workflow 需要 citation quality、source limitation 和 unsupported claim
  control。
- Ops workflow 需要 policy-sensitive side effect、human approval、recovery 和可审计
  execution summary。

每个 JSON case 使用 `task_success`、`evidence_quality`、`tool_efficiency`、
`recovery_success`、`human_intervention_count`、`safety_violation` 等通用指标。

使用显式 evidence 运行确定性 scorecard：

```bash
forge eval mini-cases --case research-citation-quality --evidence evidence.json
```

Evaluator 不是 LLM judge。它接收 produced artifact、citation、unsupported claim
count、tool-call count、human intervention count、recovery result 和 safety violation
等证据。证据缺失或过弱时，对应维度直接失败，不会用描述性文字掩盖。

Mini-case 中的 tool name 是 declarative evaluation input，evaluator 不执行 AgentLoop。
真实 runtime 中，`ask_human` 通过 `HumanInputRepository` 创建持久化信息请求；具体副作用
审批仍由独立 `ApprovalRepository` 和 `forge approve` 契约负责。

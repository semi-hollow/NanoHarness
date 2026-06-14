# 06 Technical Question Coverage

这份文件不再逐题堆答案，而是按主题告诉你：项目已经覆盖什么、代码在哪里、回答边界是什么。

| theme | coverage | code evidence | answer focus |
|---|---|---|---|
| Agent 架构 | 强 | `cli.py`, `agent_loop.py`, `runtime/`, `tools/`, `context/` | runtime core 分层：context、model、tool、control、trace。 |
| ReAct / Planning | 强 | `AgentLoop`, `PlanningModePolicy`, `StepController` | ReAct 适合观察驱动；workflow 适合确定性链路。 |
| Tool Calling | 强 | `ToolRegistry`, `ToolRouter`, `mcp_config.py`, `mcp_stdio.py` | tool 是治理系统，不只是 function call。 |
| Permission / Safety | 强 | `hooks.py`, `execution_environment.py`, `permission.py`, `command_policy.py`, `sandbox.py` | approval mode、worktree、network deny、path boundary、command allowlist。 |
| Memory / Context | 强 | `context_strategy.py`, `memory.py`, `memory_policy.py`, `task_state.py` | 短期、summary、session seed、topic relation、checkpoint。 |
| Observability | 强 | `trace.py`, `usage_report.py`, `metrics.py`, `evidence.py` | trace 是事实源，usage report 是工程视角。 |
| Eval / Flywheel | 中强 | `eval_runner.py`, `eval_history.py`, `flywheel.py` | local regression、capability breakdown、history diff。 |
| Multi-Agent | 中强 | `SupervisorAgent`, `AgentRuntime`, `TaskGraph`, `AgentSpec` | 主子结构、artifact handoff、role allowlist、Supervisor 验证。 |
| Review / Git workflow | 中 | `review_workflow.py`, `git_diff.py`, `git_status.py` | deterministic review gate；不是完整 GitHub PR 产品。 |
| RAG / GraphRAG | 边界覆盖 | `rag.py`, `file_ranker.py`, `repo_map.py` | 本仓库是 code retrieval；大规模知识库另做平台。 |
| ToC 对话产品 | 边界覆盖 | `ClarificationPolicy`, `ContextStrategy` | session 连贯性和话题切换可讲，产品指标不编造。 |
| 多模态 | 不进核心 | 无 | 可作为工具/异步任务扩展，不污染 coding runtime。 |
| 模型训练 | 不进核心 | 无 | SFT/RL 属于模型层；本仓库关注 runtime。 |

## 必须能讲清的 12 个点

1. 为什么 `single` 是主路径，`workflow` 只是 baseline。
2. 为什么 context 不是简单 prompt 拼接。
3. ToolRouter 如何避免上百工具全塞给模型。
4. ToolRegistry 如何把 tool error 变成 Observation。
5. HookManager 为什么比 prompt safety 更可靠。
6. ApprovalMode 如何支持个人电脑、公司环境、dry-run。
7. ExecutionEnvironment 为什么需要 local/worktree 两种模式。
8. TaskStateStore 和 TraceRecorder 的职责区别。
9. StepController 如何处理重复调用、失败分类、预算。
10. Multi-agent 中 Supervisor 为什么不信子 agent 文本。
11. usage_report 如何量化 token、cost、latency、context、tool success。
12. EvalHistory 如何发现回归，而不是只看单次通过。

## 边界回答模板

当问题超出本仓库范围时，用这个结构：

1. 先承认该方向重要。
2. 说明它属于哪一层：产品交互、云平台、知识平台、模型训练、业务系统。
3. 说明本仓库保留了哪个接口或扩展点。
4. 不编造没有实现的线上指标。

例子：

> GraphRAG 对复杂关联查询很重要，但本仓库是 coding runtime core，不是知识平台。
> 目前实现 repo map、file ranker、lexical retrieval 和 evidence grounding。生产扩展时，我会把
> ContextStrategy 的 retrieval 层替换成 BM25 + vector + graph expansion + reranker，
> AgentLoop 和 ToolRegistry 不需要改。

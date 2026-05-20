# 06 面试题覆盖矩阵

这份文档对应你整理的 AI Agent Engineer 面试题。它不是把所有问题都塞进代码里，而是说明：

- 哪些问题已经被 Agent Forge 的代码直接覆盖。
- 哪些问题只做了轻量实现，可以用项目发散回答。
- 哪些问题不属于这个 CodingAgent 项目，应该作为延展知识回答。

标记说明：

- `代码覆盖`：项目里有明确模块或运行路径支撑。
- `部分覆盖`：项目体现了核心思想，但没有做完整工业系统。
- `文档延展`：不适合写进代码，但面试要能讲。
- `不覆盖`：和本项目目标不直接相关。

## 1. Agent 架构与项目设计

覆盖情况：

- 代码覆盖：1, 2, 3, 4, 5, 7, 10, 11, 13, 14, 17, 18, 19, 20
- 部分覆盖：6, 8, 9, 12, 15, 16

项目证据：

- `agent_forge/cli.py`：模式分发和依赖组装。
- `agent_forge/runtime/agent_loop.py`：核心 AgentLoop。
- `agent_forge/context/context_strategy.py`：上下文工程。
- `agent_forge/tools/registry.py`：工具协议和治理。
- `agent_forge/safety/`：安全、权限、沙箱。
- `agent_forge/agents/supervisor_agent.py`：supervisor multi-agent。
- `agent_forge/workflows/task_graph.py`：任务图和调度。

回答模板：

> 我的项目不是单纯调用 LLM，而是把 LLM 放进一个可控执行系统。架构分为 CLI 入口、上下文工程、模型网关、AgentLoop、工具注册表、安全权限、trace/session、multi-agent supervisor。Workflow 适合确定性强的节点，ReAct 适合需要观察环境、读文件、修复失败的节点。我的设计重点是让模型负责推理，runtime 负责边界、状态、工具、预算和审计。

容易被追问：

- 为什么不是完全自主 Agent？

回答：

> Coding 任务有真实副作用，完全自主会带来越权、误删、无限循环和不可审计问题。所以我用 bounded autonomy：模型可以在 AgentLoop 内选择工具和恢复路径，但权限、预算、工具 schema、sandbox、输出校验由 runtime 控制。

## 2. Planning、ReAct 与推理控制

覆盖情况：

- 代码覆盖：1, 2, 3, 4, 8, 11, 12, 13, 14, 15, 17, 18
- 部分覆盖：5, 6, 9, 10, 16
- 文档延展：7

项目证据：

- `agent_forge/runtime/agent_loop.py`：plan -> llm_call -> tool_call -> observation -> recovery -> final。
- `agent_forge/runtime/planner.py`：trace-only planning summary。
- `agent_forge/runtime/control.py`：重复动作检测、失败分类、预算停止。
- `agent_forge/context/context_strategy.py`：attention sink 和上下文压缩。

回答模板：

> ReAct 在工程上不是让模型无限思考，而是标准化一个循环：上下文组装、模型输出 tool call、runtime 执行工具、observation 回写、下一轮再决策。我的实现把推理控制放在 StepController：它判断重复工具调用、patch mismatch、命令失败、权限拒绝、超时、max steps。Reflection 不是每轮都触发，而是由失败信号触发 recovery_decision。

本项目不做完整 MCTS / ToT 的原因：

> MCTS 和 ToT 会显著增加分支数、延迟和成本。对 coding agent，我优先实现 ReAct + plan-execute + failure recovery。只有在高价值、强不确定任务中才考虑树搜索，并且要配预算、剪枝和 verifier。

## 3. Tool Calling、协议与工具治理

覆盖情况：

- 代码覆盖：1, 2, 3, 4, 5, 7, 8, 9
- 部分覆盖：6, 10, 11, 12, 13, 15
- 文档延展：14

项目证据：

- `agent_forge/runtime/message.py`：assistant tool_calls + tool role observation。
- `agent_forge/tools/registry.py`：schema validation。
- `agent_forge/tools/adapters/mcp_style_adapter.py`：MCP-style adapter 雏形。
- `agent_forge/runtime/agent_runtime.py`：不同 agent 的工具 allowlist。
- `agent_forge/safety/permission.py`：工具权限决策。

回答模板：

> tool call 不能直接执行。模型输出 tool name 和 arguments 后，runtime 先检查工具是否存在、参数是否符合 schema、角色是否有权限、命令是否安全，然后执行工具并把 Observation 作为 tool role 消息回到下一轮。工具很多时应该做 tool routing：先按任务召回候选工具，再只把候选 schema 给模型，避免 100 个工具全部塞进上下文。

本项目没有完整 MCP/A2A 的原因：

> 这个项目保留 ToolRegistry 和 MCP-style adapter，用来说明协议边界。完整 MCP 是跨进程工具发现和调用，A2A 是 agent 间通信协议，属于生态接入层。我现在重点放在 coding agent runtime，不把生态复杂度塞进核心代码。

## 4. Multi-Agent 与编排

覆盖情况：

- 代码覆盖：1, 2, 3, 5, 8, 9, 10, 11, 12
- 部分覆盖：4, 6, 7
- 文档延展：13, 14

项目证据：

- `agent_forge/agents/supervisor_agent.py`：SupervisorAgent。
- `agent_forge/runtime/agent_spec.py`：角色、工具、文件权限、风险级别。
- `agent_forge/runtime/agent_runtime.py`：每个子 agent 复用 AgentLoop。
- `agent_forge/workflows/task_graph.py`：DAG、依赖、状态、冲突安全 batch。
- `agent_forge/workflows/artifact.py`：TaskArtifact。
- `agent_forge/production/ownership.py`：OwnershipPlan。

回答模板：

> 我采用 supervisor multi-agent，而不是去中心化 agent 聊天。Supervisor 负责任务图、依赖、文件 ownership、artifact contract、retry 和 review gate。子 Agent 不直接把输出交给用户，而是产出 TaskArtifact，由 supervisor 验证测试结果和 review 结果后再汇总。这样能避免 A2A 无限循环、上下文污染和权限失控。

本项目不做去中心化协商的原因：

> 去中心化 multi-agent 适合研究或开放协作，但工程落地风险高：难停、难审计、难分责。我在面试里会强调生产系统优先 supervisor / shared-state / task-graph 模式。

## 5. Agent 执行控制与异常处理

覆盖情况：

- 代码覆盖：1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17
- 部分覆盖：4, 13, 14

项目证据：

- `agent_forge/runtime/control.py`：FailureKind、FailureSignal、ExecutionBudget、StepController。
- `agent_forge/tools/apply_patch.py`：patch mismatch 可恢复。
- `agent_forge/tools/run_command.py`：命令失败 observation。
- `agent_forge/runtime/session.py`：session resume / rollback。
- `agent_forge/production/diff_tracker.py`：diff 和 rollback bundle。

回答模板：

> 异常处理不能只靠提示词。我把失败分成 unknown tool、invalid arguments、permission denied、patch mismatch、command failed、tool exception、repeated action、model response、budget exceeded。每种失败都有 retryable 和 recovery_hint。比如 patch mismatch 可以 reread 后重试，permission denied 不可绕过，重复相同工具调用直接停止。

支付接口超时这类问题怎么答：

> 这个项目没有支付工具，但原则一样：区分可重试和不可重试，非幂等操作要有 idempotency key、状态查询、补偿事务和审计日志。不能简单重放同一个动作。

## 6. Memory、Context 与 Session 管理

覆盖情况：

- 代码覆盖：1, 2, 3, 5, 6, 7, 8, 9, 11, 12, 13, 17, 18, 19, 20, 23, 24
- 部分覆盖：4, 10, 14, 15, 21, 22
- 文档延展：16

项目证据：

- `agent_forge/context/memory.py`：short-term memory、summary memory、session seed。
- `agent_forge/context/context_strategy.py`：topic_relation、inherit_session、attention_sink、budget_breakdown。
- `agent_forge/runtime/session.py`：load previous run and resume summary。

回答模板：

> 我把 memory 分成短期 memory、summary memory、session memory。短期 memory 存最近 observations，summary memory 压缩旧观察，session memory 来自上一次 run report。是否加载长期/历史 memory 不是模型随便选，而是 ContextStrategy 根据 topic_relation 判断。上下文超限时保留 attention sink、当前任务、相关文件预览、失败恢复提示，压缩或丢弃旧 memory。

用户频繁切话题怎么答：

> 不能默认继承历史。我的实现会用 topic_relation 判断 same_topic、related_topic、unknown、topic_shift。topic_shift 时不继承旧 memory，避免把上一题的上下文污染当前任务。

## 7. RAG、GraphRAG 与知识检索

覆盖情况：

- 部分覆盖：1, 2, 3, 4, 5, 9, 10, 11, 12, 13, 14, 17
- 文档延展：6, 7, 8, 15, 16, 18, 19, 20, 21, 22, 23, 24
- 不覆盖：25, 26, 27, 28, 31, 32, 33, 34

项目证据：

- `agent_forge/context/rag.py`：轻量 lexical retrieval。
- `agent_forge/context/file_ranker.py`：文件排序。
- `agent_forge/context/context_strategy.py`：selected_files、retrieved_docs、file_previews。

回答模板：

> 本项目不是知识库 RAG 产品，只实现 coding context retrieval。它用 repo map、文件排序、关键词召回和代码预览解决“让 agent 找到相关代码”的问题。生产 RAG 我会扩展为文档解析、结构化 chunk、metadata、BM25 + vector hybrid、rerank、版本/有效期、生成后校验。GraphRAG 适合多跳实体关系问题，不适合硬塞进这个 coding agent 核心。

## 8. Agent Eval 与迭代

覆盖情况：

- 部分覆盖：1, 2, 3, 7, 8, 9, 10, 13, 14, 15
- 文档延展：4, 5, 6, 11, 12

项目证据：

- `agent_forge/eval/eval_runner.py`：轻量 eval cases。
- `agent_forge/eval/eval_history.py`：eval history JSONL。
- `agent_forge/observability/metrics.py`：tool count、failed tool count、guardrail count、duration。

回答模板：

> 我现在保留轻量 eval，不做 SWE-bench 级评测。指标包括 task_success、test_pass、safety_violation、tool_call_count、failed_tool_call_count、handoff_count、duration、trace_event_count。生产系统会把 badcase 回流到 eval set，并按任务完成率、成本、延迟、安全违规率、可解释性做多维评估。

## 9. Agent 安全、可信与可控性

覆盖情况：

- 代码覆盖：1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 17, 18, 19, 20
- 部分覆盖：11, 12, 15, 16

项目证据：

- `agent_forge/safety/guardrails.py`：input/tool/output guardrail。
- `agent_forge/safety/permission.py`：allow/ask/deny。
- `agent_forge/safety/command_policy.py`：危险命令拦截。
- `agent_forge/safety/sandbox.py`：workspace path boundary。
- `agent_forge/observability/trace.py`：审计 trace。

回答模板：

> 安全不是靠模型自觉。我把安全拆成输入层、工具层、权限层、sandbox 层、输出层和审计层。高风险操作走 ASK 或 DENY，工具执行必须在 workspace sandbox 内，输出不能声称未执行的测试。动态规则可以进入 PermissionPolicy 或 CommandPolicy，而不是散落在 prompt。

电商强事实场景怎么答：

> 本项目不是电商导购，但原则相同：事实性节点用规则/API/数据库校验，模型负责理解和生成；资格、价格、库存、权益这类强事实不能让模型自由编造。

## 10. C 端对话 Agent 产品工程

覆盖情况：

- 部分覆盖：1, 2, 3, 4, 6, 7, 8, 9, 10, 11
- 不覆盖：5, 12

项目证据：

- `context_strategy.py` 的 topic shift 机制可迁移到 C 端 session 连贯性。
- `session.py` 的 resume/report 可迁移到异步长任务。
- `ModelGateway` 的 provider/model 可回答“为什么选择当前模型”。

回答模板：

> C 端对话 Agent 的关键是 session state、topic shift、异步任务、进度反馈、失败恢复和成本控制。我的项目只实现了 runtime 侧的 session/context 机制，没有做 UI 和多模态产品体验。长耗时任务应该异步化，前端展示进度，失败后给可恢复状态和重试入口。

## 11. Agent Infra / 工程落地

覆盖情况：

- 代码覆盖：2, 4, 6, 7, 8, 9, 10, 13, 14
- 部分覆盖：1, 3, 5, 11, 12

项目证据：

- `runtime/session.py`：状态管理、断点续跑。
- `tools/registry.py`：工具网关。
- `safety/sandbox.py`：轻量 sandbox。
- `observability/trace.py` + `metrics.py`：tracing 和运行观测。
- `agents/handoff.py`：handoff log。

回答模板：

> Agent Infra 要把运行状态、工具调用、handoff、trace、metrics、session、rollback 都结构化。我的项目用本地 JSON/JSONL 模拟生产数据库和日志系统，重点是证明边界和数据模型。真正线上会接任务队列、DB、对象存储、分布式 trace 和告警。

## 12. 多模态 Agent

覆盖情况：

- 不覆盖：1, 2, 3, 4, 5

回答模板：

> 这个项目是 text/code-only CodingAgent，不做多模态。多模态 Agent 会多出视觉编码、图像/video token、媒体存储、异步生成、进度和审核链路。但 AgentLoop、工具治理、session、trace、安全边界这些控制面思想可以复用。

## 13. Agent 训练与对齐

覆盖情况：

- 不覆盖：1, 2, 3, 4, 5, 6

回答模板：

> 这个项目是 runtime，不是模型训练项目。Agentic CPT/SFT/RL 解决的是模型能力和对齐，runtime 解决的是上下文、工具、权限、状态、恢复和审计。训练 tool-call 轨迹时，observation token 通常不作为 assistant 目标去学，所以 SFT 要 mask observation，只学习 assistant 的 reasoning/action/final 格式。

## 最终面试定位

你可以这样收束：

> 我没有把所有 AI Agent 话题都塞进一个项目。这个项目聚焦 CodingAgent runtime，覆盖架构、ReAct、工具治理、上下文工程、memory/session、异常恢复、multi-agent 编排、安全审计和轻量 eval。RAG、GraphRAG、多模态、训练、C 端产品体验是延展知识，我能讲设计，但不会污染这个项目的核心代码。

# 06 面试题覆盖矩阵

这份文档回答一个问题：你整理的面试题，哪些能用 Agent Forge 这个项目直接支撑，哪些只能从项目发散，哪些不应该塞进项目代码。

标记说明：

- `代码覆盖`：项目里有明确模块、运行路径或 trace 证据。
- `部分覆盖`：项目体现了核心思想，但没有做完整工业系统。
- `文档延展`：不适合写进 CodingAgent 代码，但面试要能讲。
- `不覆盖`：和本项目定位不直接相关，只作为背景知识准备。

## 项目证据索引

- `agent_forge/cli.py`：入口、模式分发、模型/session/trace 组装。
- `agent_forge/runtime/agent_loop.py`：ReAct 主循环。
- `agent_forge/context/context_strategy.py`：上下文工程、topic shift、attention sink。
- `agent_forge/tools/registry.py`：tool schema、工具执行边界。
- `agent_forge/safety/`：权限、审批、命令策略、sandbox、guardrail。
- `agent_forge/runtime/control.py`：失败分类、重试、预算、循环控制。
- `agent_forge/runtime/session.py`：session、resume、rollback。
- `agent_forge/agents/supervisor_agent.py`：supervisor multi-agent。
- `agent_forge/workflows/task_graph.py`：任务图、依赖、冲突安全调度。
- `agent_forge/observability/trace.py`：trace、审计、回放证据。
- `agent_forge/eval/eval_runner.py`：轻量 eval。

## 1. Agent 架构与项目设计

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | 介绍一个 AI Agent 项目，整体架构怎么设计？ | 代码覆盖 | `cli -> AgentLoop -> Context/Model/Tools/Safety/Trace` 是主线。 |
| 2 | 为什么选择 Workflow，而不是完全自主决策的 Agent？ | 代码覆盖 | `workflow` 是确定性 baseline，`single` 是 ReAct，对比明显。 |
| 3 | Workflow 里哪些节点需要 ReAct 能力？ | 代码覆盖 | 读文件、修复失败、重试验证这类节点需要 AgentLoop。 |
| 4 | Tools、Workflow、Agent 的本质区别是什么？ | 代码覆盖 | tools 是动作，workflow 是固定流程，agent 是观察驱动决策循环。 |
| 5 | 什么场景适合规则链路，什么场景适合大模型？ | 代码覆盖 | 权限/预算/命令策略用规则，代码理解和下一步动作由模型。 |
| 6 | 当前阻碍 Agent 大规模落地的最大挑战是什么？ | 部分覆盖 | 项目体现安全、可控、eval、成本、上下文，但非线上系统。 |
| 7 | 如何平衡 Agent 的可控性和能力？ | 代码覆盖 | bounded autonomy：模型决策，runtime 控制边界。 |
| 8 | 长链路 Agent 为什么能接受十几分钟耗时？ | 部分覆盖 | session/trace/report 支持长任务解释，但项目未做异步产品体验。 |
| 9 | 不同业务场景下，延迟和效果如何取舍？ | 部分覆盖 | ModelGateway/metrics 有成本延迟位置，但未做真实路由策略。 |
| 10 | 你最突出的两个架构设计是什么？为什么？ | 代码覆盖 | 上下文工程和工具/执行控制是最强叙事点。 |
| 11 | 你的 Agent 项目为什么做出现在这些决策？ | 代码覆盖 | study-pack 和代码注释已解释设计边界。 |
| 12 | 如何看待 Manus / OpenClaw 这类 Agent 项目的技术落地？ | 部分覆盖 | 可用本项目对比：runtime/control/eval 是落地关键。 |
| 13 | 是否看过 Agent 开源项目源码？核心实现是什么？ | 代码覆盖 | 可对比 Codex/OpenCode/Aider/SWE-agent 的 loop/tool/context。 |
| 14 | Agent 的整体框架通常包含哪些模块？ | 代码覆盖 | context、planner、model、tool、memory、safety、trace、eval。 |
| 15 | 如何设计一个智能导购 Agent？ | 部分覆盖 | Agent 控制面可复用，但导购业务规则/商品系统未实现。 |
| 16 | 智能导购 Agent 中，感知、规划、记忆、执行模块如何协同？ | 部分覆盖 | 可用 Context/AgentLoop/Memory/Tools 类比。 |
| 17 | 开放式任务 Agent 如何权衡“探索新信息”和“利用已有知识”？ | 代码覆盖 | ContextStrategy 体现检索、memory、topic 判断。 |
| 18 | Agent 如何支持长期开放式任务执行？ | 代码覆盖 | session、resume、trace、summary、rollback 是雏形。 |
| 19 | 如何在不降低通用推理能力的前提下嵌入业务规则约束？ | 代码覆盖 | 规则放 runtime policy，不塞进模型权重。 |
| 20 | 复杂 Agent 闭环中，为什么仅靠 RAG 不能彻底解决幻觉？ | 代码覆盖 | 项目同时做工具验证、输出 guardrail、trace，不只 RAG。 |

## 2. Planning、ReAct 与推理控制

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | ReAct 框架的工程实现细节是什么？ | 代码覆盖 | `AgentLoop.run()` 完整实现 context -> LLM -> tool -> observation。 |
| 2 | ReAct 消息格式如何设计？ | 代码覆盖 | `Message` 支持 assistant `tool_calls` 和 tool observation。 |
| 3 | 为什么使用 ReAct？ | 代码覆盖 | 适合需要观察代码/工具结果再决策的 coding task。 |
| 4 | 还知道哪些 Agent 推理模式？ | 代码覆盖 | 文档对比 workflow、ReAct、plan-execute、ToT/MCTS。 |
| 5 | ReWOO 是什么？ | 部分覆盖 | 项目未实现 ReWOO，但可解释为先规划变量化工具链再执行。 |
| 6 | Plan-and-Execute 是什么？ | 部分覆盖 | workflow/multi-agent 体现 plan-execute，但不做复杂 planner。 |
| 7 | 如何用 MCTS 做 Agent planning？ | 文档延展 | 不写入项目，作为高成本规划延展问题。 |
| 8 | 推理-行动循环中，如何纠正逻辑坍缩或无效工具调用？ | 代码覆盖 | `StepController` 做 repeated action 和 failure classification。 |
| 9 | Tree of Thoughts 能否用于线上系统？ | 部分覆盖 | 文档回答成本/延迟/剪枝，不做代码实现。 |
| 10 | 如何平衡 ToT 的成本和效果？ | 部分覆盖 | 可用预算和 verifier 思路回答。 |
| 11 | Agent 反思机制如何设计？ | 代码覆盖 | failure-triggered `recovery_decision` 是克制版 reflection。 |
| 12 | 如何避免 Reflection 过度纠正或陷入循环？ | 代码覆盖 | 最大步数、失败次数、重复调用限制。 |
| 13 | 如何设计有节制、基于置信度阈值的反思触发条件？ | 代码覆盖 | 当前以失败类型触发；可扩展 confidence threshold。 |
| 14 | Agent 出现循环调用或思维死循环时怎么办？ | 代码覆盖 | `max_tool_repeats`、`max_steps`、`max_consecutive_failures`。 |
| 15 | Token 过长导致 attention 稀释，为什么会降低 Agent 指令遵循能力？ | 代码覆盖 | attention sink、context budget、memory summary。 |
| 16 | 标准 Attention 在多轮 Agent 对话中主要带来哪些工程问题？ | 部分覆盖 | 项目体现上下文污染和稀释，但未深入模型结构。 |
| 17 | Attention Sink 是什么？ | 代码覆盖 | `ATTENTION_SINK` 在每轮 context 中保留稳定指令锚点。 |
| 18 | 在上下文不足时，如何在不丢失 Attention Sink 的前提下保持生成连贯性？ | 代码覆盖 | 保留 sink，压缩 memory，截断 repo map/file previews。 |

## 3. Tool Calling、协议与工具治理

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | tool_response 应该用什么角色返回？为什么？ | 代码覆盖 | 用 `tool` role，与 assistant tool_call 通过 id 对齐。 |
| 2 | tool call 和 observation 如何进入下一轮推理？ | 代码覆盖 | AgentLoop append assistant tool_calls + tool observation。 |
| 3 | 工具库有上百个工具时，如何让模型快速选对工具？ | 代码覆盖 | 现有 registry/allowlist 是雏形，可扩展 tool routing。 |
| 4 | tool schema 如何设计才能减少误调用？ | 代码覆盖 | `ToolRegistry` 做 required/type validation。 |
| 5 | 候选工具超过 100 个时，如何设计工具路由策略？ | 代码覆盖 | 当前有 per-agent allowlist；可扩展检索式路由。 |
| 6 | 如何解决工具检索过程中的召回偏差？ | 部分覆盖 | 项目未做工具 embedding/rerank，但有 schema/trace 反馈。 |
| 7 | 多工具高频调用任务中，如何压低端到端延迟？ | 代码覆盖 | TaskScheduler 支持安全并发雏形。 |
| 8 | 多个外部工具存在依赖时，如何设计工具调用依赖图？ | 代码覆盖 | `TaskGraph` 是依赖图抽象。 |
| 9 | 多工具调用如何并行执行以最小化响应延迟？ | 代码覆盖 | `TaskScheduler._conflict_safe_batches()`。 |
| 10 | MCP、A2A、Skills、Function Call 分别是什么？ | 部分覆盖 | MCP-style adapter 有雏形，其余在文档解释。 |
| 11 | MCP、A2A、Skills、Function Call 有什么区别？ | 部分覆盖 | 项目体现 tool/function/MCP adapter，A2A/Skills 不实现。 |
| 12 | MCP 和传统 Agent Skills 有什么区别？ | 部分覆盖 | 用 adapter 解释协议工具，用文档解释 Skill。 |
| 13 | 多智能体环境中，如何动态发现并注册跨协议工具？ | 部分覆盖 | Registry/adapter 是基础，未做动态发现服务。 |
| 14 | A2A 为什么不用现成协议，而要自建一套？ | 文档延展 | 属于协议设计背景，不进本项目代码。 |
| 15 | Skills 和 Tools 的区别是什么？ | 部分覆盖 | Tool 是动作，Skill 是高层能力包；项目只做 Tool/adapter。 |

## 4. Multi-Agent 与编排

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Multi-Agent 系统如何设计三层架构？ | 代码覆盖 | supervisor、runtime worker、tool/context infra 三层。 |
| 2 | Agent 之间如何通信？ | 代码覆盖 | `Handoff`、`TaskArtifact`、shared state。 |
| 3 | Agent 之间怎么编排？ | 代码覆盖 | `TaskGraph` + `TaskScheduler` + Supervisor。 |
| 4 | Agent 框架选型怎么考虑？ | 部分覆盖 | 项目自研轻量 runtime，可发散到 LangGraph/CrewAI/OpenAI Agents。 |
| 5 | shared-state、agent team、主子结构有什么区别？ | 代码覆盖 | 本项目是主子结构 + shared state。 |
| 6 | 如何防止 Multi-Agent 互相 A2A 停不下来？ | 部分覆盖 | 用 supervisor 和 graph 限制，未实现去中心化 A2A。 |
| 7 | 主子模式下，子 Agent 产生幻觉怎么办？ | 部分覆盖 | supervisor 看 trace/tool/test evidence，但无复杂 verifier。 |
| 8 | 子 Agent 的输出是否应该直接给用户？ | 代码覆盖 | 不直接给，先转 artifact，由 supervisor 汇总。 |
| 9 | Supervisor 如何验证子 Agent 输出？ | 代码覆盖 | 通过 tool_observation、test_pass、review gate。 |
| 10 | Agent 之间如何传递上下文和状态？ | 代码覆盖 | Handoff payload、state、TaskArtifact。 |
| 11 | 多 Agent 协作中，不同 Agent 的记忆如何隔离与共享？ | 代码覆盖 | 每个 AgentRuntime 独立 loop，共享 supervisor state。 |
| 12 | 如何避免不同工具或 Agent 之间的上下文污染？ | 代码覆盖 | AgentSpec allowlist、ContextStrategy、artifact contract。 |
| 13 | 去中心化 Multi-Agent 中，如何设计任务分配协议？ | 文档延展 | 不属于当前 supervisor 方案。 |
| 14 | Agent 如何根据自身能力和当前负载自主协商并接管子任务？ | 文档延展 | 当前不做自主协商。 |

## 5. Agent 执行控制与异常处理

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Agent 出现死循环怎么办？ | 代码覆盖 | `max_steps`、`max_tool_repeats`、failure budget。 |
| 2 | Agent 异常处理机制如何设计？ | 代码覆盖 | `FailureKind` + `FailureSignal`。 |
| 3 | Agent 工具调用失败如何处理？ | 代码覆盖 | Observation -> classify -> recovery_decision。 |
| 4 | 支付接口超时这类工具失败如何处理？ | 部分覆盖 | 项目无支付工具，但可用可重试/幂等/补偿事务回答。 |
| 5 | 如何区分可重试失败和不可重试失败？ | 代码覆盖 | `retryable` 字段。 |
| 6 | Agent 如何做超时控制？ | 代码覆盖 | `timeout_seconds`。 |
| 7 | Agent 如何做最大步数控制？ | 代码覆盖 | `max_steps`。 |
| 8 | Agent 如何做成本预算控制？ | 代码覆盖 | `cost_budget_usd` 和 `ModelUsage` 成本 hook。 |
| 9 | Agent 决策错误导致数据误删，系统如何防范？ | 代码覆盖 | sandbox、command allowlist、rollback。 |
| 10 | 高风险操作是否需要二次确认？ | 代码覆盖 | `PermissionDecision.ASK`。 |
| 11 | Agent 如何设计权限、审计、回放机制？ | 代码覆盖 | safety + trace + session report。 |
| 12 | Agent 如何避免越权调用工具？ | 代码覆盖 | AgentSpec allowlist + registry + sandbox。 |
| 13 | Agent 连续执行网页操作或 API 调用时，中间失败如何回滚和重试？ | 部分覆盖 | 本项目有 rollback bundle，但无浏览器/API 状态机。 |
| 14 | 如何避免重复执行非幂等操作？ | 部分覆盖 | repeated tool detection 有雏形，真实非幂等需 idempotency key。 |
| 15 | 任务执行超过单次 Token 限制时，如何支持断点续写或继续生成？ | 代码覆盖 | session resume + memory summary。 |
| 16 | Agent 执行动作前，如何做输入输出一致性的自我验证？ | 代码覆盖 | guardrail/schema/permission/diagnostics。 |
| 17 | 如何设计 Self-Verification 模块减少工具参数错误和推理链断裂？ | 代码覆盖 | schema validation + diagnostics + recovery decision。 |

## 6. Memory、Context 与 Session 管理

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Agent 的记忆机制怎么设计？ | 代码覆盖 | `Memory` 分 recent、summary、session seed。 |
| 2 | 短期记忆和长期记忆分别如何实现？ | 代码覆盖 | recent observations 与 session summary。 |
| 3 | memory 为什么要分短期、长期、summary？ | 代码覆盖 | 控制上下文长度和信息保真度。 |
| 4 | 长期 memory 存什么？ | 部分覆盖 | 项目存 run summary，未做用户画像/长期事实库。 |
| 5 | 长期 memory 什么时候加载？ | 代码覆盖 | `--resume-run` + topic relation 判断。 |
| 6 | memory 是模型自己选，还是规则触发？ | 代码覆盖 | ContextStrategy 规则触发。 |
| 7 | 上下文超出限制时如何处理？ | 代码覆盖 | 截断 repo map、压缩 memory、预览文件。 |
| 8 | 滑动窗口和动态摘要的区别是什么？ | 代码覆盖 | Memory summary 体现动态摘要。 |
| 9 | 长上下文对话中，如何让 Agent 不忘记关键信息？ | 代码覆盖 | attention sink + summary + selected evidence。 |
| 10 | 除了向量检索，还有什么记忆方法？ | 部分覆盖 | 项目用 lexical、summary、session。 |
| 11 | 多轮对话里，指代不清怎么处理？ | 代码覆盖 | topic_relation 可作为反问/继承基础。 |
| 12 | 同一 session 中用户频繁切换话题，如何处理上下文？ | 代码覆盖 | topic_shift 时不继承旧 memory。 |
| 13 | 上一秒问游戏，下一秒问经济，如何判断是否继承上下文？ | 代码覆盖 | same/related/topic_shift 分类。 |
| 14 | 用户先问原神，再问崩铁，如何处理上下文？ | 部分覆盖 | topic relation 可类比，未做实体图谱。 |
| 15 | 用户说“按老样子帮我订一下”，Agent 如何处理？ | 部分覆盖 | 需要长期偏好/订单系统，项目只给 memory 框架。 |
| 16 | Agent 知识闭环中，哪些信息进入向量库，哪些进入上下文窗口，哪些转为模型权重记忆？ | 文档延展 | 属于记忆/训练架构，不进 CodingAgent 核心。 |
| 17 | 对话轮数很多且上下文窗口不足时，如何缓解信息遗忘？ | 代码覆盖 | summary + attention sink + retrieval。 |
| 18 | 摘要总结容易丢失关键细节，长文本 Agent 如何处理？ | 代码覆盖 | 保留 evidence preview，不只保留摘要。 |
| 19 | 短期记忆和长期记忆并存时，上下文超限如何选择保留、压缩或丢弃？ | 代码覆盖 | budget_breakdown + dropped_context。 |
| 20 | 请给出至少两种记忆管理策略。 | 代码覆盖 | sliding recent、dynamic summary、retrieval memory。 |
| 21 | 从非结构化文本更新长期记忆时，如何避免虚假或矛盾信息写入？ | 部分覆盖 | 项目未做事实置信度，但可回答校验/冲突策略。 |
| 22 | 如何设计信息置信度评估与冲突解决流程？ | 部分覆盖 | trace/evidence 可支撑，但无完整 confidence store。 |
| 23 | 多轮对话任务中，用户中途改需求，如何做意图回溯和计划修正？ | 代码覆盖 | topic_relation、resume、TaskGraph 可类比。 |
| 24 | 如何避免执行过时或无用子任务？ | 代码覆盖 | dependency/status/retry/stop 控制。 |

## 7. RAG、GraphRAG 与知识检索

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | RAG 从文档入库到线上检索，完整链路怎么做？ | 部分覆盖 | 项目只做代码上下文检索，不做完整知识库入库。 |
| 2 | 如何做 RAG？ | 部分覆盖 | `rag.py` 是轻量 lexical retrieval。 |
| 3 | 为什么要混合检索？ | 部分覆盖 | file_ranker + lexical 可类比 hybrid。 |
| 4 | 向量检索和关键词检索分别解决什么问题？ | 部分覆盖 | 项目只实现关键词/路径/内容匹配。 |
| 5 | BM25 和向量检索如何融合？ | 部分覆盖 | 文档可讲融合公式，代码未做 BM25。 |
| 6 | BM 算法是什么？ | 文档延展 | 知识点，不进项目代码。 |
| 7 | RAG 为什么要引入父子索引？ | 文档延展 | 知识库场景，不进 CodingAgent 核心。 |
| 8 | 父子索引分别解决什么问题？ | 文档延展 | 同上。 |
| 9 | TopK 怎么定？ | 部分覆盖 | 项目有 top selected files/retrieved docs。 |
| 10 | 召回越多一定越好吗？ | 部分覆盖 | ContextStrategy 体现预算限制。 |
| 11 | chunk 切分为什么不能只用固定长度？ | 部分覆盖 | 文件预览保留结构，未做文档 chunker。 |
| 12 | 长文档如何处理？ | 部分覆盖 | truncate_middle。 |
| 13 | 长文档分块时，chunk size 如何确定？ | 部分覆盖 | 可用 max_context_chars 类比。 |
| 14 | 如何衡量分块效果好坏？ | 部分覆盖 | 项目无 chunk eval，但有 retrieval/trace 思路。 |
| 15 | 分块后如何用聚类、Gini 系数或熵评估纯度？ | 文档延展 | RAG 专项知识。 |
| 16 | 复杂 PDF 解析最难的点是什么？ | 文档延展 | 不属于 CodingAgent 项目。 |
| 17 | metadata 除了存字段，还能如何参与召回？ | 部分覆盖 | file path/part/suffix 类 metadata 用于排序。 |
| 18 | 文档版本机制怎么设计？ | 文档延展 | 项目无文档版本系统。 |
| 19 | 文档有效期怎么设计？ | 文档延展 | 项目无 freshness 机制。 |
| 20 | 文档更新机制怎么设计？ | 文档延展 | 项目无 ingestion pipeline。 |
| 21 | 离线文档和实时接口分别适合什么场景？ | 文档延展 | RAG/业务系统知识。 |
| 22 | 生成后校验层解决什么问题？ | 文档延展 | 项目有 output guardrail，可发散。 |
| 23 | Elasticsearch 在 RAG 系统中的作用是什么？ | 文档延展 | 不进项目。 |
| 24 | 如何优化 Elasticsearch 检索性能？ | 文档延展 | 不进项目。 |
| 25 | 为什么采用 GraphRAG？ | 不覆盖 | 图谱检索不是本项目目标。 |
| 26 | GraphRAG 在复杂关联查询中的优势是什么？ | 不覆盖 | 同上。 |
| 27 | 图谱的好处是什么？ | 不覆盖 | 同上。 |
| 28 | 知识图谱如何构建？ | 不覆盖 | 同上。 |
| 29 | 什么时候选择向量数据库，什么时候选择关系型数据库？ | 文档延展 | 架构知识，不进项目。 |
| 30 | 向量数据库和关系型数据库如何配合？ | 文档延展 | 架构知识，不进项目。 |
| 31 | 图数据库中的 NER 识别怎么做？ | 不覆盖 | 图谱专项。 |
| 32 | 如何衡量 NER 效果好坏？ | 不覆盖 | NLP 评测专项。 |
| 33 | 基于实体关联构图时，是否考虑跳转构图？ | 不覆盖 | 图谱专项。 |
| 34 | LinkedIn Search 这类图谱检索为什么适合图结构？ | 不覆盖 | 搜索/图谱专项。 |

## 8. Agent Eval 与迭代

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Agent 评测体系怎么设计？ | 部分覆盖 | `eval_runner` 是轻量 case eval。 |
| 2 | 如何量化评估一个上线 Agent 的好坏？ | 部分覆盖 | metrics 有基础指标，非线上体系。 |
| 3 | 除了准确率，还有哪些评估指标？ | 部分覆盖 | tool count、failure、guardrail、duration。 |
| 4 | 用户行为能否作为效果评估信号？ | 文档延展 | 项目无用户行为日志。 |
| 5 | badcase 怎么回流到评测集？ | 文档延展 | 可讲流程，代码未实现平台。 |
| 6 | badcase 如何用于后续迭代？ | 文档延展 | 同上。 |
| 7 | 如何评估工具调用成功率？ | 部分覆盖 | `failed_tool_call_count`。 |
| 8 | 如何评估任务完成率？ | 部分覆盖 | eval result `task_success`。 |
| 9 | 如何评估 Agent 的成本和延迟？ | 部分覆盖 | `ModelUsage`、metrics duration。 |
| 10 | 如何评估 Agent 的安全违规率？ | 部分覆盖 | guardrail/safety_violation。 |
| 11 | 如何评估开放式 Agent 的性能？ | 文档延展 | 需要人工/LLM judge/任务分层。 |
| 12 | 传统准确率和召回率为什么不够？ | 文档延展 | Agent 是过程任务，不只分类。 |
| 13 | 如何设计多维度评估指标，包括任务完成度、资源效率、鲁棒性和可解释性？ | 部分覆盖 | 当前指标是雏形。 |
| 14 | 如何衡量 Agent 的 Planning 能力和 Hallucination Rate？ | 部分覆盖 | 有 plan trace/output guardrail，未做系统评测。 |
| 15 | 请列举具体量化评估指标或自动化评估框架。 | 部分覆盖 | 可用项目 metrics 举例。 |

## 9. Agent 安全、可信与可控性

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Agent 如何做权限控制？ | 代码覆盖 | PermissionPolicy。 |
| 2 | Agent 如何做工具调用审批？ | 代码覆盖 | ASK/human_approval。 |
| 3 | Agent 如何做高风险操作拦截？ | 代码覆盖 | CommandPolicy/input/tool guardrails。 |
| 4 | Agent 如何做 human-in-the-loop？ | 代码覆盖 | AskHumanTool + approval event。 |
| 5 | Agent 如何做 dry-run？ | 代码覆盖 | no-auto-approve 可阻断写入；可扩展 dry-run。 |
| 6 | Agent 如何做审计日志？ | 代码覆盖 | TraceRecorder。 |
| 7 | Agent 如何做执行回放？ | 代码覆盖 | trace/session/report。 |
| 8 | Agent 如何避免越权访问数据？ | 代码覆盖 | WorkspaceSandbox。 |
| 9 | Agent 如何降低幻觉导致的业务风险？ | 代码覆盖 | 工具验证 + output guardrail。 |
| 10 | 如何设计关键节点确定性、局部节点智能化的 Agent 架构？ | 代码覆盖 | workflow/safety 确定性，AgentLoop 局部智能。 |
| 11 | 强事实性场景中，如何治理模型幻觉？ | 部分覆盖 | 项目有验证思想，未接业务事实 API。 |
| 12 | 电商场景中，模型幻觉导致活动资格误判如何防范？ | 部分覆盖 | 可类比强事实 API 校验。 |
| 13 | 如何设计主动澄清决策逻辑？ | 代码覆盖 | AskHumanTool 是雏形。 |
| 14 | 什么情况下 Agent 应该反问用户？ | 代码覆盖 | 参数不足/高风险/上下文不确定。 |
| 15 | 什么情况下 Agent 可以结合历史画像强行推断？ | 部分覆盖 | 项目只做 session memory，不做用户画像。 |
| 16 | 面对高度模糊的电商或导购需求，Agent 如何精准理解用户意图？ | 部分覆盖 | 可用 context/memory 类比，业务未实现。 |
| 17 | Agent 如何遵循企业安全策略、法律法规等动态规则？ | 代码覆盖 | policy 层可动态替换。 |
| 18 | 规则约束层如何动态更新？ | 代码覆盖 | PermissionPolicy/CommandPolicy 可作为注入点。 |
| 19 | 可动态更新规则如何注入 Agent？ | 代码覆盖 | 不进 prompt，进 runtime policy。 |
| 20 | 如何比较不同规则注入方式？ | 代码覆盖 | prompt/rule-engine/tool-policy 对比。 |

## 10. C 端对话 Agent 产品工程

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | 如果负责豆包，如何保证 session 内上下文连贯？ | 部分覆盖 | ContextStrategy/topic relation 可迁移。 |
| 2 | 用户同一 session 中话题跳跃，如何判断是否继承历史？ | 部分覆盖 | topic_shift 机制。 |
| 3 | 用户上一秒问游戏，下一秒问经济，消息如何处理？ | 部分覆盖 | 不继承旧上下文。 |
| 4 | 用户问原神后又问崩铁，如何处理上下文？ | 部分覆盖 | 同领域相关但实体切换，需要澄清/局部继承。 |
| 5 | 图片生成和视频生成耗时较长，如何减少用户等待？ | 不覆盖 | 多模态产品体验。 |
| 6 | 长耗时任务如何设计异步处理？ | 部分覆盖 | session/report 可类比异步任务状态。 |
| 7 | 长耗时任务如何展示进度？ | 部分覆盖 | trace/event 可类比进度事件。 |
| 8 | 长耗时任务失败后如何处理？ | 部分覆盖 | recovery/rollback 可类比。 |
| 9 | C 端 Agent 如何平衡体验、成本和效果？ | 部分覆盖 | ModelGateway/usage 有雏形。 |
| 10 | 意图识别怎么做？ | 部分覆盖 | topic_relation 是轻量意图连续性判断。 |
| 11 | 为什么选择当前模型？ | 部分覆盖 | provider/model/usage 可解释，未做真实 A/B。 |
| 12 | 模型如何优化？ | 不覆盖 | 训练/蒸馏/评测优化不在项目。 |

## 11. Agent Infra / 工程落地

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Agent 系统中如何设计异步任务？ | 部分覆盖 | session/report 是状态基础，未做队列。 |
| 2 | Agent 系统中如何设计状态管理？ | 代码覆盖 | RunSession/AgentState/TaskNode。 |
| 3 | Agent 系统中如何设计任务队列？ | 部分覆盖 | TaskGraph 是 DAG，非分布式队列。 |
| 4 | Agent 系统中如何设计工具网关？ | 代码覆盖 | ToolRegistry。 |
| 5 | Agent 系统中如何设计沙箱环境？ | 部分覆盖 | WorkspaceSandbox 是轻量 sandbox。 |
| 6 | Agent 系统中如何设计 tracing？ | 代码覆盖 | TraceRecorder。 |
| 7 | Agent 系统中如何记录 tool call log？ | 代码覆盖 | trace tool_call/tool_observation。 |
| 8 | Agent 系统中如何记录 handoff log？ | 代码覆盖 | Handoff event。 |
| 9 | Agent 系统中如何做运行时观测？ | 代码覆盖 | metrics + trace。 |
| 10 | Agent 系统中如何做线上故障定位？ | 代码覆盖 | trace/report/diff/metrics。 |
| 11 | 包含 3 个以上工具调用且高频请求的任务，如何降低系统端到端延迟？ | 部分覆盖 | 并发 scheduler 雏形，未做线上优化。 |
| 12 | 开放式 Agent 执行任务时，如何动态调节新信息探索和已有知识利用比例？ | 部分覆盖 | ContextStrategy 可类比 exploration/exploitation。 |
| 13 | Agent 多轮执行任务如何保存可恢复状态？ | 代码覆盖 | SessionStore。 |
| 14 | Agent 执行过程如何支持断点、恢复和重放？ | 代码覆盖 | resume-run、trace、rollback。 |

## 12. 多模态 Agent

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | 多模态大模型的具体结构是什么？ | 不覆盖 | 模型结构，不属于 CodingAgent runtime。 |
| 2 | 视觉编码器和语言模型如何衔接？ | 不覆盖 | 多模态模型知识。 |
| 3 | 图像 token 如何进入语言模型上下文？ | 不覆盖 | 多模态模型知识。 |
| 4 | 多模态 Agent 和纯文本 Agent 在工程上有什么区别？ | 不覆盖 | 可在答案文档中延展。 |
| 5 | 多模态 Agent 如何处理图像、视频等长耗时任务？ | 不覆盖 | C 端/多模态产品工程。 |

## 13. Agent 训练与对齐

| 编号 | 问题 | 覆盖状态 | 项目说明 |
|---:|---|---|---|
| 1 | Agentic CPT、SFT、RL 三阶段训练分别是什么？ | 不覆盖 | 训练层问题。 |
| 2 | 为什么 SFT 时要 mask observation tokens？ | 不覆盖 | 训练数据构造问题。 |
| 3 | tool call 轨迹数据如何构造训练样本？ | 不覆盖 | 训练数据问题。 |
| 4 | Agent 训练和普通对话模型训练有什么区别？ | 不覆盖 | 模型训练问题。 |
| 5 | 为什么有些场景 SFT 效果不好？ | 不覆盖 | 模型训练和数据分布问题。 |
| 6 | reward model 适合解决什么问题？ | 不覆盖 | RLHF/RLAIF 问题。 |

## 最终定位

Agent Forge 覆盖的是 CodingAgent runtime 主线：架构、ReAct、上下文工程、tool calling、权限安全、异常恢复、multi-agent 编排、trace/session/eval。RAG/GraphRAG、多模态、训练、C 端产品体验是面试延展知识，不应强行塞进核心代码。

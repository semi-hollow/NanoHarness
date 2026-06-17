# 02 技术问题覆盖地图

这份文件回答：哪些题能用 Agent Forge 项目直接回答，哪些只能作为扩展知识回答。
不要把所有 AI Agent 题都硬塞进项目，否则会显得项目边界混乱。

## 覆盖等级

| 等级 | 含义 | 回答方式 |
|---|---|---|
| 强覆盖 | 项目里有代码实现、运行证据和文档。 | 先讲项目实现，再讲取舍。 |
| 中覆盖 | 项目有基础实现，但不是完整工业平台。 | 讲当前实现和生产扩展方向。 |
| 边界覆盖 | 项目只体现局部思想，不是主能力。 | 承认边界，说明如何接入。 |
| 不覆盖 | 不属于 CodingAgent runtime core。 | 讲理论理解，不要说项目实现了。 |

## 主题覆盖表

| 主题 | 覆盖 | 项目证据 | 回答重点 |
|---|---|---|---|
| Agent 整体架构 | 强覆盖 | `cli.py`, `agent_loop.py`, `runtime/`, `context/`, `tools/` | runtime control plane 分层。 |
| ReAct / Plan-and-Execute | 强覆盖 | `AgentLoop`, `PlanningModePolicy`, `SimplePlanner` | Coding task 需要观察驱动；workflow 适合确定性链路。 |
| 工具调用治理 | 强覆盖 | `ToolRouter`, `ToolRegistry`, `run_command.py`, `mcp_config.py` | tool calling 是 schema、权限、执行、观测、恢复的治理系统。 |
| 执行控制和异常处理 | 强覆盖 | `StepController`, `FailureKind`, `TaskStateStore` | repeated action、failure classification、timeout、budget、resume。 |
| 权限、安全、human-in-loop | 强覆盖 | `HookManager`, `PermissionPolicy`, `ApprovalMode`, `WorkspaceSandbox` | prompt safety 不可靠，要用 runtime hard gate。 |
| Memory / Context / Session | 强覆盖 | `ContextStrategy`, `Memory`, `MemoryPolicy`, `SessionStore` | 短期记忆、summary、topic shift、session seed。 |
| Observability / Usage | 强覆盖 | `TraceRecorder`, `usage_report.py`, committed artifacts | per-step token、cache、cost、latency、tool efficiency。 |
| Eval / Badcase flywheel | 中覆盖 | `eval_cases/`, `eval_runner.py`, `eval_history.py`, `flywheel.py` | local regression，有能力分类；还不是大规模线上 eval 平台。 |
| Multi-Agent | 中覆盖 | `SupervisorAgent`, `AgentRuntime`, `TaskGraph`, `AgentSpec` | 主子模式、role allowlist、artifact handoff、review gate。 |
| MCP / 外部工具 | 中强覆盖 | `agent_forge/mcp/`, `mcp_stdio.py`, `mcp_tools.example.json` | stdio server、discovery、allowlist、tools/call；不是远程 marketplace。 |
| RAG / GraphRAG | 边界覆盖 | `repo_map.py`, `file_ranker.py`, `rag.py`, `symbol_search.py` | 本项目是 code retrieval，不是企业知识库平台。 |
| C 端对话产品 | 边界覆盖 | `ClarificationPolicy`, `ContextStrategy.topic_relation` | 能讲 session 连贯性和话题切换，不讲产品指标。 |
| 多模态 Agent | 不覆盖 | 无 | 可作为工具接入，不进入 coding runtime core。 |
| Agent 训练/SFT/RL | 不覆盖 | 无 | 属于模型层，不属于 runtime 项目。 |
| 大规模云平台/队列/SLA | 不覆盖 | 无 | 可由服务层接入，本项目不声称线上化。 |

## 必须掌握的 16 个问题

| 问题 | 项目回答 |
|---|---|
| 这个项目整体架构怎么设计？ | CLI 组装模型、工具、环境和 trace；AgentLoop 做 ReAct 闭环；context/tools/safety/models/observability/eval 分层。 |
| 为什么不是直接 prompt LLM 写代码？ | 真实代码修改需要读文件、改文件、跑测试、处理失败和审计，必须有工具、权限和观测闭环。 |
| 为什么用 ReAct？ | Coding task 的下一步依赖 observation，例如读文件后才能 patch，测试失败后才能修。 |
| Workflow 和 Agent 怎么取舍？ | 确定性、高风险链路适合 workflow；需要动态探索、读写反馈、失败恢复的局部适合 Agent。 |
| 上下文怎么构建？ | repo map + file ranker + lexical retrieval + file preview + memory summary + FORGE.md + budget breakdown。 |
| 工具很多怎么选？ | ToolRouter 按 capability/risk/latency/mode 和任务词裁剪工具 schema，减少工具过载。 |
| 工具参数错怎么办？ | ToolRegistry 先做 schema validation，失败转成 Observation，StepController 判断是否可恢复。 |
| 工具调用失败怎么办？ | 失败分为 unknown tool、invalid args、permission denied、patch mismatch、command failed、repeated action 等。 |
| 怎么避免死循环？ | max_steps、max_tool_repeats、max_consecutive_failures、timeout、cost budget。 |
| 怎么避免越权？ | WorkspaceSandbox 阻断外部路径，CommandPolicy allowlist，ExecutionEnvironment 阻断网络和 git 风险命令。 |
| 怎么做 human-in-loop？ | Hook 返回 ASK，AgentLoop 写 human_approval event；locked/dry-run 可用于非交互环境。 |
| 任务长了怎么恢复？ | TaskStateStore 记录 checkpoint，SessionStore 提供 resume summary，trace 可 replay。 |
| Multi-Agent 怎么防幻觉？ | Supervisor 不信子 agent 文本，只看 artifact、trace、tool observation、tests、review gate。 |
| 怎么量化成本和效果？ | usage_report 记录 per-step tokens、cache hit/miss、cost、latency、context chars、tool success。 |
| MCP 在项目里起什么作用？ | 证明外部工具可以通过 stdio JSON-RPC discovery/call 接入 ToolRegistry，而不是硬编码在 AgentLoop。 |
| 这个项目还缺什么？ | IDE/TUI、远程 sandbox、线上 telemetry、大规模 eval、远程 MCP gateway、真实用户反馈闭环。 |

## 不能硬说项目已经实现的内容

| 方向 | 正确说法 |
|---|---|
| GraphRAG | “本项目做的是代码仓库 retrieval；GraphRAG 属于知识平台层，可以替换 ContextStrategy 的 retrieval backend。” |
| 多模态 | “多模态可以作为工具接入，比如 image/video tool，但当前项目聚焦文本代码修改 runtime。” |
| SFT/RL | “训练属于模型层，本项目关注 runtime 层；tool trajectory 可作为训练数据来源，但没有训练模型。” |
| 线上 SLA | “项目有本地 trace/eval/usage 证据，不声称真实线上服务。” |
| 企业容器沙箱 | “当前有 worktree + policy hooks；生产会把 ExecutionEnvironment backend 替换成 Docker/remote sandbox。” |

## 边界回答模板

当追问超出项目范围时，用这个结构：

```text
这个问题确实重要，但它属于 <某一层>，不是我这个 runtime core 当前实现的主范围。
我项目里和它相关的是 <已有实现>。
如果生产化，我会在 <扩展点> 接入 <更完整方案>，这样不会污染 AgentLoop 主链路。
```

例子：

```text
GraphRAG 对复杂关联查询很重要，但 Agent Forge 是 CodingAgent runtime core，不是知识平台。
当前项目实现了 repo map、file ranking、lexical retrieval、symbol search 和 evidence grounding。
如果要接企业知识库，我会把 ContextStrategy 的 retrieval 层替换成 BM25 + vector + graph expansion + reranker，
AgentLoop、ToolRegistry、HookManager 都不需要改。
```

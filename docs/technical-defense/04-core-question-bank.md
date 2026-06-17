# 04 核心中文问答库

这份文件是直接用来练技术追问的。回答时尽量遵循：

```text
先给结论 -> 再说项目实现 -> 再说取舍和生产扩展
```

## Agent 架构

### 1. 介绍一个 AI Agent 项目，整体架构怎么设计？

我的项目是 Agent Forge，一个 CodingAgent runtime core。整体分成七层：

1. CLI composition：`cli.py` 负责解析 mode、模型、工具、trace、session。
2. Agent runtime：`AgentLoop` 做 context -> LLM -> tool -> observation -> recovery。
3. Context engineering：`context/` 负责 repo map、file ranking、retrieval、memory、budget。
4. Tool governance：`tools/` 和 `safety/` 负责 schema、权限、sandbox、command policy。
5. Model gateway：`models/` 抽象 MockLLM、DeepSeek、Ollama/公司 OpenAI-compatible API。
6. Observability：`TraceRecorder` 和 `usage_report` 记录每步 evidence、token、cost、latency。
7. Eval/review：`eval_cases/` 和 `review_workflow.py` 做本地回归和 deterministic review gate。

核心观点是：LLM 只负责提出动作，runtime 负责控制动作。

### 2. 为什么选择 Workflow，而不是完全自主决策的 Agent？

我的项目不是二选一，而是同时保留 AgentLoop 和 deterministic workflow。

Workflow 适合稳定、高风险、业务规则明确的流程，例如固定审批链、支付链路、合规检查。AgentLoop 适合开放式代码修改，因为下一步依赖 observation：先读文件，再决定 patch；测试失败后再决定修哪里。

所以生产系统更合理的是混合架构：关键节点 deterministic，局部复杂节点用 ReAct。

### 3. Tools、Workflow、Agent 的本质区别是什么？

Tool 是动作接口，比如 read_file、apply_patch、run_command。Workflow 是确定性编排，按固定 DAG 或状态机执行。Agent 是模型参与决策的闭环 runtime，它会根据 observation 动态决定下一步。

在 Agent Forge 里，Tool 是 `ToolRegistry` 里的可执行能力，Workflow 是 `workflows/` 里的 baseline 和 review gate，Agent 是 `AgentLoop`。

### 4. 如何平衡 Agent 的可控性和能力？

我的做法是把能力放在工具和上下文里，把可控性放在 runtime 里。

能力来自：上下文召回、工具集合、MCP 外部工具、真实模型。可控性来自：ToolRouter、schema validation、HookManager、CommandPolicy、WorkspaceSandbox、StepController、trace 和 eval。

也就是说，不靠 prompt 限制模型，而是让模型只能在 runtime 允许的动作空间里行动。

### 5. 当前阻碍 Agent 大规模落地的最大挑战是什么？

我会回答三个：

1. 可靠性：长链路里一个工具参数错、上下文漏召回、测试误判都会导致失败。
2. 可控性：模型会产生越权、重复调用、幻觉验证、错误副作用。
3. 可观测和评估：只看最终回答不足以定位问题，必须有 trace、usage、eval、badcase flywheel。

Agent Forge 主要解决的是 runtime 可靠性和可观测这两块。

## ReAct、Planning 和执行控制

### 6. ReAct 框架工程上怎么实现？

工程上不是让模型输出一段“思考”，而是稳定地循环：

```text
assemble context -> LLM call -> parse tool call -> policy check -> execute tool -> observation -> next turn
```

在 Agent Forge 中，`AgentLoop.run()` 是主循环，`Message` 表示对话消息，`ToolCall` 表示结构化工具请求，`Observation` 表示工具结果。每一步都会写 trace。

### 7. 为什么使用 ReAct？

因为代码修改是 observation-driven。模型必须先观察仓库、文件、测试结果，才能做下一步。一次性生成 patch 很容易漏上下文或测试失败。

ReAct 的价值不是“让模型多思考”，而是把外部事实不断反馈进下一轮决策。

### 8. Agent 出现死循环怎么办？

我在 `StepController` 做了几层控制：

- `max_steps`：总步数上限。
- `max_tool_repeats`：同一工具和同一参数重复太多就拦截。
- `max_consecutive_failures`：连续失败上限。
- `timeout_seconds`：运行时间上限。
- `cost_budget_usd`：成本预算上限。

此外，失败会分类为 repeated_action、permission_denied、patch_mismatch、command_failed 等，不同类型给不同 recovery hint。

### 9. 工具调用失败如何处理？

工具失败不能直接抛异常中断主循环。Agent Forge 的做法是：

1. `ToolRegistry` 捕获 unknown tool、invalid arguments、tool exception。
2. 统一转成 failed `Observation`。
3. `StepController.classify_observation()` 判断是否可重试。
4. recovery hint 写进 trace 和后续上下文。

这样模型能基于失败证据调整下一步，而不是盲目重试。

### 10. 如何区分可重试失败和不可重试失败？

可重试失败通常是参数、上下文、patch anchor 或测试失败，例如 invalid arguments、old text not found、command failed。不可重试失败通常是权限、安全策略、重复副作用或预算耗尽，例如 permission denied、repeated action、max steps reached。

项目里这个逻辑在 `StepController`，不是交给模型自由判断。

## Tool Calling 和 MCP

### 11. tool call 和 observation 如何进入下一轮推理？

模型输出 tool call 后，runtime 执行工具，得到 `Observation`。这个 observation 会进入 memory 和 message history，下一轮 context 重新 assemble。这样模型不是凭空继续，而是基于工具返回的事实继续。

这也是我把工具结果叫 Observation 的原因：它不是日志，而是下一轮推理的输入事实。

### 12. 工具库有上百个工具时怎么选？

不能把所有工具 schema 都塞给模型。我的项目里 `ToolRouter` 会按任务词、capability、risk、latency、mode 选择候选工具。基础 read/search 工具常驻；写、命令、MCP 外部工具按任务需要加入。

生产上可以把 ToolRouter 扩展成两阶段：先用 embedding/BM25/规则召回候选工具，再让模型在小集合里选择。

### 13. tool schema 怎么设计减少误调用？

schema 要做到：

- 工具名动词明确，例如 `read_file`、`apply_patch`。
- 参数少且类型明确。
- description 写清楚允许/禁止场景。
- required 字段完整。
- 高风险工具在 description 里提示边界，但真正边界由 Hook/Policy 执行。

项目里 `ToolRegistry` 会做参数校验，避免 bad args 直接进入工具实现。

### 14. MCP 和 Function Calling 有什么区别？

Function calling 更偏模型输出格式：模型按 schema 生成函数调用。MCP 更偏工具生态协议：工具 server 如何暴露工具、如何 discovery、如何 call、如何返回 content。

在我的项目里，模型仍然使用 tool schema 选择工具；MCP 负责把外部 server 的工具发现出来，注册成普通 Tool 进入 ToolRegistry。

### 15. 为什么 web search 不直接写进 AgentLoop？

因为 web search 是外部工具能力，不是 agent loop 的核心逻辑。如果写进 AgentLoop，会让主循环充满 provider if/else。

我的做法是把 web_search/web_fetch 放在 MCP server 后面。AgentLoop 只看到统一的 tool schema、permission、observation、trace。这样 DuckDuckGo、OpenAI hosted search、Claude hosted search 都是工具 provider 的实现细节。

## Context、Memory 和 Session

### 16. Agent 的记忆机制怎么设计？

我把记忆分成：

- current messages：当前运行内的对话和工具 observation。
- recent memory：当前任务中的短事实。
- summary memory：旧 observation 的压缩总结。
- session seed：从历史 session 或 task state 恢复的摘要。
- MemoryRecord：带 scope、confidence、TTL、source、agent_name 的结构化记录。

关键不是记得越多越好，而是该继承时继承，该隔离时隔离。

### 17. 上下文超出限制怎么办？

不能简单截断末尾。我的策略是：

1. 保留 attention sink 和最新任务。
2. repo map 只占部分预算。
3. 选择最相关文件 preview，而不是全文件。
4. old observations 变 summary。
5. 低相关 docs 被 dropped_context 记录。
6. trace 里保留 budget_breakdown，便于复盘。

### 18. 用户频繁切换话题，如何判断是否继承上下文？

项目里用 `infer_topic_relation()` 做轻量判断，输出 same_topic、related_topic、topic_shift、unknown。若 topic_shift，就不继承上一 session memory，避免上下文污染。

生产上可以替换成 intent classifier 或 embedding similarity，但关键设计点是：上下文继承必须显式决策，不能默认全继承。

### 19. 为什么 RAG 不能彻底解决幻觉？

RAG 只解决“给模型更多事实”，但不能保证模型正确使用事实，也不能解决工具执行失败、权限越界、输出 false claim、长链路状态漂移。

所以 Agent Forge 除了 retrieval，还有 guardrail、ToolRegistry、HookManager、StepController、tests、review gate、trace evidence。

## Multi-Agent 和编排

### 20. Multi-Agent 系统怎么设计？

我倾向三层：

1. Supervisor 层：任务分解、状态机、handoff、retry、review gate。
2. Role Agent 层：planner、coder、tester、reviewer，各自有 tool allowlist。
3. Runtime 层：所有 agent 共用 AgentLoop、ToolRegistry、HookManager、Trace。

Agent Forge 的 multi 模式就是主子结构，而不是去中心化聊天群。

### 21. 子 Agent 产生幻觉怎么办？

Supervisor 不应该直接相信子 Agent 的自然语言。要看 artifact、trace、tool observation、test result、git diff 和 review gate。

在项目里，子 agent 输出通过 `AgentRunResult` 和 artifact handoff 传递，review/test 阶段会重新验证。

### 22. 多 Agent 为什么不一定并发？

并发取决于依赖和冲突，不是为了展示多 agent 就强行并发。代码修改任务通常有 plan -> code -> test -> review 的依赖。

项目里 `TaskGraph` 支持依赖和 conflict-aware batches。只有没有文件写冲突、依赖满足时才适合并发。

## 安全和可信

### 23. Agent 如何做权限控制？

权限不是写在 prompt 里，而是在 runtime 执行：

- `PermissionPolicy` 决定 allow/ask/deny。
- `ApprovalMode` 支持 trusted、on-write、on-risk、locked、dry-run。
- `HookManager` 在工具执行前统一检查。
- `WorkspaceSandbox` 限制路径。
- `CommandPolicy` 限制命令。

### 24. 高风险操作是否需要二次确认？

需要。项目里 write/apply_patch/run_command 这类 side-effect action 可以通过 approval mode 触发 ASK。非交互环境下可以用 locked 或 dry-run 直接阻断。

生产上还要把非幂等操作接入 operation ledger，避免重复执行。

### 25. 如何避免 Agent 决策错误导致数据误删？

我的项目做了几层：

- 命令 allowlist，不允许 rm、sudo、git reset、git push。
- `shell=False`，避免 shell injection。
- workspace sandbox，不允许逃出仓库。
- worktree 模式隔离主 checkout。
- rollback bundle 只保存改动文件的旧版本。

生产上还要容器/VM 隔离、快照、审批、dry-run 和非幂等操作账本。

## Observability 和 Eval

### 26. Agent 评测体系怎么设计？

我会分三层：

1. Case-level：每个 eval case 验证一个 failure mode，例如 sandbox、unknown tool、repeated call。
2. Trace-level：记录 context、tool、permission、recovery、final answer。
3. Trend-level：eval history 比较 pass rate、新增失败、修复失败。

Agent Forge 当前实现的是本地 regression harness，不是大规模 leaderboard。

### 27. 除了准确率，还有哪些指标？

对 Agent 来说要看：

- task success
- tool success rate
- failed observation count
- recovery success
- safety violation
- token/cost/latency
- context truncation / dropped context
- repeated action count
- human approval rate
- review gate finding count

### 28. usage report 有什么价值？

它把一次 agent run 从“感觉跑了”变成可量化：

- 每步 LLM call 花了多少 token。
- cache hit/miss 怎么样。
- 成本估算是多少。
- 哪些工具被调用，成功率如何。
- context 预算花在哪里。
- runtime control 触发了哪些 hook 和 status。

这能支持成本优化、延迟优化、badcase 复盘和技术讲解。

## RAG、GraphRAG 和训练边界

### 29. 为什么项目没有做完整 GraphRAG？

因为 Agent Forge 是 coding runtime core，不是企业知识库平台。代码任务第一阶段更需要 deterministic repo retrieval：路径、文件名、符号、关键词、测试文件关系。

GraphRAG 可以作为 ContextStrategy 的 retrieval backend 接入，但不应该污染 AgentLoop。

### 30. Agent 训练和 runtime 的关系是什么？

训练解决模型能力，比如 tool-use policy、trajectory imitation、RL 优化。runtime 解决系统能力，比如工具权限、执行边界、trace、eval、恢复。

本项目不训练模型，但 trace/tool trajectory 可以成为后续 SFT/RL 数据来源。


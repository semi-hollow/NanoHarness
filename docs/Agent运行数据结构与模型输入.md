# Agent 运行数据结构与模型输入

> 本文回答四个问题：用户对话保存在哪里；一次用户请求、一次执行尝试和一次模型调用如何区分；模型实际看见什么；崩溃恢复从哪里继续。

# 1. 四级身份先分清

NanoHarness 的运行身份只有下面四层：

```text
ConversationThread                  durable user conversation
└── Turn                            one top-level user request
    ├── Run                         one execution attempt
    ├── resumed Run                 another attempt of the same Turn
    └── Model Step 1..N             one ModelPort.chat(...) per step
```

核心映射：

| 身份 | 系统角色 | 什么时候变化 |
|---|---|---|
| `thread_id` | 一段可持续追问的用户 Conversation | 新建对话时变化 |
| `turn_id` | 一次顶层用户请求从开始到终态的完整逻辑工作 | 正常 follow-up 时变化；resume 不变 |
| `run_id` | 一个 Turn 的一次实际 execution attempt | 每次 fresh run 或 resume 都变化 |
| `step` | 当前 Run 内的一次模型调用序号 | 每次 `ModelPort.chat(...)` 前递增 |

因此：

```text
Normal follow-up = same thread_id + new turn_id + new run_id
Resume           = same thread_id + same turn_id + new run_id
```

Resume 不是“继续聊天”。它只继续同一个尚未完成的 Turn；终态后用户再发要求，必须创建新 Turn。

# 2. 每种数据只有一个权威 Owner

| 数据对象 | 生命周期 | 权威内容 | 不负责什么 |
|---|---|---|---|
| `ConversationThread` | 跨 Turn、跨进程 | Thread 元数据、Turn/Run 导航 | 不复制 Run trace |
| `conversation.jsonl` | 跨 Turn、跨进程 | 完整 user/assistant/tool Conversation items | 不保存 System Prompt 投影 |
| `ThreadContextState` | 跨 Run | rolling digest、covered sequence、Turn snapshots | 不替代 raw Conversation |
| `StableTurnContextSnapshot` | 一个 Turn | 冻结的 Prompt、指令、Skill、LTM recall、base tools | 不保存动态仓库正文 |
| `AgentRunSession` | 一个 Run 的当前进程 | 有界消息视图、控制器、Working Memory、运行累计 | 不作为 durable truth |
| `PreparedModelStep` | 一次模型调用 | 最终 messages、tool schemas、路由与预算证据 | 不执行模型或工具 |
| `TaskCheckpoint` v4 | 一个 Run | 状态、step、context revision、pending tool cursor | 不保存 root task 或 Conversation |
| `trace.jsonl` | 一个 Run | durable execution evidence | 不是 raw conversation store |

最重要的边界是：

```text
conversation.jsonl                  = canonical raw conversation
ThreadContextState.conversation_history_digest = durable derived projection
TaskCheckpoint                      = execution recovery pointer
trace.jsonl                         = execution evidence
```

不能从 Trace 反推“完整聊天”，也不能把压缩摘要当成原始对话。

# 3. Conversation item 为什么不只保存 provider role

每条 durable item 同时保存：

```text
sequence / item_id / item_hash / previous_hash
thread_id / turn_id / run_id
role / content / tool_calls / tool_call_id
origin / human_authority
```

`role=user` 只是发送给 Provider 的编码。授权语义由 `origin + human_authority` 决定：

| 输入 | provider projection | `human_authority` |
|---|---|---:|
| 用户顶层请求 | `role=user` | `true` |
| Operator steer / 人工回答 | `role=user` | `true` |
| Worker coordination evidence | `role=user` | `false` |
| Runtime budget control | transient `role=user` | 不持久化、不授权 |

这使 `remember_memory` 能验证当前 Turn 内真实的人类原文，而不会把 Runtime coordination 或模型文本误当成人类授权。

Repository 为 append 分配连续 `sequence`，并验证 hash chain。相同 `item_id` 只有在逻辑 payload 完全一致时才幂等返回；payload 不同会 fail closed。

# 4. Single-Agent 主数据流

```text
Harness.run(RunRequest)
    ↓
ConversationThreadRepository.start_turn(...)
    ↓
RunPreparation.create_session()
    ├── Thread.require_turn(turn_id) → authoritative root_task
    ├── TaskCheckpoint v4
    └── bounded raw Conversation view
    ↓
RunPreparation.prepare_run()
    ├── input policy
    ├── StableTurnContextSnapshot freeze / restore
    └── clarification barrier
    ↓
AgentLoop.run()
    ↓ repeated Model Steps
ModelStepPreparation.prepare_model_step()
    ├── ToolRouter.route()
    ├── current dynamic repository context
    ├── PromptWindowManager.prepare()
    └── PreparedModelStep
    ↓
ModelPort.chat(
    PreparedModelStep.llm_messages,
    PreparedModelStep.tool_schemas,
)
    ↓
FinalAnswerBuilder or ToolExecutionPipeline
    ↓
RunLifecycle.finalize_run()
```

`AgentLoop` 只拥有阶段顺序。输入治理、窗口压缩、工具授权、执行幂等和最终答案质量门分别由具名 Application owner 负责。

# 5. StableTurnContextSnapshot 冻结什么

新 Turn 首次运行时，`RunPreparation` 冻结：

```text
PromptSpec profile and complete governing System Prompt
Project Instructions
selected Skill cards and Skill tool names
Long-Term Memory recall snapshot
base Tool schemas
Runtime compatibility contract
```

同一 Turn 的 resume Run 直接恢复该 Snapshot，不重新扫描这些稳定输入。新 Turn 才重新解析。

Snapshot 不冻结动态仓库事实。每个 Model Step 仍会根据最新 `turn_focus` 重新读取：

- repository map 与 selected file previews；
- Working Memory 派生状态；
- 当前路由后的 Tool surface；
- 当前 human-authority 输入对应的 Memory Management Candidates。

如果当前模型能力、Prompt profile、输入预算或基础 Tool schema 与 Snapshot contract 不兼容，resume fail closed，不静默换规则。

# 6. AgentRunSession 保存什么

`AgentRunSession` 是一个 Run 内唯一的可变状态容器，关键字段按职责分组：

```text
Identity
├── thread_id / turn_id
├── thread_initial_task
├── root_task
└── turn_focus / turn_focus_item_id

Bounded Conversation projection
├── messages
├── observations
└── message_sequences

Frozen Turn input
├── stable_system_prefix
├── base_tool_schemas
├── skill_tool_names
└── long_term_memory_snapshot

Run-local derived state
├── working_memory
├── evidence
├── controller
└── cost / validation / stop state
```

其中：

- `root_task` 永远来自当前 `Turn.root_task`；
- `turn_focus` 可以被 clarification 或 operator steer 更新，用于本步检索和路由；
- `messages` 只是从 Thread journal 重建的有界模型视图，不是第二份 Conversation；
- `WorkingMemory` 只保存运行中的派生提示，不镜像 raw Tool Observation。

# 7. 模型实际收到什么

`PreparedModelStep` 是一次 `ModelPort.chat(...)` 的冻结输入：

```text
PreparedModelStep
├── llm_messages
│   ├── current ModelStepSystemContext
│   ├── optional ConversationHistoryDigest
│   ├── recent raw Conversation tail
│   └── optional transient Runtime control message
├── tool_schemas
├── allowed_tool_names
├── phase
└── prompt metrics
```

真实字段流：

```text
ConversationThread journal
    ↓ bounded raw tail
AgentRunSession.messages
    ↓
conversation_history = list(session.messages)
    ↓
PromptWindowRequest.conversation_history
    ↓
PromptWindowManager.prepare()
    ↓
PromptWindowResult.llm_messages
    ↓
PreparedModelStep.llm_messages
    ↓
ModelPort.chat(...)
```

System Context 由两块组成：

```text
StableTurnContextSnapshot.stable_system_prefix
+
ModelStepSystemContextBuildReport dynamic context
→ model_step_system_message
```

完整 governing System Prompt 是 mandatory block。动态 repository 内容只能在自己的预算内裁剪，不能挤掉或改写它。

# 8. Compaction 改的是模型投影，不是 Conversation

当窗口接近预算时，`PromptWindowManager` 选择最小合法旧前缀：

```text
previous digest + newly covered raw prefix
→ deterministic rolling merge
→ updated ConversationHistoryDigest + recent raw tail
```

合法边界不能拆开 assistant ToolCall batch 与对应 Tool Observations。长 Thread 会按有界页持续合并，直到追到最新 raw tail；只有最终视图才发送给模型。

压缩后：

- `conversation.jsonl` 仍保留完整 item；
- `ThreadContextState.covered_sequence` 指向已投影的 journal 边界；
- `ConversationHistoryDigest.covered_message_count` 是累计审计计数；
- `TaskCheckpoint.context_revision` 只指向当前 context state revision。

# 9. ToolCall batch 如何跨崩溃恢复

模型返回 ToolCall 时，Runtime 先 durable append 完整 assistant batch，包括同时返回的 assistant content。然后 checkpoint 保存：

```text
PendingExecutionPointer
├── assistant_item_id
├── next_tool_call_index
├── pending_operation_key
└── pending_operation_fingerprint
```

每个调用按原顺序执行：

```text
security recheck
→ Operation Ledger / Approval boundary
→ ToolGateway.execute()
→ durable Tool Observation
→ advance cursor
```

Resume 先读取原 assistant batch 和 cursor，直接继续原调用，不要求模型重新生成。Observation 已写但 cursor 未推进时，只修复 cursor；状态变更已启动但结果未知时 fail closed，不重复执行。

无论执行、拒绝、预算丢弃还是 terminal stop，每个 ToolCall 最终都只能有一个合法 Tool Observation。等待 Approval、Human Input 或 Pause 时例外：cursor 停在原调用，直到同一 Turn resume。

# 10. Follow-up 与 Resume 的最小验证

正常追问：

```text
completed Turn A
→ Harness.run(task=new_user_message, thread_id=same_thread)
→ Turn B + Run B1
```

中断恢复：

```text
waiting Turn A / Run A1 checkpoint
→ Harness.resume(checkpoint_path)
→ same Turn A + new Run A2
→ restore snapshot and pending batch
```

只有 accepted `COMPLETED` 才会产生 `final_answer`，并先把 assistant final durable append 到 Thread。这样下一个用户 Turn 能看到真实最终回答；被质量门拒绝的文本只作为 `model_final_candidate` 证据，不冒充最终答案。

# 11. 源码入口（Source Anchors）

- `agent_forge/runtime/domain/thread.py::ConversationThread`：Thread、Turn、Run 与 Snapshot 领域事实。
- `agent_forge/runtime/adapters/thread_json.py::JsonConversationThreadRepository`：hash-chain journal、锁、fsync、CAS 与修复。
- `agent_forge/harness.py::Harness.run()`：Thread/Turn/Run 身份绑定与 Public API。
- `agent_forge/runtime/application/run_preparation.py::RunPreparation`：Session 与 Turn Snapshot。
- `agent_forge/runtime/application/session.py::AgentRunSession`：Run-local mutable state。
- `agent_forge/runtime/application/model_step_preparation.py::PreparedModelStep`：单次模型输入契约。
- `agent_forge/runtime/application/model_step_preparation.py::ModelStepPreparation.prepare_model_step()`：Context、History 与 Tool schema 汇合点。
- `agent_forge/context/application/compaction.py::PromptWindowManager.prepare()`：增量压缩与模型窗口。
- `agent_forge/runtime/application/tool_execution.py::ToolExecutionPipeline`：durable batch、cursor 与 exactly-one Observation。
- `agent_forge/runtime/application/run_lifecycle.py::RunLifecycle.finalize_run()`：唯一停止与最终答案持久化边界。

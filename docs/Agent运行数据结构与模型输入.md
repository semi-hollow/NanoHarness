# Agent 运行数据结构与模型输入

> 本文建立 NanoHarness 最基础的运行模型：Thread 保存持续对话，Turn 拥有一次用户目标，Run 表示一次执行尝试，Model Step 是一次模型调用。

# 1. 四级运行身份

```text
ConversationThread                  durable user conversation
└── Turn                            one top-level user request
    ├── Run 1                       first execution attempt
    └── Run 2                       resume attempt of the same Turn
         └── Model Step 1..N        one ModelPort.chat(...) per step
```

| 身份 | 系统角色 | 变化时机 |
|---|---|---|
| `thread_id` | 一段可持续 follow-up 的用户 Conversation | 新建对话 |
| `turn_id` | 一个顶层目标从输入到终态的逻辑工作 | 新顶层请求 |
| `run_id` | Turn 的一次实际 execution attempt | fresh run 或 resume |
| Model Step | 当前 Run 内的一次模型调用边界 | 每次 `ModelPort.chat(...)` |

```text
Normal follow-up = same thread_id + new turn_id + new run_id
Resume           = same thread_id + same turn_id + new run_id
```

Resume 只延续未完成 Turn。终态后的新要求进入新 Turn。

# 2. 权威对象与生命周期

| 对象 | 生命周期 | Canonical responsibility |
|---|---|---|
| `ConversationThread` | 跨 Turn、跨进程 | Thread metadata 与 Turn/Run navigation |
| `conversation.jsonl` | 跨 Turn、跨进程 | 完整 user/assistant/tool Conversation items |
| `ThreadContextState` | 跨 Run | rolling digest、`covered_sequence`、Turn snapshots |
| `StableTurnContextSnapshot` | 一个 Turn | root task、稳定 Prompt/Instruction/Skill/Memory/base tools |
| `AgentRunSession` | 一个 Run 的当前进程 | 有界消息视图、controller、Working Memory、运行累计 |
| `PreparedModelStep` | 一次模型调用 | 最终 messages、tool schemas、route 与 prompt metrics |
| `TaskCheckpoint v4` | 一个 Run | status、step、context revision、pending execution cursor |
| `trace.jsonl` | 一个 Run | append-only execution evidence |

核心映射：

```text
conversation.jsonl                              raw conversation authority
ThreadContextState.conversation_history_digest derived continuation projection
TaskCheckpoint                                 execution recovery state
trace.jsonl                                    happened-before evidence
```

# 3. 对话条目的协议与来源语义

`ConversationItem` 同时保存顺序、协议 payload 和来源：

```text
sequence / item_id / previous_hash / item_hash
thread_id / turn_id / run_id
role / content / tool_calls / tool_call_id
origin / human_authority
```

Provider role 决定怎样发送给模型；`origin + human_authority` 决定 Runtime 是否把它视为人类授权：

| 输入 | Provider projection | Authority |
|---|---|---|
| 顶层用户请求 | `role=user` | `human_authority=true` |
| Operator steer / 人工回答 | `role=user` | `human_authority=true` |
| Worker coordination evidence | `role=user` | `human_authority=false` |
| 临时预算控制 | transient `role=user` | 不持久化 |

Repository 为 append 分配连续 `sequence` 并验证 hash chain。相同 `item_id` 只有在逻辑 payload 相同的情况下才幂等返回。

# 4. Single-Agent 执行链

```text
Harness.run(RunRequest)
→ ConversationThreadRepository.start_turn(...)
→ RunPreparation.create_session()
    → ConversationThread.require_turn(turn_id).root_task
    → TaskCheckpoint v4
    → bounded Conversation view
→ RunPreparation.prepare_run()
    → freeze or restore StableTurnContextSnapshot
→ AgentLoop.run()
    → ModelStepPreparation.prepare_model_step()
    → ModelPort.chat(...)
    → FinalAnswerBuilder or ToolExecutionPipeline
→ RunLifecycle.finalize_run()
```

`Harness` 绑定身份；`RunPreparation` 建立同 Turn 稳定输入；`AgentLoop` 控制迭代；`ModelStepPreparation` 冻结单次模型输入；`RunLifecycle` 提交终态。

# 5. Turn 稳定状态与 Run 可变状态

新 Turn 首次准备时，`StableTurnContextSnapshot` 冻结：

```text
Turn.root_task
+ governing System Prompt profile
+ Project Instructions
+ selected Skill cards and tool names
+ Long-Term Memory recall snapshot
+ base Tool schemas
+ Runtime compatibility contract
```

Same-Turn resume 恢复同一 Snapshot；新 Turn 才重新发现这些稳定输入。仓库正文、Working Memory 和当前 Tool route 属于每个 Model Step 的动态事实。

`AgentRunSession` 则保存一个 Run 的有界可变视图：

```text
identity              thread_id / turn_id / root_task / turn_focus
conversation view     messages / observations / message_sequences
stable input          stable prefix / base tools / selected skills / memory snapshot
run-local state       working_memory / evidence / controller / usage / stop state
```

`root_task` 来自当前 `Turn.root_task`；clarification 可更新 `turn_focus`，但不创建第二份 root-task authority。

# 6. 一次 Model Step 的冻结输入

```text
AgentRunSession
→ ModelStepPreparation.prepare_model_step()
    ├── ToolRouter.route()
    ├── dynamic repository context
    └── PromptWindowManager.prepare()
→ PreparedModelStep
    ├── llm_messages
    ├── tool_schemas
    ├── allowed_tool_names
    ├── phase
    └── prompt metrics
→ ModelPort.chat(...)
```

`llm_messages` 由稳定 System Context、当前动态 Context、可选 `ConversationHistoryDigest`、protocol-preserving recent tail 和可选 transient control 组成。超长历史的详细规则由 [上下文压缩与长任务设计](上下文压缩与长任务设计.md) 负责。

# 7. Follow-up 与 Resume 样例

```text
Turn A: “修复 auth refresh bug”
└── Run A1 → waiting_human
    └── durable assistant batch + pending cursor

Resume
└── same Turn A / Run A2
    └── restore snapshot + continue original pending batch

Turn A completed
└── user: “再补 regression test”
    └── new Turn B / Run B1 / new stable snapshot
```

新 Turn B 的 `root_task` 是“再补 regression test”。Turn A 的完整事件仍在 journal 中，但不继续拥有 Turn B 的 current authority。

# 8. 恢复边界

ToolCall batch 的恢复状态由 `PendingExecutionPointer` 表达：

```text
assistant_item_id
next_tool_call_index
pending_operation_key
pending_operation_fingerprint
```

Runtime 先持久化完整 assistant batch，再顺序执行调用。Resume 读取原 batch 和 cursor；Observation 已写但 cursor 未推进时只修复 cursor；副作用已启动而结果未知时 fail closed。完整执行协议见 [运行治理与工具执行](运行治理与工具执行.md)，物理 artifact 见 [运行产物与持久化契约](运行产物与持久化契约.md)。

# 9. 源码入口（Source Anchors）

- `agent_forge/runtime/domain/thread.py::ConversationThread`：Thread、Turn、Run 与 Context State 领域事实。
- `agent_forge/runtime/adapters/thread_json.py::JsonConversationThreadRepository`：Conversation journal 与 Context State persistence。
- `agent_forge/harness.py::Harness.run()`：身份绑定与 Single-Agent composition root。
- `agent_forge/runtime/application/run_preparation.py::RunPreparation`：Session 与 Stable Turn Snapshot。
- `agent_forge/runtime/application/session.py::AgentRunSession`：Run-local mutable state。
- `agent_forge/runtime/application/model_step_preparation.py::PreparedModelStep`：单次模型输入契约。
- `agent_forge/runtime/application/tool_execution.py::ToolExecutionPipeline`：durable batch 与 cursor recovery。
- `agent_forge/runtime/application/run_lifecycle.py::RunLifecycle.finalize_run()`：终态提交。

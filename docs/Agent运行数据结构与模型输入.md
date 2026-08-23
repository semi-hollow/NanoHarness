# Agent 运行数据结构与模型输入

> 本文说明一次 Run 中谁持有状态、状态如何跨 Turn 流动，以及模型最终收到什么。
> System Context 的内容和 Conversation 压缩算法分别由对应文档展开。

# 1. 五个核心对象

| 对象 | 生命周期 | 输入 | 输出 / 作用 |
|---|---|---|---|
| `AgentRunSession` | 整个 Run | task、lifecycle、controller | messages、working state、rolling digest/cursor、memory catalog |
| `WorkingMemory` | 整个 Run | continuation seed、recall snapshot、Observation | 当前 Run 的有界任务状态视图 |
| `PreparedTurn` | 单个 Turn | system context、history、tool route、budget | 一次 `ModelPort.chat(...)` 的冻结输入 |
| `TaskCheckpoint` | durable latest state | lifecycle transition | crash/pause/resume 使用的控制面快照 |
| `ConversationHistoryDigest` | 跨增量 compaction，可随 checkpoint 恢复 | previous digest + raw delta | 已覆盖 Conversation 的结构化投影 |

关键边界：

```text
AgentRunSession        ≠ TaskCheckpoint
live mutable state       durable latest-state snapshot

WorkingMemory          ≠ Long-term Memory Repository
current Run view         cross-Run persisted records

PreparedTurn           ≠ AgentRunSession
one-call snapshot        complete live Run state
```

# 2. 对象所有权（Ownership Graph）

```text
AgentLoop
└── AgentRunSession
    ├── messages[]                         Conversation History
    ├── observations[]                     typed Tool facts
    ├── working_memory                     bounded current-Run view
    ├── conversation_history_digest         rolling derived projection
    ├── compacted_message_cursor            current-session raw index
    ├── memory_management_catalog           frozen remember candidates
    ├── lifecycle ────────────────→ TaskCheckpoint Repository
    ├── controller                         budget / failure / repetition
    ├── evidence                           final-answer grounding
    └── active_skills[]
            │
            │ current Turn reads a snapshot
            ▼
TurnPreparation.prepare_turn()
└── PreparedTurn
    ├── turn_system_message
    ├── llm_messages
    ├── tool_schemas
    ├── allowed_tool_names
    ├── prompt metrics
    └── conversation_history_digest?
```

`AgentRunSession` 保存数据，不拥有策略。Turn 控制在 `AgentLoop`，模型输入治理在
`TurnPreparation`，工具治理在 `ToolExecutionPipeline`，持久化在 `RunLifecycle`。

# 3. 一个 Turn 的状态流

```text
AgentRunSession
    ↓
TurnPreparation.prepare_turn()
    ├── checkpoint current turn boundary
    ├── ToolRouter.route()
    ├── TurnSystemContextAssemblerPort.build()
    └── PromptWindowManager.prepare()
    ↓
PreparedTurn
    ↓
ModelPort.chat(
    PreparedTurn.llm_messages,
    PreparedTurn.tool_schemas,
)
    ↓
AgentResponse
    ├── final text → StopRequest
    └── ToolCall
          ↓
       Observation
          ├── session.observations
          ├── WorkingMemory
          ├── Evidence / Trace / Checkpoint
          └── if Run continues → session.messages(role=tool)
```

每个 Turn 都重新生成 `PreparedTurn`；上一个 Turn 的快照不会被原地修改后复用。

# 4. Conversation 与临时控制消息

当前进程 Conversation 的 Owner 是：

```text
AgentRunSession.messages
```

当前模型窗口的初始历史则是：

```python
conversation_history = list(session.messages)
```

`TurnPreparation` 将当前 Turn 的预算控制 Message 作为
`PromptWindowRequest.transient_messages` 单独传入。该临时 Message 不写回
`session.messages`，也不进入 conversation digest 或 cursor。

Runtime 先把本轮保留的 assistant ToolCall 写入 Session。产生 Observation 的拒绝、
回放或执行分支再按各自边界写入 tool Message；重复副作用等停止分支可以直接返回
`StopRequest` 而不伪造 Observation。正常执行结果只有在预算允许继续时才成为下一
Turn 的 tool Message。

# 5. WorkingMemory 的边界

`WorkingMemory` 聚合：

- 当前 Run 的显式键值；
- 最近普通事实与类型化 Observation；
- 被移出最近窗口的短摘要；
- Run 启动时固定的长期记忆召回快照；
- continuation 注入的旧运行摘要。

它不负责：

- 保存完整 Conversation；
- 写长期记忆 Repository；
- 决定 Prompt Window compaction；
- 持久化 TaskCheckpoint。

# 6. Checkpoint 与 Digest

`TaskCheckpoint` 只保存继续执行所需的最小控制事实，包括 identity、status、current
step、最近 Tool/Observation、stop/resume 信息、计数和可选 digest。它不保存 Python
stack、Provider 连接、完整 `session.messages` 或模型 hidden state。

`ConversationHistoryDigest` 在第一次实际 compaction 时产生，后续只合并尚未覆盖的
current-session raw delta：

```text
previous digest + new raw segments after current cursor
    ↓
ConversationHistoryDigest
    ├── initial task
    ├── task updates
    ├── tool transactions / failures
    └── covered-message provenance
```

Digest 被写入 checkpoint 作为 historical projection；原始消息继续留在当前 live
Session，不因构造模型窗口而被删除。`covered_message_count` 是跨合并累计审计数，
`AgentRunSession.compacted_message_cursor` 才是当前 Session list index。Trace 只记录
窗口度量、source hash 和压缩事实，不保存完整 Prompt 正文或完整 raw Conversation。

# 7. Resume 是新的 continuation

```text
Old TaskCheckpoint
    ├── summarize_checkpoint() → WorkingMemory continuation seed
    └── conversation_history_digest
              ↓ ConversationHistoryDigest.from_dict()
       NEW AgentRunSession.conversation_history_digest
       NEW AgentRunSession.compacted_message_cursor = 0
```

Resume 继承显式持久化事实。下一次 compaction 把 restored digest 与新 Session 从 index 0
开始的 raw delta 合并；它不声称恢复旧 raw tail、旧进程对象或完整模型状态。

## 源码入口（Source Anchors）

- `agent_forge/runtime/application/session.py::AgentRunSession`：跨 Turn live state。
- `agent_forge/runtime/application/working_memory.py::WorkingMemory`：当前 Run 的有界状态视图。
- `agent_forge/runtime/application/turn_preparation.py::PreparedTurn`：单次模型输入契约。
- `agent_forge/runtime/application/turn_preparation.py::TurnPreparation.prepare_turn()`：Turn 数据汇合点。
- `agent_forge/runtime/domain/conversation.py::Message / Observation`：对话与工具事实。
- `agent_forge/runtime/domain/task.py::TaskCheckpoint`：durable latest-state snapshot。
- `agent_forge/context/domain/conversation_digest.py::ConversationHistoryDigest`：历史压缩投影。
- `agent_forge/runtime/application/run_preparation.py::RunPreparation._load_resume_state()`：continuation summary 与 digest 恢复。

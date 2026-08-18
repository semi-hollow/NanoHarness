# Agent 运行数据结构与模型输入

> 这份文档专门解决最容易混淆的一组概念：  
> `AgentRunSession`、`WorkingMemory`、`PreparedTurn`、LLM 两个入参、`TaskCheckpoint`、`ConversationHistoryDigest`。

---

# 1. 先记四句话

```text
AgentRunSession
= 一个 Run 跨 Turn 存活的 live mutable state

WorkingMemory
= Session 里专门保存当前任务认知状态的一块 bounded view

PreparedTurn
= 当前这一轮冻结后的 LLM input snapshot

TaskCheckpoint
= Crash / Pause / Resume 使用的 durable control-plane snapshot
```

`ConversationHistoryDigest`：

```text
= 旧 Conversation History 的有界结构化投影
≠ 整个 AgentRunSession 的摘要
```

---

# 2. 完整数据关系图

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ AgentRunSession                         跨 Turn 存活                        │
│                                                                             │
│ task / agent_name / workspace_root                                          │
│ iteration / max_iterations                                                  │
│                                                                             │
│ messages[]                ← Conversation History                            │
│ observations[]            ← typed Tool Observations                         │
│                                                                             │
│ working_memory            ← bounded task-state read model                   │
│ active_skills[]                                                           │
│ skill_tool_names                                                          │
│ evidence                                                                    │
│                                                                             │
│ lifecycle                 ← checkpoint / resume / finalization               │
│ controller                ← budget / repeat / failure control                │
│                                                                             │
│ ran_tests / blocked / cost / status / stop_*                                │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
                               │ 每个 Turn 读取当前 Session
                               │ + 当前 Workspace/Repo
                               │ + ToolRoute / Permission / Budget
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TurnPreparation                                                             │
│                                                                             │
│ ① route tools                                                               │
│ ② build Current Turn System Context                                         │
│ ③ build Conversation Window                                                 │
│ ④ enforce Prompt Budget                                                     │
│ ⑤ produce PreparedTurn                                                      │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ PreparedTurn                           单 Turn 临时快照                       │
│                                                                             │
│ turn_system_message                                                        │
│   ├ repo / current workspace context                                        │
│   ├ WorkingMemory projection                                                │
│   ├ active Skill cards                                                      │
│   ├ permission summary                                                      │
│   └ runtime/global instructions                                             │
│                                                                             │
│ llm_messages[]                                                              │
│   ├ [0] turn_system_message                                                 │
│   └ [1..] Conversation Window                                               │
│         ├ normal: session.messages                                          │
│         └ compacted: ConversationHistoryDigest + recent raw messages        │
│                                                                             │
│ tool_schemas[]             ← 当前 Turn ToolRouter 选择                      │
│ allowed_tool_names / phase / estimated_prompt_tokens                        │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
                    ┌──────────────────────────┐
                    │ LLM.chat(                │
                    │   llm_messages,          │
                    │   tool_schemas           │
                    │ )                        │
                    └──────────────────────────┘


                 ───────── durable recovery side path ─────────

AgentRunSession / Turn / Tool execution
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ TaskCheckpoint                         durable latest-state snapshot         │
│                                                                             │
│ Identity                                                                    │
│   run_id / task / workspace / agent_name                                    │
│                                                                             │
│ Execution Snapshot                                                          │
│   status / current_step                                                     │
│   last_tool / last_observation                                              │
│   stop_reason / stop_output / final_answer / resume_hint                    │
│   messages_count / observations_count                                       │
│                                                                             │
│ Historical Projection                                                       │
│   conversation_history_digest                                               │
│                                                                             │
│ metadata / timestamps                                                       │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ crash / pause / explicit resume
                               ▼
                     summarize_checkpoint(...)
                               │
                               ▼
                         resume_summary
                               │
                               ▼
                      NEW AgentRunSession
                               │
                               └─ WorkingMemory.seed_session(...)
```

---

# 3. Session 和 WorkingMemory 到底差在哪

```text
AgentRunSession
├── Conversation
├── Observation
├── WorkingMemory
├── Skill
├── Lifecycle
├── Controller
├── Evidence
└── Run status / cost
```

而：

```text
WorkingMemory
├── recent items
├── recent typed observations
├── bounded summaries
├── run-local KV
└── recalled long-term-memory snapshot
```

所以：

```text
Session ⊃ WorkingMemory
```

WorkingMemory 不是“整个 Agent 的 Memory 总称”。

---

# 4. Current Turn System Context 是什么

它不是 Session。

它是当前 Turn 临时生成的一条：

```text
Message(role="system")
```

内容来自：

```text
Current Turn System Context
├── Repo / Workspace Context
├── WorkingMemory projection
├── Skills
├── Permissions
└── Instructions
```

它不持久化。

下一 Turn 根据最新状态重新 assemble。

---

# 5. LLM 真正只有两个主入参

```python
llm.chat(
    prepared_turn.llm_messages,
    prepared_turn.tool_schemas,
)
```

其中：

```text
llm_messages
=
Current Turn System Context
+
Conversation Window
```

正常：

```text
system
user
assistant
tool
assistant
tool
...
```

压缩后：

```text
system: Current Turn System Context
system: ConversationHistoryDigest
recent user / assistant / tool ...
```

Tool Schema 是第二个独立入参，不属于 `session.messages`。

---

# 6. ConversationHistoryDigest 的真实来源

输入：

```text
session.task
+
old session.messages segments
+
aligned session.observations
```

输出：

```text
ConversationHistoryDigest
├── task
├── covered_message_count
├── source_hash
├── task_updates[]
├── tool_transactions[]
├── failed_tool_evidence[]
├── estimated_tokens_before
├── estimated_tokens_after
└── created_at
```

字段来源：

```text
task_updates
← 旧 user messages 中的后续 task/steer 变化

tool_transactions
← assistant ToolCall + matching tool message + typed Observation

failed_tool_evidence
← failed tool transactions 的派生视图
```

所以这些不是四套互相独立的输入。

---

# 7. 为什么 Digest 里没有 messages[]

因为 Digest 是 projection：

```text
M1 ... M80
    ↓
extract durable meaning
    ↓
ConversationHistoryDigest
```

原始 `messages[]` 仍在 live Session / Trace。

Digest 只保留：

```text
“做过什么”
“哪些结果成功/失败”
“任务约束怎么变化”
“覆盖了多少历史”
“原历史指纹是什么”
```

---

# 8. 当前正常 Turn 不读取上轮 Checkpoint Digest

当前行为：

```text
Turn N

完整 session.messages
↓
PromptWindowManager
↓
若超限
  build ConversationHistoryDigest-N
  ├→ 本轮 LLM
  └→ TaskCheckpoint
```

Turn N+1：

```text
完整 session.messages
↓
重新计算
```

不会：

```text
checkpoint.conversation_history_digest
↓
继续 incremental merge
```

因此当前 Digest 的两个用途是：

```text
① 当前 Turn History compaction result
② Crash/Resume 的 durable historical projection
```

不是下一 Turn 的 rolling state。

---

# 9. Resume 是 continuation，不是 Session resurrection

```text
Old Run
↓
TaskCheckpoint
↓
summarize_checkpoint
↓
resume_summary
↓
NEW AgentRunSession
↓
WorkingMemory.seed_session
```

不会恢复：

```text
old session.messages
old Python stack
old HTTP connection
old model hidden state
```

因此要准确说：

> Checkpoint 提供 continuation context 和 execution snapshot，不是完整 live object serialization。

---

# 10. 字段命名原则

代码中推荐：

```text
PreparedTurn.turn_system_message
PreparedTurn.llm_messages
PreparedTurn.tool_schemas
PreparedTurn.conversation_history_digest

TurnPreparation.turn_system_context
TurnPreparation.conversation_history
PromptWindowManager
ConversationHistoryDigest

TaskCheckpoint.conversation_history_digest
```

不要再使用容易跨层混淆的：

```text
Runtime Context
context_message
messages_for_llm
schemas
SessionDigest
```

作为核心业务术语。

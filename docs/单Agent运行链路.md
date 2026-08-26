# 单 Agent 运行链路

本文聚焦：

```text
一个 Turn 怎样从创建一路运行到完成？
Same-Turn Resume 恢复什么、不恢复什么？
各 Runtime owner 的职责怎样分开？
```

> Single-Agent Runtime 负责把一个顶层用户目标转成一系列受治理的 Model Step，并在完成、等待人工或中断时留下可恢复状态。

# 1. 总体流程

```text
用户目标
  ↓
建立可恢复执行
  ↓
准备当前任务
  ↓
模型产生下一步
  ↓
Runtime 处理动作或结果
  ↓
继续 / 等待人工 / 完成
```

这一层只表达生命周期；具体副作用治理单独见 [运行治理与副作用](运行治理与副作用.md)。

# 2. 建立执行

新任务先建立：

```text
Thread
Turn
Run
Execution workspace
```

然后：

```text
create CREATED checkpoint
→ claim Turn / current Run ownership
→ enter AgentLoop
```

先有 durable Run bootstrap，再发布 ownership，可以避免 Thread 指向一个完全没有恢复状态的 Run。

# 3. Durable State → `AgentRunSession`

`RunPreparation.create_session()`：

```text
canonical Turn
+ ThreadContextState
+ TaskCheckpoint
+ uncovered Conversation tail
        ↓
AgentRunSession
```

主要步骤：

```text
确认 Thread / Turn / Run identity
→ 读取 durable Context revision
→ 恢复 Run execution cursor
→ 加载 covered_sequence 之后的 Conversation
→ 恢复 StableTurnContextSnapshot
```

Owner 边界：

```text
Turn.root_task
= 任务目标 truth

ThreadContextState
= Context durable state

TaskCheckpoint
= Run execution state

AgentRunSession
= 当前进程的工作投影
```

# 4. Stable Turn Snapshot

Fresh Turn：

```text
build StableTurnContextSnapshot
→ persist into ThreadContextState
→ project into AgentRunSession
```

Same-Turn Resume：

```text
load existing snapshot
→ validate contract
→ restore
```

不重新 discovery：

```text
System Prompt
Project Instructions
Skill selection
LTM recall
base Tool schemas
```

具体见 [上下文工程与长任务](上下文工程与长任务.md)。

# 5. Pre-Run Governance

`RunPreparation.prepare_run()`：

```text
input policy
→ clarification if needed
→ refresh Conversation view
```

Clarification 更新：

```text
AgentRunSession.turn_focus
```

但不改写：

```text
Turn.root_task
StableTurnContextSnapshot
```

所以初始目标与后续人类约束不会混成第二份 root task。

# 6. AgentLoop

```text
有未完成 Tool batch？
  ├─ yes → 先 Resume pending execution
  └─ no
       ↓
准备 Model Step
       ↓
Model
       ↓
ToolCalls ? ─ yes → Run Governance → 下一循环
       │
       no
       ↓
Final Answer governance
       ↓
terminal
```

关键设计：

> **Durable old work 优先于生成 new work。**

Resume 后不会先请求模型重新规划，再丢掉上一 Run 已经持久化的 Tool batch。

# 7. Model Step

`ModelStepPreparation.prepare_model_step()` 汇合：

```text
Stable Turn Context
+ current dynamic repository context
+ governed Conversation window
+ Tool route / schemas
        ↓
PreparedModelStep
        ↓
ModelPort.chat(...)
```

模型输出：

```text
ToolCalls
→ ToolExecutionPipeline

Final text
→ FinalAnswerBuilder
```

# 8. Run 状态与 Same-Turn Resume

Run 可以进入：

```text
running
waiting_approval
waiting_human
paused
completed
failed / blocked
```

Resume：

```text
old Run
  ↓
new continuation Run
  ↓
same thread_id
same turn_id
new run_id
  ↓
copy minimal execution cursor
```

它不是恢复 Python call stack，也不是创建新用户目标。

# 9. Ownership

```text
Harness
= composition / identity / execution environment

RunPreparation
= durable state → Session

AgentLoop
= Run / Model-Step ordering

ModelStepPreparation
= one-step model input

ToolExecutionPipeline
= Tool batch lifecycle

RunLifecycle
= checkpoint / HITL / terminal transitions
```

# 10. 核心不变量

```text
1. Turn.root_task 是目标唯一 owner
2. Resume = same Turn + new Run
3. Stable Turn inputs 在 same-Turn Resume 中复用
4. Pending Tool batch 优先于新的 Model Step
5. AgentRunSession 是工作投影，不是 durable truth
6. terminal / waiting state 都要有明确 durable recovery contract
```

# 11. 附录：生命周期示例

```text
Thread T
└── Turn A
    ├── Run A1
    │   ├── Step 1
    │   ├── Step 2
    │   └── waiting_approval
    │
    └── Run A2
        ├── resume pending execution
        ├── finish old batch
        └── Step 3...
```

具体 pending cursor / Approval / Ledger 的完整案例见 [运行治理与副作用](运行治理与副作用.md)。

## 源码入口

- `agent_forge/harness.py::Harness.run()` / `Harness.resume()`
- `agent_forge/runtime/application/run_preparation.py::RunPreparation.create_session()`
- `agent_forge/runtime/application/run_preparation.py::RunPreparation.build_stable_turn_context_snapshot()`
- `agent_forge/runtime/application/run_preparation.py::RunPreparation.prepare_run()`
- `agent_forge/runtime/application/agent_loop.py::AgentLoop.run()`
- `agent_forge/runtime/application/model_step_preparation.py::ModelStepPreparation.prepare_model_step()`
- `agent_forge/runtime/application/run_lifecycle.py::RunLifecycle`

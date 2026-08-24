# 多 Agent 编排

> Multi-Agent 把一条自然语言仓库任务转换成受校验的执行图，在隔离 Worker 中复用
> canonical `AgentLoop`，并通过 Runtime gates 集成为一个可信候选结果。

# 1. 输入、输出与系统角色

```text
Input
├── Natural-language Task
└── Git Repository

Output
├── integrated candidate diff
├── WorkerHandoff records
├── Finalizer decision
├── private Worker / Finalizer execution conversations
└── auditable runtime evidence
```

它解决 Planning、调度、隔离执行、协作和集成问题，不复制另一套 Single-Agent Runtime。

# 2. 端到端控制流（End-to-End Control Flow）

Planner 与 Coordinator 由 Application 层组合，不是
`FanoutCoordinator.run()` 内部调用 `AdaptivePlanner.decide()`：

```text
Natural-language Task
    ↓
apps.repository_run._run_adaptive_repository_task()
    ↓
AdaptivePlanner.decide()
    ↓
PlanningOutcome
    ├── single → canonical Single-Agent path
    └── fanout
          ↓
      PlanningDecision.to_fanout_plan()
          ↓
      validated FanoutPlan
          ↓
      build_live_fanout()
          ↓
      FanoutCoordinator.run()
          ↓
      current effective plan generation
       ├── HARD-only       → _run_batch()
       └── has LIVE routes → _run_live_plan()
          ↓
      isolated Worker AgentLoops
          ↓
      Candidate Integration
       ├── success
       └── bounded worker retry / merge recovery / remaining-plan replan
                                                   ↓
                                         next FanoutPlan generation
                                                   ↓
                                      scheduling strategy selected again
          ↓
      read-only Finalizer
          ↓
      LiveFanoutSummary
```

Scheduling strategy is selected per effective plan generation. 一个 Run 可以出现：

```text
Generation 1 → _run_live_plan()
remaining-plan replan
Generation 2 → _run_batch()
```

反向切换同样成立。每代只有一个 active `effective_plan`；completed prefix、initial
identity 和 global acceptance criteria 在 replan 中受 Runtime 保护。

这里的切换同时作用于 Coordinator 调度、`LiveHandoffRuntime` generation 和 Worker
prompt。每代启动前，`FanoutWorkerPort.bind_effective_plan()` 将当前 `effective_plan`
绑定到 `LocalAgentWorkerAdapter`；因此 Task/scope/tools/criteria 与 LIVE route listing
都来自当前 generation，goal 仍按设计保持 initial identity。

# 3. Runtime 对象所有权

```text
Application composition
├── AdaptivePlanner
└── build_live_fanout()
      ├── FanoutCoordinator                 execution/integration owner
      ├── LocalAgentWorkerAdapter           isolated Worker/Finalizer adapter
      └── LiveHandoffRuntime?               LIVE shared-state owner
             ↓
        LiveWorkerContext                   worker-bound identity/route view
             ├── PublishHandoffEventTool
             └── LiveHandoffRunControl
                      ↓
                 canonical AgentLoop
```

- `FanoutPlan`：当前 generation 的 typed execution contract；
- `FanoutCoordinator`：调度、Worker lifecycle、candidate gates、recovery 与 Finalizer；
- `LiveHandoffRuntime`：LIVE route、mailbox、version/freshness 与 delivery facts；
- `LocalAgentWorkerAdapter`：私有 Thread、worktree、受限 Tool registry、AgentLoop、diff 与 validation。

对话所有权不属于 Planner 或 Coordinator：

```text
User ConversationThread (thread_kind=user)
└── top-level user Turn
    └── planning / final top-level result

Worker Task
└── private ConversationThread (thread_kind=worker)
    └── private Turn / Run / Model Steps / Tool Observations

Finalizer Task
└── private ConversationThread (thread_kind=finalizer)
    └── private read-only Turn / Run / Model Steps
```

Worker/Finalizer 的 root input 来自已验证 plan，`origin=runtime_plan` 且
`human_authority=false`。内部 provider history、ToolCall 与 Observation 不写入用户主
Thread，也不能成为用户授权来源。

| 核心概念 | 系统角色 |
|---|---|
| Task | Planner 拆出的、经 Runtime 校验的逻辑工作单元 |
| Worker | 在隔离 worktree 中执行一个 Task 的 Agent execution |
| Batch | HARD-only path 中由 Runtime 推导的并发 Task 分组 |

# 4. 规划（Planning）

```text
task + bounded repository map + available tools
    ↓
AdaptivePlanner.decide()
    ↓
LLM proposes
├── single / fanout
├── worker tasks
├── HARD dependencies
├── write scopes / allowed tools / criteria
└── LIVE routes when useful
    ↓
StructuredOutputParser
    ↓
PlanningDecision
    ↓
FanoutPlan deterministic validation
```

Planner 只提议；结构或领域约束校验失败后至多允许一次 repair，Provider failure 不做
结构修复。无效 plan 不会启动 Worker。Worker prompt 由已校验的当前 generation Task
与 plan facts 确定性渲染，不直接复用模型原文；HARD → LIVE 或 LIVE → HARD replan
后都会重新绑定 route listing。

# 5. 两种 generation 调度策略

## HARD-only 调度：`_run_batch()`

```text
FanoutPlan without LIVE routes
    ↓
build_conflict_free_batches()
    ↓
one batch → concurrent Workers
    ↓
batch barrier
    ↓
next batch
```

这是 barrier-oriented scheduling。`Batch` 是 Runtime 从 HARD 依赖与 write scope
推导的并发分组，不是 Planner 直接指定的执行线程。

## 包含 LIVE：`_run_live_plan()`

```text
pending Tasks
    ↓
dynamic readiness checks
    ↓
ThreadPoolExecutor.submit()
    ↓
concurrent Workers
    ↓
READY / completion changes shared state
    ↓
Coordinator rescans runnable Tasks
```

这是 event-driven dynamic scheduling。for-loop 顺序调用 `submit()` 只表示顺序派发；
`submit()` 立即返回 `Future`，不表示 Worker 顺序执行。

# 6. HARD 与 LIVE

```text
HARD
Producer successfully integrated
    ↓
Consumer may start from trusted code state
```

真实字段是 `SubagentTask.depends_on`。

```text
LIVE
Producer publishes required READY / UPDATE
    ↓
Consumer may start early from semantic evidence

before Consumer integration:
Producer successfully integrated and sealed
+
Consumer consumed Producer final current version
```

真实字段是 `FanoutPlan.live_dependencies`。

核心不变量：LIVE relaxes the execution start barrier, not the final integration trust barrier.
需要上游文件结果时必须使用 HARD；LIVE 只传递版本化语义证据。

# 7. Worker Isolation 与并发

```text
Coordinator Thread
├── FanoutCoordinator
├── integration gates
└── shared LiveHandoffRuntime

Worker Thread A                       Worker Thread B
└── LocalAgentWorkerAdapter          └── LocalAgentWorkerAdapter
    ├── independent AgentLoop            ├── independent AgentLoop
    ├── isolated Git worktree            ├── isolated Git worktree
    ├── private durable Thread/WM         ├── private durable Thread/WM
    └── scoped candidate diff            └── scoped candidate diff
```

Worker 不共享 Conversation、WorkingMemory 或私有 worktree。跨 Worker 只传递：

```text
semantic state → READY / FEEDBACK / UPDATE
trusted code   → successful integration + WorkerHandoff
```

私有 Thread 只负责单个 Worker/Finalizer 的执行连续性与审计。Coordinator 只读取 typed
`SubagentResult`、`WorkerHandoff` 和 candidate diff；它不拼接各 Worker raw Conversation，
也不创建第二套 AgentLoop lifecycle。

复用同一个 `AgentLoop` 不等于复用同一个身份 Prompt。Composition 明确设置：

```text
standalone Runtime → system_prompt_profile=single_agent
Worker Runtime     → system_prompt_profile=fanout_worker
Finalizer Runtime  → system_prompt_profile=fanout_finalizer
```

因此通用 Tool/Context/Checkpoint 控制流只有一套，而 Worker 的隔离职责与 Finalizer 的
只读职责仍在模型输入中明确可见；具体 task/scope/routes/criteria 来自当前有效计划。

# 8. Live Handoff 与 AgentLoop safe boundary

```text
Worker ToolCall: publish_handoff_event
    ↓
PublishHandoffEventTool
    ↓
worker-bound LiveWorkerContext
    ↓
LiveHandoffRuntime.publish()
    ├── route / event type / version / causality validation
    └── target mailbox + coordination evidence
    ↓
LiveHandoffRunControl.drain_coordination()
    ↓
RunControlHandler.consume_pending_signals()
    ↓
peer AgentLoop safe model boundary
```

协议语义：

| Event | Direction | 作用 |
|---|---|---|
| READY | Producer → Consumer | 解锁提前执行 |
| FEEDBACK | Consumer → Producer | 返回运行中发现的新约束 |
| UPDATE | Producer → Consumer | 发布吸收 FEEDBACK 后的新版本 |

正常 provider request 期间到达 coordination 时：

```text
old model response returns
→ after_model safe boundary detects changed input
→ discard response before Tool side effects
→ next Model Step uses latest coordination
```

Runtime coordination 与 operator steer 使用同一 safe-boundary mechanics，但拥有不同
domain signal、trace provenance 和 authority。采用 `role=user` 只是 transport
encoding；消息明确标记 `human_authority=false`。

当前 context-overflow recovery 的立即第二次 provider call 返回后不会再次执行同一个
`after_model` signal check；若 Run 继续，该重试期间到达的 coordination 保留到下一
model boundary，若本次响应终止 Run，则本 Run 不再消费。这是当前 failure-safety
边界，不应把正常调用路径的 stale-response 保证扩大到该窄例外。

# 9. Integration、Recovery 与 Finalizer

```text
Worker candidate diff
    ↓
declared scope / actual touched files
    ↓
dynamic conflict checks
    ↓
LIVE freshness authorization
    ↓
git applicability
    ↓
stable integration + WorkerHandoff
```

失败恢复保持有界：

- 每个 Task 的首次 retryable failure 至多一次 Worker retry；
- 每个 candidate merge conflict 至多一次串行 recovery；
- 整个 Run 至多一次 remaining-plan replan；
- stale coordination 只 detect + reject，不自动无限重跑。

Replan 只替换 remaining work。新 `FanoutPlan` 进入下一 generation 后重新选择
`_run_batch()` 或 `_run_live_plan()`。

只有所有 Task 成功、无 unresolved conflict 时才运行 `run_finalizer()`。Finalizer
使用独立 worktree、只读 Tool surface 和 dry-run 权限；明确 PASS 且没有新增 Diff 才通过。

# 10. 设计边界（Design Boundaries）

- Agent-level Turn REPLAN 与 plan-level remaining-task replan 是两个控制环；
- LIVE early start 不绕过最终 integration trust gates；
- Runtime coordination 不获得 human authority；
- Planner / Coordinator 不拥有 AgentLoop Conversation；
- Worker / Finalizer 使用私有 durable Thread，用户主 Thread 不承载内部 raw history；
- per-generation scheduling 与 Worker prompt 都绑定当前 `effective_plan`；
- HARD-only checkpoint 支持已有 resume；LIVE mailbox/in-flight state 不提供 replay；
- 当前证据验证 deterministic mechanism 与真实 AgentLoop integration，不构成真实模型性能结论。

唯一公开机制证据见
[`multi-agent-v1/mechanism-evidence.json`](../benchmarks/experiments/multi-agent-v1/mechanism-evidence.json)：
它验证当前控制闭环，不评估真实模型性能，也不提供性能提升 claim。

## 源码入口（Source Anchors）

- `apps/repository_run.py::_run_adaptive_repository_task()`：Planner 与执行器的 Application composition。
- `agent_forge/multi_agent/application/planning.py::AdaptivePlanner.decide()`：自然任务到策略提议。
- `agent_forge/multi_agent/domain/planning.py::PlanningDecision.to_fanout_plan()`：提议到 canonical plan。
- `agent_forge/multi_agent/wiring.py::build_live_fanout()`：Coordinator、Worker 与 LIVE Runtime 装配。
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator.run()`：per-generation 调度与集成闭环。
- `agent_forge/multi_agent/ports/live.py::FanoutWorkerPort.bind_effective_plan()`：当前 generation 到 Worker prompt 的绑定边界。
- `agent_forge/multi_agent/application/live_handoff.py::LiveHandoffRuntime`：协作状态与 freshness Owner。
- `agent_forge/multi_agent/adapters/local_worker.py::LocalAgentWorkerAdapter`：私有 durable Thread、隔离 Worker 与 Finalizer。
- `agent_forge/runtime/application/agent_loop.py::AgentLoop.run()`：Worker 复用的 canonical Runtime。
- `agent_forge/runtime/domain/thread.py::ConversationThread`：user/worker/finalizer 三类 Thread 的共同 durable contract。

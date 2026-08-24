# 多 Agent 编排

> NanoHarness 对 repository task 只提供 `Single` 和 `Ultra` 两种执行策略。
> Single 直接进入 canonical `AgentLoop`；Ultra 先规划，再选择同一
> Single 路径或受治理 Multi-Agent Runtime。

# 1. 输入、输出与系统角色

```text
Input
├── Natural-language repository task
└── Git repository

Output
├── integrated candidate diff
├── WorkerHandoff records
├── Finalizer decision
└── auditable runtime evidence
```

Multi-Agent 解决的是规划、依赖调度、隔离执行、受限协作和候选集成。
它不复制 Single-Agent Runtime：每个 Worker 内部仍是同一个 `AgentLoop`。

# 2. Single / Ultra 唯一公开主线

```text
Repository Task
    │
    ▼
run_repository_task()
    ├── Single
    │     └── execute_single_repository_task()
    │             └── Harness.run() → AgentLoop.run()
    │
    └── Ultra
          └── _run_ultra_repository_task()
                 └── AdaptivePlanner.decide()
                        └── PlanningDecision
                               ├── single → 同一 Harness.run()
                               └── multi
                                      └── validated FanoutPlan
                                             └── FanoutCoordinator.run()
```

Ultra 不是“强制 Multi-Agent”，而是“强制先 Planning”。以下情况都回到
canonical Single path：

- Planner 选择 `single`；
- Multi 提议只有一个有效 Task；
- 首次规划经一次 repair 后仍非法。

`fanout` 仍是内部 Domain / artifact 术语，不是第三种用户模式。

# 3. 受治理规划（Governed Planning）

```text
LLM proposal
    ↓
Schema validation          JSON 结构和字段类型
    ↓
Domain validation          mode / task count / tools / budgets
    ↓
FanoutPlan graph validation
unknown task / duplicate dependency / cycle / HARD-LIVE composition
```

`AdaptivePlanner._request()` 让 LLM 提议，Runtime 拥有三层 validation authority。
校验失败时，Runtime 把明确 validation error 编入 repair prompt，让同一个
LLM 最多修复一次。首次 Planning 再失败则 fallback to Single；remaining-plan
replan 再失败则 fail closed，不执行半合法 Plan。

> 核心原则：LLM proposes, Runtime validates and governs; LLM repairs,
> 修复信息：Runtime tells it exactly what is invalid.

# 4. Runtime 对象所有权

```text
Application composition
├── AdaptivePlanner                  planning proposal owner
└── build_live_fanout()
      ├── FanoutCoordinator             scheduling/integration owner
      ├── LocalAgentWorkerAdapter       Worker/Finalizer adapter
      └── LiveHandoffRuntime?           LIVE typed-state owner
```

- `FanoutPlan`：current generation 的 typed execution contract；
- `FanoutCoordinator`：readiness、Worker lifecycle、retry/replan、candidate gate、integration 和 Finalizer；
- `LocalAgentWorkerAdapter`：私有 Thread、隔离 worktree、受限 Tool registry、AgentLoop 和 candidate diff；
- `LiveHandoffRuntime`：LIVE route、mailbox、version/freshness 和 delivery facts。

Planner 和 Coordinator 不拥有 AgentLoop Conversation。Worker/Finalizer 使用私有
`ConversationThread`；用户主 Thread 不灌入内部 raw token/tool history。

# 5. 唯一 Dependency-Aware Scheduler

`FanoutCoordinator.run()` 对每个 effective plan generation 都调用同一个
`_run_plan()`：

```text
pending Tasks
    ↓
is runnable?
├── all HARD predecessors successfully integrated
├── all inbound LIVE routes ready, when present
├── no write-scope conflict with running Tasks
├── running count < max_workers
└── not already executed in this generation
    ↓
launch isolated Worker
    ↓
Worker completion / LIVE event / integration
    ↓
update state and rescan pending Tasks
```

HARD 和 LIVE 是两种 dependency semantics，不是两个 scheduler mode。Replan 从
HARD 切到 LIVE 或从 LIVE 切到 HARD 时，scheduler 不变，只替换 effective plan。

## 为什么不再有 Batch 主链

Coordinator 的调度粒度是 Task / Worker execution，不是 Worker 内部的
model/tool iteration。纯 HARD DAG 自然可能表现为 wave：

```text
A ─┐
   ├─ HARD → C
B ─┘

initial: A/B runnable, C blocked
A integrated only: C still blocked
A/B integrated: C runnable
```

`summary.batches` 为了旧 artifact reader 仍保留，但它只投影实际 launch waves；
Batch 是观测结果，不再是 Runtime abstraction。

# 6. HARD 与 LIVE

## HARD：依赖可信的已集成代码状态

```text
Producer Worker succeeds
    +
candidate passes scope / applicability / conflict gates
    +
candidate enters trusted integration workspace
    ↓
Consumer becomes runnable
```

HARD 不是“上游 Agent 跑完就行”，也不是“收到 handoff 就行”。下游需要
基于上游真实代码修改继续时，必须用 HARD。

## LIVE：基于语义信息提前启动

```text
Producer publishes READY / UPDATE (versioned semantic fact)
    ↓
Consumer may start early

before Consumer integration
├── Producer eventually integrated and sealed
├── Consumer consumed Producer final current version
├── scope / conflict / applicability remain valid
└── candidate gate still passes
```

> 核心口径：HARD depends on trusted integrated code state. LIVE allows semantic
> 协作边界：early coordination only relaxes the start barrier, not the integration trust barrier.

# 7. Worker Isolation 与 Live Handoff

```text
Worker A                            Worker B
├── private ConversationThread      ├── private ConversationThread
├── private WorkingMemory           ├── private WorkingMemory
├── isolated Git worktree          ├── isolated Git worktree
└── canonical AgentLoop            └── canonical AgentLoop
             \                        /
              typed coordination state
```

Worker 不共享 Conversation History、WorkingMemory、private Agent state、worktree
或 full prompt context。LIVE 只共享受约束的 typed state：

```text
producer_task_id / target_task_id / semantic_key
event_type / version / summary / evidence
```

`publish_handoff_event` 经 worker-bound identity、route、event type、version 和 causality
校验后才进入 mailbox。`LiveHandoffRunControl` 在 canonical AgentLoop safe model
boundary 投递 coordination；它使用 `human_authority=false`，不伪装成 operator steer。

# 8. Candidate Integration、Recovery 与 Finalizer

```text
Worker candidate
    ↓
actual touched files ⊆ declared write scope
    ↓
optional LIVE freshness authorization
    ↓
git applicability check
    ↓
apply to integration workspace
    ↓
WorkerHandoff + trusted integrated state
```

`_integrate_result()` 是 HARD / LIVE 共用的 candidate gate。失败恢复保持有界：

- 每个 Task 的首次 retryable failure 至多一次 Worker retry；
- 每个 candidate merge conflict 至多一次从最新 workspace 串行恢复；
- 整个 Run 至多一次 remaining-plan replan；
- stale LIVE version 只 detect + reject，不自动无限重跑。

只有所有 Task 成功进入 trusted integrated state 且无 unresolved conflict，才启动
`LocalAgentWorkerAdapter.run_finalizer()`。Finalizer 使用私有、只读 AgentLoop，只做
semantic verification，不 merge，不修代码。

# 9. Resume 与证据边界

- HARD-only Plan 走同一 `_run_plan()`，不创建 `LiveHandoffRuntime`，仍支持
  checkpoint/resume；
- 真正含 LIVE route 的 generation 才创建 mailbox；V1 不支持 durable mailbox
  replay/resume；
- `fanout/`、`fanout_plan.json`、`fanout_summary.json`、`batches`、`batch_index`
  仍保留为内部/历史 artifact contract，不代表公开执行模式；
- 当前证据验证 deterministic mechanism 与真实 AgentLoop integration，不构成
  real-model performance claim。

历史 Lab 2 仍如实展示当时 observed static batches；Workbench 的当前源码入口
指向 unified scheduler，不改写历史 raw evidence。

# 10. 代码阅读顺序（Source Anchors）

1. `apps/repository_run.py::run_repository_task()`：Single / Ultra 公开路由。
2. `apps/repository_run.py::_run_ultra_repository_task()`：Planner 与 Single/Multi 执行的 Application composition。
3. `agent_forge/multi_agent/application/planning.py::AdaptivePlanner.decide()`：自然任务到受治理提议。
4. `agent_forge/multi_agent/domain/planning.py::PlanningDecision`：`single | multi` typed decision。
5. `agent_forge/multi_agent/domain/live.py::FanoutPlan`：HARD/LIVE 组合图 validation。
6. `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator.run()`：编排主链。
7. `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._run_plan()`：唯一 readiness scheduler。
8. `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._integrate_result()`：candidate trust gate。
9. `agent_forge/multi_agent/application/live_handoff.py::LiveHandoffRuntime`：LIVE mailbox/version/freshness。
10. `agent_forge/multi_agent/adapters/local_worker.py::LocalAgentWorkerAdapter.run_worker()`：隔离 Worker AgentLoop。
11. `agent_forge/multi_agent/adapters/local_worker.py::LocalAgentWorkerAdapter.run_finalizer()`：只读最终验收。

核心口径：

```text
Ultra lets the model propose Single or Multi execution, while the Runtime validates
the plan and owns one dependency-aware scheduler. HARD waits for trusted integrated
code; LIVE permits versioned semantic early coordination without weakening the final
integration gate.
```

# 多 Agent 编排

> 本文回答一个问题：NanoHarness 当前的 Live Fanout 怎样把已经结构化的子任务，变成可隔离、可并发、可验证、可稳定集成的多个 Agent 执行。

---

## 1. 当前能力边界

NanoHarness 当前的 Live Fanout 不是：

```text
一个 Main Agent
↓
自动拆自然语言任务
↓
多个共享 Memory 的 Subagent
```

当前实现是：

```text
Typed FanoutPlan
↓
LiveFanoutCoordinator
↓
Dependency / Ready Level
↓
Conflict-free Batch
↓
ThreadPoolExecutor
↓
Independent Worker AgentLoops
↓
Isolated Worktrees
↓
LiveSubagentResult + Candidate Diff
↓
Four Conflict Gates
↓
Stable Integration
↓
Read-only Finalizer
```

其中：

```text
Coordinator
= deterministic orchestrator
≠ LLM
≠ Main Agent
≠ shared WorkingMemory
```

当前已经实现的是 Planning 之后的 Orchestration / Execution half。

---

## 2. FanoutPlan：并发执行前的结构化契约

当前 Coordinator 不把一段自然语言 Task 自动拆成多个 Worker。它接收已经结构化的：

```text
FanoutPlan
├ goal
└ tasks[]
   ├ id
   ├ task
   ├ depends_on
   ├ write_scope
   ├ allowed_tools
   └ max_steps
```

### 依赖字段 `depends_on`

`depends_on` 表示显式任务依赖。例如：

```text
A depends_on=[]
B depends_on=[]
C depends_on=[A,B]
```

则：

```text
Ready Level 0 = A, B
Ready Level 1 = C
```

依赖来自 Plan，不由 Coordinator 做语义推断。

### 写入范围 `write_scope`

`write_scope` 是 Worker 执行前声明的 filesystem ownership contract，不是 mutex：

```text
write_scope
= pre-run scheduling contract
```

Worker 真正修改了哪些文件，仍由执行后的 `touched_files` 验证。

---

## 3. Ready Level 到 Batch

Coordinator 先按 dependency 得到 ready tasks，再用 `write_scope` 做同层并发分组：

```text
depends_on DAG
↓
Ready Level
↓
write_scope overlap check
↓
Conflict-free Batches
```

例如：

```text
A → src/pricing.py
B → src/shipping.py
```

可以进入同一 Batch。如果：

```text
A → src/
B → src/order.py
```

路径存在父子 overlap，则不能同批并发。当前会拆成不同 Batch，而不是让整个 Fanout 直接失败。

---

## 4. Worker 是独立 AgentLoop Job，不是永久绑定线程

```text
SubagentTask
→ 一个逻辑 Worker Job

ThreadPoolExecutor
→ 给 Worker Job 分配执行线程
```

例如 `tasks = A, B, C`、`max_workers = 2`，可能出现：

```text
Thread-1 → A
Thread-2 → B

B 完成
↓
Thread-2 → C
```

所以：

```text
Thread identity
≠ Agent identity
```

---

## 5. Worker Runtime 如何组装

每个 Worker 都创建自己的执行上下文：

```text
Task
↓
Ephemeral Worktree
↓
Worker RuntimeConfig
↓
Filtered ToolRegistry
↓
RuntimeDependencies
↓
Independent AgentLoop
```

Worker 共享 Runtime 实现，但不共享：

- `AgentRunSession`；
- `WorkingMemory`；
- Conversation；
- mutable Workspace；
- durable run-state path。

每个 Worker 会派生自己的 workspace、state / approval / ledger roots、run identity、allowed tools 和 max steps。
隔离先落在 Workspace、RuntimeConfig 和 Tool Surface 上，不依赖 Prompt 约定。

---

## 6. Worktree 生命周期

```text
Worker starts
↓
create ephemeral detached worktree
↓
seed current integration baseline
↓
AgentLoop reads / edits / validates
↓
collect candidate diff
↓
collect touched_files / trace / usage
↓
return LiveSubagentResult
↓
cleanup worktree
```

需要区分：

```text
Worker Worktree
= 源码目录

Worker Artifact Dir
= trace / usage / diff / manifest 等运行证据目录
```

Worker 修改业务代码时主要依赖 separate worktrees，而不是让多个 Worker 在一个 Working Tree 上抢全局锁。

---

## 7. Worker 返回什么

Coordinator 不接收 Worker 的内部 Agent Memory，而接收结构化 result envelope：

```text
LiveSubagentResult
├ task_id
├ status
├ final_answer
├ touched_files
├ candidate_diff_path
├ candidate_diff_sha256
├ trace_path
├ usage_path
├ batch_index
├ error
└ duration
```

其中：

```text
Candidate Diff
= integration payload

LiveSubagentResult
= orchestration contract
```

`touched_files` 用于冲突检查，`status` 用于依赖解锁，Trace / Usage 用于 Evidence，Diff hash 用于恢复和完整性验证。

---

## 8. 四层 Conflict Gate

当前系统做 Conflict Detection / Blocking，不做自动语义修复。

| Gate | 时机 | 判断依据 | 例子 | 当前处理 |
| --- | --- | --- | --- | --- |
| Static Plan Gate | Worker 前 | declared `write_scope` | 两个 ready task 都声明 `src/` | 拆成不同 Batch |
| Scope Violation Gate | Worker 后 | `touched_files ⊆ write_scope` | 只允许改 A，实际改了 B | Block，不 merge |
| Dynamic Result Gate | 同 Batch Worker 后 | actual `touched_files` | 两个 Worker 都改 `utils.py` | Block |
| Merge Applicability Gate | Integration | candidate patch 是否仍可 apply | 前序集成使 patch 不再适用 | `merge_conflict` |

```text
Conflict Detection         ✅
Automatic Conflict Repair  ❌
```

当前不会在发现冲突后自动调用 LLM 修 Patch。

---

## 9. 一个 Batch 的真实生命周期

```text
Batch N
↓
Workers concurrent
↓
collect LiveSubagentResult
↓
scope / dynamic conflict checks
↓
stable sequential merge
↓
checkpoint
↓
next Batch
```

系统不是等所有 Batch 都完成后再一次性 merge。

---

## 10. 稳定合并顺序 / Stable Merge Order

同 Batch Worker 的完成顺序可能是：

```text
D → C → B → A
```

但 Integration 不使用 Future completion order，而是按 FanoutPlan 的稳定 Task 顺序：

```text
A → B → C → D
```

这样相同 Plan 不会因为线程或模型时延不同而得到不同的 integration order。

---

## 11. cumulative diff 是什么

假设 Batch 0 中 Worker A、B 分别产生 candidate diff A、B，成功集成后：

```text
Integration Workspace
= original baseline + A + B
```

下一 Batch 使用的 cumulative diff 不是 `string(diff A) + string(diff B)`，而是：

```text
Git compare:
original baseline
vs
current Integration Workspace
↓
cumulative diff
```

因此 cumulative diff 只包含已经成功进入 Integration Workspace 的真实状态。被 Conflict Gate 阻断的 candidate 不会被自动修复后加入。

---

## 12. 下一 Batch 为什么能看到上一 Batch 修改

假设 Batch 0 为 `A || B`，Batch 1 为 C。Batch 0 集成后：

```text
Integration Workspace
= baseline + A + B
```

启动 C 时：

```text
fresh Worker worktree
↓
apply current cumulative integration diff
↓
commit worker baseline
↓
run AgentLoop C
```

C 开始时看到 `baseline + A + B`，最后产生自己相对这个新 baseline 的增量 diff。

---

## 13. Git metadata lock 的边界

Worker 写业务代码时依靠 separate worktrees，不需要共享 code-file mutex。但多个线程同时执行
`git worktree add`、`git worktree remove / prune` 会触碰同一 repository 的共享 Worktree metadata。

当前 Live Fanout 是单 Python Process 内多线程，因此这段生命周期操作使用 process-local
`threading.Lock`。如果未来跨 Process，`threading.Lock` 不足，需要 filesystem / OS lock。

```text
Code editing
→ isolation

Shared Git metadata
→ lock scoped to concurrency domain

Integration
→ deterministic sequential apply
```

---

## 14. 最终验收 / Finalizer

Finalizer 是最后 correctness gate，只能执行：

```text
read
git status / diff
validation
```

它不能 repair candidate。如果 Finalizer 自己修改 Workspace，则应被 Block。它验证已经集成的结果，
不会成为隐藏的最后修代码 Agent。

---

## 15. 当前能力与未来方向 / Current vs Future

### 当前已实现 / CURRENT

```text
Explicit typed FanoutPlan
↓
Plan validation
↓
Deterministic Coordinator
↓
Independent Worker AgentLoops
↓
Worktree isolation
↓
Conflict detection
↓
Stable integration
↓
Read-only Finalizer
```

### 未来方向 / FUTURE / NOT IMPLEMENTED

```text
User natural-language task
↓
Planner Agent
↓
typed FanoutPlan
```

Planner 未来可以提出 task decomposition、dependency、coarse write_scope、allowed tools 和 budget，
但 Runtime 仍必须做 deterministic validation。

另一个 Future extension 是 Conflict Resolver Agent，当前同样没有实现。

---

## 16. 核心源码 Owner

```text
LiveFanoutCoordinator.run
↓
build_conflict_free_batches
↓
LiveFanoutCoordinator._run_batch
↓
LocalAgentWorkerAdapter.run_worker
↓
LiveFanoutCoordinator._mark_dynamic_conflicts
↓
LiveFanoutCoordinator._merge_batch
↓
LocalAgentWorkerAdapter.run_finalizer
```

| 环节 | Owner |
| --- | --- |
| typed plan 与 task contract | [`FanoutPlan`](../agent_forge/multi_agent/domain/live.py) |
| dependency 与 conflict-free batching | [`build_conflict_free_batches`](../agent_forge/multi_agent/domain/fanout.py) |
| batch 执行、冲突检查、稳定集成 | [`LiveFanoutCoordinator`](../agent_forge/multi_agent/application/live_fanout.py) |
| Worker Runtime 与只读 Finalizer | [`LocalAgentWorkerAdapter`](../agent_forge/multi_agent/adapters/local_worker.py) |

阅读每个方法时只需要问四件事：Input 是什么、做了什么 Runtime 判断、失败分支是什么、Output 给谁。

---

## 17. 30 秒摘要

> NanoHarness 当前的 Multi-Agent 不是 Main Agent 带多个共享 Memory 的 Subagent，而是显式 FanoutPlan 驱动的并发执行器。Coordinator 本身不调用 LLM；它先按 dependency 形成 ready level，再按 declared write_scope 做 conflict-free batching。同批 Worker 用 ThreadPool 并发，每个 Worker 都有独立 AgentLoop、Git worktree、RuntimeConfig 和 Tool Surface。Worker 只返回结构化结果和 candidate diff；Coordinator 按 actual touched files 做动态冲突检查，再按稳定 Task 顺序串行 apply 到 Integration Workspace。后续 Batch 基于已经集成的 cumulative Git diff 构建新 Worker baseline。Planner 和 automatic conflict resolver 都是明确未实现的 future extension。

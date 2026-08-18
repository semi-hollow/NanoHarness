# 多 Agent 编排

> 本文描述 NanoHarness Multi-Agent 的**当前稳定基线（V0）**、正在开发的 **V1 MVP**，以及明确不在当前版本实现的后续能力。
> Roadmap 以 [`MULTI_AGENT_ROADMAP.md`](./MULTI_AGENT_ROADMAP.md) 为准。

---

# 1. 版本与分支

当前使用两条明确分支：

```text
stable/v0-20260818
    ↓
当前稳定版本，未来两周继续用于展示

feature/multi-agent-v1
    ↓
新的 Multi-Agent 能力开发分支
```

同时使用：

```text
v0-stable-20260818
```

作为 V0 精确 commit 的不可变 snapshot tag。

原则：

- V0 稳定分支不接收 V1 开发改动；
- V1 全部能力吃透、验证完成后，再决定是否将其提升为新的稳定版本；
- 未实现能力不得在文档中写成 CURRENT。

---

# 2. V0：当前稳定能力

当前 V0 的真实主链：

```text
Typed FanoutPlan
        ↓
LiveFanoutCoordinator
        ↓
Dependency / Ready Level
        ↓
Conflict-Free Batch
        ↓
ThreadPoolExecutor
        ↓
Independent Worker AgentLoops
        ↓
Isolated Git Worktrees
        ↓
LiveSubagentResult + Candidate Diff
        ↓
Conflict Gates
        ↓
Stable Integration
        ↓
Read-only Finalizer
```

当前 Coordinator：

```text
Coordinator
= deterministic orchestrator
≠ LLM Main Agent
≠ shared WorkingMemory
```

当前能力本质上是：

> **Planning 之后的 Orchestration / Execution half。**

---

# 3. V0 的 FanoutPlan 是谁生成的？

当前：

```text
Manual / External JSON or Python Mapping
        ↓
FanoutPlan.from_mapping(...)
        ↓
Runtime Validation
        ↓
Coordinator Execution
```

当前 `FanoutPlan` 已包含：

```text
goal
tasks[]
├── id
├── task
├── depends_on
├── write_scope
├── allowed_tools
├── expected_artifact
└── max_steps
```

V0 不会把自然语言任务自动拆成多个 Worker。

因此 V0 的最大结构性缺口是：

```text
Natural Language Task
        ↓
       ???
        ↓
FanoutPlan
```

V1 首要目标就是补齐这一段。

---

# 4. V0 Worker 的 Context 隔离

每个 Worker 都拥有独立：

- `AgentRunSession`
- WorkingMemory
- Conversation
- RuntimeConfig
- Tool Surface
- Git worktree
- durable state path

多个 Worker **不共享完整 Conversation 或 WorkingMemory**。

因此 V1 不会改成：

```text
Main Agent huge context
        ↓
copy everything to every Worker
```

而会继续保持：

```text
Private context
→ isolated
```

---

# 5. V0 Worker 如何交接代码状态？

Worker A/B 成功 merge 后：

```text
Integration Workspace
= original baseline + merged successful candidate changes
```

下一批 Worker C 启动时：

```text
fresh worktree
        ↓
seed current cumulative integrated diff
        ↓
commit as Worker baseline
        ↓
run Worker C
```

因此 C 能看到 A/B 已经成功进入集成空间的代码修改。

V0 的 **code-state handoff 是存在的**。

当前不足的是：

> 缺少一个明确、紧凑、可追溯的 **semantic handoff contract**。

---

# 6. V0 的冲突处理

当前系统主要做 **Conflict Detection / Blocking**，不做自动语义修复。

主要门禁：

| Gate | 时机 | 依据 | 当前作用 |
|---|---|---|---|
| Static Plan Gate | Worker 前 | declared `write_scope` | 拆分冲突 batch |
| Scope Violation Gate | Worker 后 | actual touched files vs scope | fail closed |
| Dynamic Result Gate | 同批 Worker 后 | actual `touched_files` | 阻止冲突 merge |
| Merge Applicability Gate | Integration | candidate patch 是否仍可 apply | 标记 merge conflict |

V0：

```text
Conflict Detection         ✅
Automatic Conflict Repair  ❌
Bounded Serialized Retry   ❌
Semantic Replan            ❌
```

---

# 7. V0 Finalizer 是否只是汇总？

不是。

V0 Finalizer 已经是一个真实的 **read-only verification gate**：

- 能读取当前 integrated candidate；
- 能查看 Git diff/status；
- 能运行允许的 validation；
- 最终输出 `PASS / NEEDS_REVISION / BLOCKED`；
- 不允许修改 workspace。

但 V0 的 completion semantics 仍偏软：

```text
Goal
+ Worker results
+ Integrated state
↓
Prompt-driven judgment
```

V1 会加入显式 Acceptance Criteria，使完成判断更可解释。

---

# 8. V1：目标主链

V1 不重写 V0 execution half。

V1 只补当前能力闭环最关键的缺口：

```text
Natural Language Task
        ↓
Planner
        ↓
Single / Fanout Strategy Gate
        ↓
Validated Typed FanoutPlan
        ↓
Existing DAG Scheduler
        ↓
Isolated Workers
        ↓
Structured WorkerHandoff
        ↓
Existing Deterministic Integration
        ↓
Bounded Recovery / Replan
        ↓
Criteria-aware Read-only Finalizer
        ↓
PASS / NEEDS_REVISION / BLOCKED
```

---

# 9. V1.1 Planner + Single/Multi Strategy Gate

V1 Planner 只负责：

- task decomposition；
- dependency proposal；
- coarse `write_scope` proposal；
- acceptance criteria；
- Single/Fanout strategy。

Planner 不拥有最终执行权。

原则：

```text
LLM proposes.
Runtime validates.
```

目标 contract：

```text
PlanningDecision
├── mode: single | fanout
├── reason
├── global_acceptance_criteria
└── tasks[]
```

Fanout task 只保留必要字段：

```text
id
task
depends_on
write_scope
allowed_tools
acceptance_criteria
max_steps
```

Planner 输出仍必须进入 deterministic validation。

如果任务足够局部或高度耦合：

```text
mode = single
```

避免为了 Multi-Agent 而强行 Multi-Agent。

---

# 10. V1.2 Acceptance Criteria + Structured Handoff

## 10.1 Acceptance Criteria

Acceptance Criteria 从 Planner 贯穿到 Finalizer：

```text
Planner
↓
Task Contract
↓
Worker
↓
Evidence
↓
Finalizer
```

它不是单纯 Prompt decoration。

---

## 10.2 WorkerHandoff

V1 继续保持 Context isolation。

Agent 之间的通信拆成三类：

```text
Code State
→ Integrated Workspace

Semantic State
→ Compact WorkerHandoff

Private Context
→ Isolated
```

目标 Handoff：

```text
WorkerHandoff
├── task_id
├── status
├── summary
├── touched_files
├── validation_evidence
├── unresolved_issues
└── artifact_path
```

Handoff 优先从已有 `LiveSubagentResult` / Artifact 做 deterministic projection。

不要为了 Handoff 再调用一个 LLM。

如果 C `depends_on=[A,B]`：

C 只收到：

- 自己的 task；
- 自己的 acceptance criteria；
- A/B compact handoff；
- 当前 integrated workspace。

不收到 A/B 完整 Conversation。

---

# 11. V1.3 Bounded Recovery / Replan

硬边界：

```text
max_worker_retry <= 1
max_plan_replan_rounds <= 1
```

---

## 11.1 Merge Applicability Conflict → Serialized Rerun

目标场景：

```text
A || B
↓
A merge success
↓
B old candidate becomes stale / non-applicable
```

V1 recovery：

```text
discard B old patch
        ↓
latest integrated workspace
        ↓
fresh B Worker
        ↓
re-read / re-edit / re-validate
        ↓
B-v2 candidate
```

核心设计：

> optimistic parallel execution + serialized conflict recovery

不做 LLM textual patch merge。

---

## 11.2 Retryable Worker Failure

只对可合理判断为 transient/retryable 的失败最多 retry 一次。

不盲目 retry：

- scope violation；
- deterministic permission violation；
- invalid plan。

---

## 11.3 One-Round Remaining-Plan Replan

已经成功完成并 merge 的任务冻结：

```text
Completed Prefix
= immutable history
```

只允许替换剩余任务图：

```text
Remaining Graph
= replaceable once
```

Replanner 输入：

- original goal；
- current plan；
- completed task IDs；
- completed handoffs；
- current integrated-state summary；
- failure/conflict evidence；
- remaining work。

Replanner 输出仍必须通过 deterministic validation。

最多一次。

---

## 11.4 Fail-Closed Boundaries

V1 明确不做：

- scope violation 自动重试；
- LLM semantic merge；
- 无限 replan；
- completed-task rollback；
- Agent ping-pong。

---

# 12. V1.4 Criteria-Aware Finalizer

V1 不重写 Finalizer。

继续使用 read-only Finalizer AgentLoop。

输入升级为：

```text
Original Goal
+
Global Acceptance Criteria
+
Per-task Acceptance Criteria
+
WorkerHandoffs
+
Integrated Workspace / Diff
+
Validation Evidence
```

目标判断：

```text
criterion 1 → PASS / FAIL / UNKNOWN
criterion 2 → PASS / FAIL / UNKNOWN
...
↓
Final Decision
```

最终仍是：

```text
PASS
NEEDS_REVISION
BLOCKED
```

Runtime hard facts 优先于模型文字。

---

# 13. V1.5 五个机制 Case

V1 不跑大规模 Multi-Agent Benchmark。

只要求五个 deterministic mechanism cases：

1. **Single Gate**
   - 局部任务应选择 `single`。

2. **Independent Parallel Fanout**
   - 独立任务可并发、隔离执行、稳定 merge。

3. **Dependency + Handoff**
   - 下游 Worker 得到 dependency handoff + integrated code state。

4. **Merge Conflict Serialized Recovery**
   - stale candidate 丢弃，在最新集成状态重新执行一次。

5. **Worker Failure / Bad Remaining Plan**
   - 最多一次 retry/replan，然后成功或 controlled abort。

这些 Case 的目标是证明：

> orchestration mechanism correctness

不是证明：

> Multi-Agent Pass@1 一定高于 Single-Agent。

---

# 14. V2：Multi-Agent Quantitative Evaluation

V2 当前不实现。

未来固定 Golden-10 比较：

```text
Single Agent
vs
Static Fanout
vs
Adaptive Fanout
```

关注：

- success rate；
- wall time；
- token；
- LLM calls；
- conflict rate；
- recovery rate。

在 V2 完成以前，不声称 Multi-Agent 已量化提高 Pass@1。

NanoHarness 已有 Single-Agent benchmark/eval 能力，因此 V1 当前优先把 Multi-Agent mechanism 做完整。

---

# 15. V3：Evaluation-Driven Multi-Agent Optimization

V3 当前不实现。

它仍放在 Multi-Agent Roadmap 中，因为第一阶段只优化：

- Planner；
- Handoff；
- Recovery/Replan；
- Finalizer；
- Multi-Agent orchestration policy。

链路：

```text
Multi-Agent Runs
↓
FailureReport
↓
ImprovementProposal
↓
ExperimentSpec
```

边界：

```text
Agent proposes
Engineer changes source
Eval judges
```

不做 Agent 自动修改 NanoHarness。

如果未来优化范围扩展到整个 Harness，则拆出独立 Harness Improvement Roadmap。

---

# 16. 当前明确不做

当前不引入：

- Auto Research 第二主线；
- Agent 群聊；
- unrestricted A2A；
- voting / consensus；
- recursive supervisors；
- unlimited agent spawning；
- semantic merge Agent；
- Redis / MQ / Kubernetes worker platform；
- cloud multi-tenancy；
- organization memory；
- knowledge graph；
- Skill marketplace；
- automatic Harness self-modification。

---

# 17. 核心源码 Owner

## V0 已有

```text
FanoutPlan
→ agent_forge/multi_agent/domain/live.py

Dependency / conflict-free batching
→ agent_forge/multi_agent/domain/fanout.py

Live orchestration / integration
→ agent_forge/multi_agent/application/live_fanout.py

Worker runtime / worktree / finalizer
→ agent_forge/multi_agent/adapters/local_worker.py

Composition root
→ agent_forge/multi_agent/wiring.py
```

## V1 新增/增强 Owner

最终实现后，应保持 Owner 数量尽量少：

```text
Planner / PlanningDecision
→ one clear planning owner

Acceptance Criteria
→ existing plan/task contract where possible

WorkerHandoff
→ one compact contract / projection owner

Recovery / Replan
→ LiveFanoutCoordinator or one thin helper

Criteria-aware Finalizer
→ existing LocalAgentWorkerAdapter finalizer path
```

不要为了 V1 引入复杂多层抽象。

---

# 18. 30 秒架构摘要

V0：

> NanoHarness 当前 Multi-Agent 已经不是多个 Agent 共用一个 Working Tree 的玩具实现。它由 typed FanoutPlan 驱动，Coordinator 按 dependency 和 write scope 做 deterministic scheduling，每个 Worker 使用独立 AgentLoop 和 Git worktree，执行后通过 actual touched files 和 patch applicability 做冲突检查，再稳定顺序集成，最后由只读 Finalizer 验收。但 V0 的 Plan 仍由外部提供，semantic handoff、bounded replan 和显式 completion criteria 还不完整。

V1 目标：

> V1 在不重写现有 execution half 的前提下补 Planner、Single/Multi strategy、structured handoff、acceptance criteria、bounded conflict recovery 和 criteria-aware Finalizer，使系统从人工 DAG executor 升级为一个小而完整的 adaptive Multi-Agent coding harness。

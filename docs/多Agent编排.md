# 多 Agent 编排

本文聚焦：

```text
Planner / Worker / Coordinator 分别拥有什么 authority？
HARD / LIVE 怎样影响 readiness？
并发执行怎样保持 deterministic trusted integration？
```

> Multi-Agent 的核心不是“多开几个 Agent”，而是把 **Planning、并发执行、Candidate 生产、可信集成** 分给不同 owner。Worker 可以并发，但只有 Coordinator 能改变 trusted integration state。

# 1. 总体流程

```text
Task
  ↓
Planner Proposal
  ↓
Runtime Validation
  ↓
Frozen FanoutPlan
  ↓
Readiness Scheduler
  ↓
Isolated Workers
  ↓
Candidates
  ↓
Integration Gate
  ↓
Trusted Workspace
  ↓
Finalizer
```

一句话：

```text
Planner proposes
Worker executes
Coordinator integrates
Finalizer verifies
```

# 2. Planning

`AdaptivePlanner.decide()`：

```text
task
+ bounded repository context
+ capability constraints
        ↓
Single / Multi proposal
```

Runtime 随后校验：

```text
task identity
dependency graph / acyclic
write_scope
allowed tools
task limits
HARD / LIVE contract
```

通过后形成 immutable `FanoutPlan`。

Planner 不拥有 scheduler state。

# 3. Worker Isolation

每个 `SubagentTask`：

```text
trusted upstream base
+ bounded handoff
        ↓
isolated Git worktree
        ↓
canonical AgentLoop
        ↓
WorkerAttemptResult
```

Worker 只产：

```text
candidate diff
touched files
validation evidence
typed attempt outcome
```

Worker 没有 merge 权。

所以：

```text
candidate_produced
!=
integrated
```

# 4. Readiness Scheduler

`FanoutCoordinator._execute_plan()` 负责：

```text
哪些 task 已 ready？
哪些 write scope 可以同时运行？
哪些 attempt 现在 launch？
哪些 candidate 可以进入 integration gate？
```

Worker 完成顺序只决定 candidate arrival，不直接决定 trusted state。

# 5. HARD Dependency

```text
A --HARD--> B
```

B readiness：

```text
A Worker finished      ✗
A candidate produced   ✗
A integrated           ✓
```

所以 B 的代码基线来自 trusted integration state。

# 6. LIVE Coordination

LIVE 用于 consumer 只依赖一段明确语义证据、可以提前工作：

```text
Producer
→ READY v1
→ Consumer starts

Producer
→ UPDATE v2
```

`LiveHandoffRuntime` 负责：

```text
producer attempt identity
semantic key
monotonic version
mailbox
consumption record
freshness
```

LIVE 只修改：

```text
start barrier
```

最终 integration 仍要求：

```text
producer trusted integrated
AND
consumer consumed exact latest version
```

因此：

> **LIVE 只放松 start barrier，不放松 final trust barrier。**

# 7. Candidate Integration

`FanoutCoordinator._integrate_candidate()` 是唯一 trust owner：

```text
candidate artifact / hash
→ touched files within write_scope
→ patch contract
→ strict frontier
→ HARD trust
→ LIVE freshness
→ patch dry-check
→ apply
→ mark integrated
```

只有成功 apply 后，logical Task 才成为 integrated。

Worker outcome：

```text
candidate_produced
retryable_failure
terminal_failure
```

Task outcome：

```text
integrated
failed
blocked
not_integrated
```

两者不能混在一起。

# 8. Strict Integration Frontier

Runtime 维护：

```text
integration_order
```

并保持：

```text
merged_task_ids == integration_order[:k]
```

所以：

```text
B 比 A 先完成
→ B candidate 可以等待
→ 不能越过 A 形成另一条 trusted state
```

这把：

```text
execution completion order
```

和：

```text
trusted integration order
```

彻底分开。

# 9. Retry

Retry 只消费 typed Worker outcome：

```text
attempt == 1
AND
status == retryable_failure
AND
retryable == true
```

Candidate governance rejection：

```text
scope violation
artifact mismatch
merge conflict
stale LIVE dependency
```

不会反向变成 Worker retry。

# 10. Resume

HARD-only Resume：

```text
same frozen plan
+ same Git base
+ verified merged_task_ids strict prefix
+ persisted candidate evidence
        ↓
rebuild trusted prefix
→ continue remaining tasks
```

LIVE mailbox 当前没有 durable replay contract，因此不进入该 Resume path。

# 11. Finalizer

所有 Task trusted integrated：

```text
read-only Finalizer
→ inspect final workspace
→ acceptance criteria
→ PASS / FAIL / BLOCKED
```

Finalizer 没有 merge 或 fix authority。

# 12. Ownership

```text
AdaptivePlanner
= proposal

FanoutPlan
= frozen execution contract

Worker
= private execution attempt

FanoutCoordinator
= readiness + trusted integration

LiveHandoffRuntime
= LIVE version / consumption state

Finalizer
= final read-only verdict
```

# 13. 核心不变量

```text
1. LLM proposes; Runtime governs
2. Worker reuses canonical AgentLoop
3. Worker candidate != trusted state
4. HARD waits for integrated upstream
5. LIVE only relaxes start barrier
6. Coordinator exclusively owns integration
7. merged_task_ids is a strict trusted prefix
8. Retry consumes typed attempt outcome
9. Finalizer verifies only
```

# 14. 附录：A/B 并发 + LIVE

```text
integration_order = [A, B]

A
→ implementation

B
→ tests / docs
```

普通并发：

```text
Worker B finishes first
→ candidate B = DEFERRED

Worker A finishes
→ integrate A

then
→ integrate B
```

LIVE：

```text
A READY v1
→ B starts early

A UPDATE v2
→ final producer version = v2

B consumed only v1
→ B integration rejected as stale
```

所以并发只影响 execution timing，不改变 final trust contract。

## 源码入口

- `agent_forge/multi_agent/application/planning.py::AdaptivePlanner.decide()`
- `agent_forge/multi_agent/domain/fanout.py::FanoutPlan`
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator.run()`
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._execute_plan()`
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._integrate_candidate()`
- `agent_forge/multi_agent/adapters/local_worker.py::LocalAgentWorkerAdapter.run_worker()`
- `agent_forge/multi_agent/application/live_handoff.py::LiveHandoffRuntime`
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._restore_hard_prefix()`

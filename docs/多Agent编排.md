# 多 Agent 编排

> LLM 只提议任务拆分；Runtime 校验并冻结 Plan；隔离 Worker 只产生 candidate；Coordinator 独占可信集成；Finalizer 只验证最终结果。

# 1. 适用任务与样例

假设 repository task 是：

```text
修改 auth module，并补独立的 tests/docs module
```

Planner 可以提议：

```text
A: 修改 auth module
   write_scope = src/auth/

B: 补 tests/docs
   write_scope = tests/auth/, docs/auth.md
```

A/B 写域独立时可以并发。高度耦合的任务由 `AdaptivePlanner` 选择 Single，继续使用 canonical `AgentLoop`。Multi 的收益来自可隔离工作面的并行执行，而不是增加另一套 Agent reasoning runtime。

# 2. 从 Planner Proposal 到可信结果

```text
Ultra
→ AdaptivePlanner.decide()
→ Single | Multi proposal
→ schema + domain + graph validation
→ deeply frozen FanoutPlan
→ FanoutCoordinator.run()
→ _execute_plan() readiness scheduler
→ isolated LocalAgentWorkerAdapter.run_worker()
→ WorkerAttemptResult
→ candidate pending integration
→ FanoutCoordinator._integrate_candidate()
→ strict trusted integration prefix
→ read-only Finalizer
```

`AdaptivePlanner.decide()` 给模型 bounded repository map、可用 tools 和任务上限。Proposal 必须满足 task identity、acyclic dependencies、write scopes 和 tool constraints；结构或领域错误最多进入一次 repair。合法单任务 proposal 规范化为 Single。

通过验证后，`FanoutPlan` 深度不可变。执行期不追加 Task、不改写 DAG，也不把模型文本直接变成 scheduler state。

# 3. Worker Isolation 与 Candidate

`LocalAgentWorkerAdapter.run_worker()` 为每次 Attempt 创建隔离 Git worktree，并装配受 task tool set、write scope 和 step budget 约束的 canonical `AgentLoop`：

```text
SubagentTask + trusted upstream diff + bounded handoff
→ isolated worktree
→ canonical AgentLoop
→ candidate diff + artifact + validation evidence
→ WorkerAttemptResult
```

Worker 无 merge 权。worktree 隔离保证并发 Worker 不共享可变 workspace；`candidate_produced` 只表示一个待治理产物已经形成。

`state.candidates` 仅保存“已产生、仍等待 integration decision/frontier”的 candidate：

```text
DEFERRED   keep pending
INTEGRATED remove immediately
REJECTED   remove immediately
```

Attempt artifact 和 logical Task result 提供终态审计，因此 terminal candidate 不需要作为第二份 lifecycle state 残留。

# 4. Readiness 与并发

`FanoutCoordinator._execute_plan()` 按 readiness 与 `max_workers` 持续提交 Attempt。无依赖的 A/B 可以同时运行；完成顺序只影响 candidate 到达时间。

HARD dependency 表达代码基线依赖：

```text
A --HARD--> B
```

B 只有在 A 出现在 `merged_task_ids` 中时 ready。A Worker finished 或 candidate produced 都不足以启动 B，因为 A 仍可能在 Coordinator gate 被拒绝。B 启动时得到已集成 upstream diff 和有界 `WorkerHandoff`，不继承 A 的 private Conversation 或临时 worktree。

# 5. Coordinator 集成门（Integration Gate）

`FanoutCoordinator._integrate_candidate()` 是唯一 integration authority：

```text
candidate artifact and hash
→ actual touched files within write_scope
→ patch contract / no-patch rule
→ strict frontier position
→ HARD trusted dependencies
→ LIVE producer trust and freshness
→ patch dry-check
→ apply to integration workspace
→ trusted commit + FanoutTaskResult(status=integrated)
```

Worker execution outcome 与 logical Task result 是两个不同阶段：

```text
WorkerAttemptResult(status=candidate_produced)
→ Coordinator gate
→ FanoutTaskResult(integrated | failed | blocked | not_integrated)
```

Scope violation、artifact mismatch、no patch、merge conflict 和 stale LIVE dependency 属于 candidate governance rejection；它们不会重新启动 Worker Attempt。

# 6. 严格集成前沿（Strict Integration Frontier）

Runtime 计算稳定 `integration_order`，并始终保持：

```text
merged_task_ids == integration_order[:k]
```

若 B 比 A 先完成，B candidate 保持 `DEFERRED`；A 集成后再处理 B。Frontier task 终止失败时，后续结果保持可审计，但不能跳过失败 task 形成另一条 trusted state。该 contract 用确定性的连续前缀换取简单恢复、单一信任边界和稳定结果顺序。

# 7. Typed Worker Outcome 与 Retry

一次 Attempt 只产生三种 typed outcome：

```text
candidate_produced
retryable_failure
terminal_failure
```

`LocalAgentWorkerAdapter` 从 canonical Run 的 `TaskCheckpoint.status` 和 typed recovery decision 投影结果。`final_answer` 只保存 payload；`failure_kind` 只用于 diagnosis。Domain 强制只有 `retryable_failure` 才能令 `retryable=true`。

Coordinator 的 retry 条件只有：

```text
attempt == 1
AND status == retryable_failure
AND retryable == true
```

Provider timeout 或 transient connection failure 可在第一次 Attempt 后 retry；waiting-human、blocked、policy stop 与其他 non-retryable lifecycle state 进入 `terminal_failure`。

# 8. LIVE 协作（Coordination）

LIVE 用于 consumer 只需一段明确语义证据就能提前启动的依赖：

```text
A publishes READY v1: auth interface fixed
B consumes v1 and starts docs
A publishes UPDATE v2: interface detail changed
```

`LiveHandoffRuntime` 独占 attempt identity、mailbox、monotonic version、consumption record 与 final freshness。事件在 Model Step safe boundary 进入 Worker。

READY 只放松 start barrier。最终 integration 必须同时满足：

```text
producer is trusted integrated
AND consumer consumed producer exact latest version
```

若 B 只消费 v1 而 producer final version 是 v2，`authorize_integration()` 拒绝 candidate。

# 9. Resume 与 Finalizer

HARD-only Resume 恢复 trusted integration prefix：

```text
same frozen plan digest
+ same Git base
+ merged_task_ids is strict prefix
+ persisted candidate evidence can be replayed
→ rebuild trusted prefix
→ continue remaining plan
```

单独的 Worker artifact 或 candidate 不能提升为 trusted Task。LIVE mailbox 当前没有 durable replay contract，因此不进入 Resume path。

所有 Task 可信集成后，Finalizer 使用只读 Tool surface 验证最终 workspace 和 acceptance criteria。它输出 PASS / FAIL / BLOCKED，但没有 merge 或 fix 权。

# 10. 核心不变量

1. LLM proposes, Runtime governs。
2. Worker 产生 candidate，Coordinator 拥有 integration authority。
3. `candidate_produced != integrated`。
4. HARD readiness 等 trusted integrated state。
5. `merged_task_ids` 是 strict trusted prefix。
6. LIVE 只放松 start barrier，不放松 final trust barrier。
7. Retry 只消费 typed Worker outcome。
8. Finalizer verifies only。

# 11. 源码入口（Source Anchors）

- `agent_forge/multi_agent/application/planning.py::AdaptivePlanner.decide()`：Single/Multi proposal 与 Runtime validation。
- `agent_forge/multi_agent/domain/fanout.py::FanoutPlan`：冻结 Plan 与 graph invariants。
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator.run()`：完整 Fanout lifecycle。
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._execute_plan()`：readiness scheduler 与 Attempt collection。
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._integrate_candidate()`：唯一 candidate integration gate。
- `agent_forge/multi_agent/adapters/local_worker.py::LocalAgentWorkerAdapter.run_worker()`：worktree isolation、AgentLoop 与 Worker result。
- `agent_forge/multi_agent/application/live_handoff.py::LiveHandoffRuntime.authorize_integration()`：LIVE final freshness。
- `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator._restore_hard_prefix()`：HARD-only Resume 验证。

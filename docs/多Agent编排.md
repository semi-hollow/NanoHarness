# 多 Agent 编排

> 核心结论：LLM 只提议如何拆分；Runtime 冻结并治理 Plan；隔离 Worker 只产生 candidate；只有 Coordinator 能把 candidate 变成可信集成结果。

## 1. 为什么需要 Multi-Agent

Multi-Agent 只适合存在可隔离工作面的任务。假设目标是：

```text
同时修改 auth module，并补独立的 tests/docs module
```

Planner 可以提议：

```text
A: 修改 auth module
   write_scope = src/auth/

B: 补独立 tests/docs
   write_scope = tests/auth/, docs/auth.md
```

A/B 写域独立时可以并发。若工作高度耦合，`AdaptivePlanner` 应选择 Single，继续使用 canonical `AgentLoop`。Multi 不是默认更强，而是用隔离与并发换取特定任务的吞吐。

## 2. 从提议到可信结果

完整主链是：

```text
Ultra
-> AdaptivePlanner.decide()
-> Single | Multi proposal
-> parser + domain validation
-> deeply frozen FanoutPlan
-> FanoutCoordinator.run()
-> readiness scheduler
-> isolated LocalAgentWorkerAdapter
-> WorkerAttemptResult(candidate)
-> FanoutCoordinator._integrate_candidate()
-> strict trusted integration prefix
-> read-only Finalizer
```

### 模型提议，运行时治理

`AdaptivePlanner.decide()` 给模型 bounded repository map、可用工具与任务上限。模型最多获得一次结构/领域 repair；输出必须经过 Schema、Task identity、依赖无环、write scope 与工具约束校验。单任务 Multi proposal 会规范化为 Single。

通过校验后形成深度不可变的 `FanoutPlan`。执行期不改写 DAG、不动态追加 Task，也不把模型输出直接当成调度命令。这是“模型提议、Runtime 治理”的实际边界。

### Worker 产生候选，Coordinator 负责集成

`LocalAgentWorkerAdapter.run_worker()` 为每次 Attempt 创建隔离 Git worktree，按 Task 收窄工具和 write scope，并在其中复用同一套 canonical `AgentLoop`。Worker 的输出是：

```text
WorkerAttemptResult
candidate diff
artifact / validation evidence
actual touched files
```

Worker 无权 merge，也无权把自己标记为 integrated。隔离 worktree 防止并发 Worker 直接共享可变 workspace；即使 Worker 成功完成，主 workspace 仍未发生可信变化。

`FanoutCoordinator._integrate_candidate()` 是唯一集成 authority。它依次检查 candidate artifact、实际写域、patch contract、当前 integration frontier、dependency trust、LIVE freshness、patch dry-check，最后才 apply 并建立 trusted commit。

因此：

```text
candidate_produced != Task integrated
```

## 3. Readiness 与并发

`FanoutCoordinator._execute_plan()` 按 readiness 和 `max_workers` 提交 Worker Attempt，而不是按固定 wave 等待整批结束。

对上面的 A/B，若没有依赖，两者都可以立即进入线程池。谁先完成只影响 candidate 到达时间，不改变可信集成顺序。

### HARD dependency 等可信集成

若 B 必须基于 A 的代码，则 Plan 表达：

```text
A --HARD--> B
```

B 的 ready 条件不是“A Worker finished”，而是“A 已出现在 `merged_task_ids` 的 trusted prefix 中”。原因是 A 可能已经产生 candidate，但随后在 scope、merge、policy 或 validation gate 失败；让 B 基于这种未可信结果启动会制造第二套真相。

B 启动时只获得已集成 upstream diff 和有界 `WorkerHandoff`，不会继承 A 的 private Conversation、模型上下文或临时 worktree。

## 4. 严格集成前沿

Runtime 为 Plan 计算稳定 `integration_order`，并始终保持：

```text
merged_task_ids == integration_order[:k]
```

Worker execution 可以并发，trusted integration 必须形成连续前缀。即使 B 比 A 先完成，B candidate 也不能绕过 A 进入主 workspace。当前 frontier Task 终止失败后，后续 candidate 可以保留审计证据，但只能成为 `not_integrated`。

这是有意的 POC trade-off：牺牲 out-of-order salvage，换取单一 trusted frontier、确定性顺序、简单恢复和可审计性。当前不扩张为 partial merge-state recovery。

## 5. Retry 只有一个分类 owner

一次 Attempt 的 typed outcome 是：

```text
candidate_produced
retryable_failure
terminal_failure
```

Producer 在形成 `WorkerAttemptResult` 时一次性决定 `status` 与 `retryable`。Domain 强制二者一致：只有 `retryable_failure` 才能令 `retryable=true`。

Coordinator 只消费：

```text
attempt == 1
AND status == retryable_failure
AND retryable == true
```

它不解析 `failure_kind` 字符串。Timeout 与 transient connection failure 可以 retry；scope violation、merge conflict、policy denial 等确定性拒绝由 producer 生成 terminal outcome，不 retry。`failure_kind` 仅用于诊断。

## 6. LIVE 只放松启动屏障

HARD 要等 upstream 集成后才启动 downstream；LIVE 用于 downstream 只需要一小段语义证据就能提前工作的情况。例如：

```text
A 发布 READY v1：auth 接口已确定
B 消费 v1，提前开始写 docs
A 后来发布 UPDATE v2：接口细节修正
```

`LiveHandoffRuntime` 是 LIVE consistency owner，管理 attempt identity、mailbox、单调 version、消费记录和 final freshness。事件只在 Model Step 安全边界进入 Worker。

READY 只表示 B 可以提前启动，不表示 B 可以 merge。最终授权必须同时满足：

```text
A 已可信 integrated
AND B 消费了 A 的 exact latest version
```

若 B 只消费 v1 而最终版本是 v2，`authorize_integration()` fail closed。LIVE 放松 start barrier，从不放松 final trust barrier；COMMON scheduler 不拥有 mailbox 内部状态。

## 7. Resume 与 Finalizer

HARD-only resume 恢复的是经过验证的 trusted prefix：

```text
verify same frozen plan digest
AND same Git base
AND merged_task_ids is strict prefix
AND candidate evidence and replay are valid
-> replay trusted prefix
-> continue remaining plan
```

单独存在 Worker Attempt 或 candidate artifact 不能被提升为 trusted Task。LIVE 当前不支持 resume，因为 mailbox 与 consumed-version lifecycle 没有 durable replay contract；这是明确的 POC 边界。

所有 Task 都进入 trusted prefix 后，Finalizer 对最终 workspace 和全局 acceptance criteria 做只读语义验证。它可以给出 PASS/FAIL/BLOCKED，但不拥有 merge 或 fix 权，因此不会成为第二个 Coordinator。

## 8. 设计不变量

1. LLM proposes, Runtime governs。
2. Worker 只产生 candidate，Coordinator 拥有 integration authority。
3. Worker finished / candidate produced 不等于 integrated。
4. HARD readiness 只依赖 trusted integrated state。
5. Worker 使用隔离 worktree，并复用 canonical `AgentLoop`。
6. `merged_task_ids` 是唯一 strict trusted integration prefix。
7. LIVE 只放松 start barrier，不放松 final trust barrier。
8. Retry classification 只在 typed Attempt outcome 生成阶段完成。
9. Resume 只恢复经过验证的 HARD trusted prefix。
10. Finalizer 只验证，不 merge、不修复。

## 核心源码入口

1. `agent_forge/multi_agent/application/planning.py::AdaptivePlanner.decide()`：Single/Multi proposal 与 Runtime validation。
2. `agent_forge/multi_agent/domain/fanout.py::FanoutPlan`：冻结 Plan 与 Domain invariants。
3. `agent_forge/multi_agent/application/fanout.py::FanoutCoordinator.run()`：完整 Fanout 生命周期。
4. 同文件 `_execute_plan()` / `_integrate_candidate()`：readiness scheduler 与唯一 integration gate。
5. `agent_forge/multi_agent/adapters/local_worker.py::LocalAgentWorkerAdapter.run_worker()`：隔离 worktree、canonical AgentLoop 与 candidate。
6. `agent_forge/multi_agent/application/live_handoff.py::LiveHandoffRuntime.authorize_integration()`：LIVE final freshness。

HARD resume 的严格检查在 `FanoutCoordinator._restore_hard_prefix()`；artifact 文件布局、trace 字段和内部 lock map 属于实现与审计细节，不是理解主控制流的前置知识。

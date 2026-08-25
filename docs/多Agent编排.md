# 多 Agent 编排

Multi-Agent Runtime 解决的是：多个隔离 Worker 可以并发产生候选结果，但最终只有一处
能够决定哪些候选进入可信工作区。

- 输入：自然语言目标、受校验且深度不可变的 `FanoutPlan`、Runtime 配置；
- 输出：`FanoutSummary`、严格连续的 `merged_task_ids`、Worker Attempt 证据与最终 Diff；
- 唯一执行权：`FanoutCoordinator`；
- Worker 只产生 candidate，不能自行声明 Task 已可信集成。

## 主链

```text
AdaptivePlanner.decide()
        │
        ├── Single → canonical AgentLoop
        │
        └── Multi
              ↓
       deeply frozen FanoutPlan
              ↓
         build_fanout()
              ↓
       FanoutCoordinator.run()
              ↓
          _execute_plan()
              ↓
    readiness-driven Scheduler
       ┌──────┴──────┐
       │             │
      HARD          LIVE
       └──────┬──────┘
              ↓
       isolated Worker Attempt
              ↓
          candidate
              ↓
     Strict Integration Frontier
              ↓
       trusted integrated state
              ↓
        read-only Finalizer
```

Planning phase 只发生在执行前：一次 structured planning request 最多允许一次 bounded
schema/domain repair。`FanoutPlan.tasks`、Task 内的依赖、写域、工具和验收条件都在 Domain
边界转成 tuple；Run 中 `plan_digest` 不变。Runtime 不允许在执行中改写 DAG 或重新规划。

## 公共机制、严格依赖与实时协作（COMMON、HARD、LIVE）

### COMMON：公共执行机制

COMMON 是所有 Task 共用的执行机制：

- readiness scan 与 `max_workers`；
- 隔离 worktree 和 canonical `AgentLoop`；
- 最多两个真实 Worker Attempts；
- candidate artifact integrity、实际写域、patch contract；
- deterministic patch dry-check / apply；
- checkpoint、summary、trace 与 read-only Finalizer。

### HARD：可信集成后就绪

`A ──HARD──→ B` 表示 B 依赖 A 的可信代码或结构化结果。A 的 Worker 完成并不够；
只有 A candidate 通过全部门禁并追加到严格连续的 `merged_task_ids` 后，B 才能启动。

B 只接收直接 predecessor 的 `WorkerHandoff` 语义载荷，不接收 A 的 private Conversation、
模型上下文、worktree 或未合入 Diff。Handoff 本身没有生命周期 status；是否可交付只由
Coordinator 检查 upstream `FanoutTaskResult.status == integrated`。

### LIVE：语义证据提前就绪

`A ──LIVE──→ B` 允许 B 在 A 完成前启动。`READY` 或 `UPDATE` 只放松 start barrier。

Worker 通过 `LiveHandoffRuntime` 交换有界语义证据：

```text
READY → FEEDBACK → UPDATE
```

事件绑定：

- frozen `plan_digest`；
- `worker_attempt_id`；
- producer / target / semantic key；
- 单调 version 与可选 cause event。

事件只在 Model Step 安全边界进入 Worker。如果模型调用中途出现新 coordination，旧响应
返回后会被标为 stale，并以 `REFRESH_INPUT` 进入下一 Model Step。该动作不是 Task retry。

最终集成时，Producer 必须先可信集成并冻结 final latest；Consumer 必须实际消费 exact
latest version。否则 `stale_live_dependency` fail closed。LIVE 从不放松 trust barrier。

`LiveHandoffRuntime` 只管理 LIVE edge 两端的 Task；普通 Task 不进入它的 Attempt、mailbox
或 freshness 生命周期。

## Attempt 与 Task Result

`WorkerAttemptResult` 表示一次真实 Worker execution：

```text
candidate_produced | retryable_failure | terminal_failure
```

`FanoutTaskResult` 表示逻辑 Task 的治理结论：

```text
integrated | failed | blocked | not_integrated
```

核心区别：`candidate_produced` 不等于 `integrated`。

从未启动的 HARD blocked Task 只有：

```text
status=blocked
failure_kind=blocked_dependency
final_attempt=null
```

它不会进入 `attempt_results`。`task_count` 来自 Plan；`attempt_count` 来自真实
`WorkerAttemptResult`。

## Scheduler、Retry 与 Launch Wave

Launch Wave 是一次 Scheduler scan 实际 submit 的 Worker Attempts，只用于 Evidence、
Trace 和 Workbench 分组。它不是依赖单位、完成单位或调度屏障，不同 Wave 可以重叠。

Attempt-1 只有被 Runtime 确定性分类为 retryable Worker execution failure 时，Task 才返回
Scheduler；Attempt-2 在后续 Wave 异步提交。不存在 Attempt-3。

以下结果永不 retry：scope violation、no patch、merge conflict、LIVE stale、policy / approval /
permission / guardrail denial、blocked dependency 等确定性拒绝结果。

Trace 使用：

```json
{
  "event_type": "fanout_wave_launched",
  "launch_wave_index": 2,
  "attempts": [{"task_id": "A", "attempt": 2}]
}
```

单个 Attempt 的结束由 `worker_attempt_finished` 表达。

## Candidate Gate 与 Strict Integration Frontier

`_integrate_candidate()` 是唯一 candidate authority，分两阶段：

1. 候选本地校验（Candidate-local validation）：artifact integrity → actual touched scope → patch contract；
2. 可信集成授权（Trusted authorization）：frontier → HARD readiness → LIVE producer integrated → freshness →
   patch dry-check → apply → trusted commit。

非当前 frontier 的 candidate 仍立即做 local validation；因此越界和 write-task empty patch
不会被延迟或掩盖。

`merged_task_ids` 必须始终满足：

```text
merged_task_ids == integration_order[:k]
```

它是 trusted integrated identity 的唯一 authority；HARD/LIVE readiness 临时从该前缀
派生集合，不再维护第二份成功状态。

如果当前 frontier Task 已终止且没有合法 retry，Scheduler 停止提交新 Attempt；已运行
Attempt 可以结束并保留证据，但后续 candidate 只能成为：

```text
status=not_integrated
failure_kind=integration_frontier_blocked
```

它们不能越过 frontier，也不能被误判为 LIVE stale。

## Checkpoint、Summary 与 Resume

`fanout_checkpoint.json` 是唯一恢复 authority。当前 clean-break schema v4 保存：

- `schema_version`、`plan_digest`、`base_head`、`status`；
- `merged_task_ids`、`task_results`、`attempt_results`、`launch_waves`；
- `updated_at`。

`fanout_summary.json` 只用于 observation、reporting 和 Workbench。

HARD-only Resume 会验证当前 schema、相同 Plan digest、相同 Git base、严格 merged prefix、
canonical integrated Task result、candidate SHA、`validate_recovery_diffs()` 和 replay
applicability。入口只接受 `status=running` 的 Checkpoint；任意 terminal status 都在启动
Worker 前 fail closed。单独的 Attempt evidence 不能提升为 trusted Task。

LIVE V1 不支持 Resume，因为当前没有 durable mailbox / consumed-version replay。

三条执行语义彼此独立：

- **Worker Retry**：同一 Fanout Run 内，Runtime 明确分类为 retryable 的 Worker failure 最多进入 Attempt-2；
- **Resume**：外部中断后，继续同一个 HARD-only Fanout execution 的 verified trusted prefix；
- **New Run**：terminal result 后由用户显式发起的新执行，不能伪装成 Resume。

## 最小验证

当前原生机制验证：

```bash
.venv/bin/python scripts/run_multi_agent_v1_smoke.py
```

版本化证据：

```text
benchmarks/experiments/multi-agent-v1/mechanism-evidence.json
```

核心测试：

```bash
.venv/bin/python -m pytest -q tests/test_multi_agent_clean_break.py
```

## 代码阅读顺序

1. `domain/fanout.py` → `FanoutPlan`、`WorkerAttemptResult`、`FanoutTaskResult`：COMMON Domain；
2. `application/fanout.py` → `run()`：完整 Fanout 生命周期骨架；
3. 同文件 → `_execute_plan()`：统一 readiness scheduler；
4. 同文件 → `_run_worker_attempt()`：唯一真实 Attempt owner；
5. 同文件 → `_integrate_candidate()`：唯一 candidate authority；
6. `application/live_handoff.py` → `LiveHandoffRuntime`：LIVE mailbox 与 freshness；
7. `adapters/local_worker.py` → `run_worker()`：隔离 AgentLoop 和 candidate artifact；
8. `adapters/fanout_files.py`：checkpoint / summary / coordination 持久化；
9. `wiring.py` → `build_fanout()`：composition root。

文件边界：`ports/fanout.py` 只放 COMMON 外部能力契约，`ports/live.py` 只放
Worker-bound LIVE Port；`domain/live_handoff.py` 只放 LIVE route/event value object。

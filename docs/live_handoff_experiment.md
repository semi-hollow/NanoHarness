# Live Handoff 机制与真实 AgentLoop 集成证据

本文记录 `live-handoff-v1` 的能力边界、受控机制实验和真实 AgentLoop 集成证据。事实源是：

- [冻结计划](../benchmarks/experiments/live-handoff-v1/plan.json)
- [实验汇总](../benchmarks/experiments/live-handoff-v1/result.json)
- [真实 AgentLoop 派生证据](../benchmarks/experiments/live-handoff-v1/real-agent-loop-integration.json)
- 每个 controlled Run 的 `summary.json` 与 `timeline.jsonl`

## 核心映射

```text
HARD dependency
= producer 必须完成，consumer 才能启动

LIVE dependency
= producer 发布经过 Runtime 校验的 READY，consumer 即可启动

FEEDBACK
= downstream 对已消费版本提出的结构化约束证据

UPDATE
= upstream 因新证据产生的后续里程碑版本

Live delivery
= 尽早把证据送入 still-running Worker

Version validation
= integration 前拒绝使用过期版本的 candidate
```

Live delivery 是优化边界；version validation 是 correctness boundary。

## 证据分类（Evidence Classification）

本实验严格区分三类问题：

| 证据类别 | 本轮是否完成 | 能证明什么 |
|---|---:|---|
| `deterministic_controlled_mechanism` | 是 | HARD/LIVE readiness、双向事件、版本拒绝、因果偏序 |
| `deterministic_real_agent_loop_integration` | 是 | 事件真正经过 AgentLoop、ToolCall/Observation、safe boundary、worktree Diff 和 integration |
| real-model performance | 否 | Provider/模型质量、吞吐、Token、成功率均未评估 |

Controlled Case 中的固定等待只用于稳定暴露事件顺序。`wall_time_ms` 仍作为原始运行事实保留，但不得用于百分比性能提升或模型效率结论。

## 运行时流程（Runtime Flow）

### 1. Worker 只能发布事实

真实 AgentLoop 通过 `PublishHandoffEventTool` 提出 `READY`、`FEEDBACK` 或 `UPDATE`：

```text
Model ToolCall
→ PublishHandoffEventTool.execute()
→ producer identity 由 LiveWorkerContext 绑定
→ LiveHandoffRuntime.publish()
→ route / type / version / event-id validation
→ accepted or rejected Observation
```

`producer_task_id` 不在模型 Tool schema 中。即使模型提交同名额外参数，Tool 也不会使用它。Worker 没有 `start_worker()`、`cancel_peer()` 或修改 task graph 的能力。

工具执行阶段把 `publish_handoff_event` 显式识别为 `coordination_publish`：它不是普通 read，也不是 workspace/external non-idempotent side effect。`PermissionPolicy` 允许 plan-bound coordination；真正的授权边界是 frozen `LiveHandoffPlan` 与 `LiveHandoffRuntime` 的 fail-closed route/version 校验。它不需要人工 Approval，也不进入外部副作用 Operation Ledger。

### 2. Event commit 与 readiness

`LiveHandoffRuntime.publish()` 的顺序是：

```text
validate publisher / route / version / causal reference
→ append one timeline record and flush
→ MilestoneRegistry.commit(event)
→ WorkerMailbox.enqueue(event)
→ notify scheduler
```

`LiveHandoffRuntime.can_start()` 使用：

```text
HARD → producer state == completed
LIVE → MilestoneRegistry has accepted READY(producer, target, key)
```

真正启动 Worker 的 owner 仍是 `LiveHandoffCoordinator`。

### 3. Mailbox 不是共享 Context

`WorkerMailbox` 只保存 target Worker 尚未消费的结构化 `LiveHandoffEvent` FIFO。它不共享：

- `AgentRunSession.messages`
- Working Memory
- Tool Observation 历史
- Prompt Window
- Worker worktree
- candidate diff

真实 AgentLoop 的边界适配是：

```text
WorkerMailbox
→ LiveHandoffRunControl.drain_coordination()
→ RuntimeCoordinationSignal
→ RunControlHandler.consume_pending_signals()
→ [RUNTIME COORDINATION EVIDENCE]
→ next ModelPort.chat(...)
```

`RuntimeCoordinationSignal` 与人工 `RunControlSignal(STEER)` 是不同 domain signal。前者在 Trace/Checkpoint 中使用 `runtime_coordination` provenance，模型输入明确写明“peer-agent evidence, not user/operator instruction”。Provider transport 当前仍编码为 `role=user`，但内部审计和 rendering 不把它视作 human authority。

如果 coordination 在 `ModelPort.chat(...)` 进行期间到达：

```text
model response returns
→ RunControlHandler consumes new coordination
→ AgentLoop marks old response stale
→ recovery_decision(runtime_input_changed)
→ REPLAN
→ next real model turn sees the evidence
```

Runtime 不会中断正在执行的模型请求或 Tool；它只在真实模型安全边界收口。

### 4. 因果链（Causal chain）

`LiveHandoffEvent.event_id` 是内容哈希。可选 `caused_by_event_id` 必须引用已经被 Runtime 接受的事件，否则发布失败。

旗舰 Case 的链路是：

```text
READY#90c1...
→ FEEDBACK#dddf... caused_by READY#90c1...
→ producer consumes FEEDBACK at after_model:step=2
→ producer stale response discarded and replanned
→ producer candidate changes
→ UPDATE#c402... caused_by FEEDBACK#dddf...
→ consumer consumes UPDATE at after_model:step=2
```

Timeline 区分 proposed ToolCall、accepted event、mailbox enqueue、safe-boundary consume、candidate action 和后续 UPDATE；enqueue 不等于 consumed。

### 5. 版本正确性（Version correctness）

`MilestoneRegistry` 分别记录 latest version 与 consumer 实际消费版本。集成前：

```text
consumed_version != latest_version
→ stale_dependency
→ reject integration
```

本轮没有实现 automatic stale rerun。

## 受控机制 Case（Controlled Mechanism Cases）

| Case | 机制问题 |
|---|---|
| `early_unblock` | B 只需 A 的 keyword-only API contract，能否在 A 完成前开始 |
| `bidirectional_schema` | B 才能发现 `legacy_timeout`，能否在 A 完成前反向改变 candidate |
| `hard_dependency_control` | B 必须读取 A 的完成产物时，Runtime 是否仍保持 HARD 串行 |

### 基线契约（Baseline contract）

- `sequential`：completion-time handoff；Case 2 显式执行 A v1 → B late feedback → A revision → B rework。
- `naive_parallel`：一次性并行、无通信、无 recovery 的 negative control。它不是与其他模式 recovery 能力相同的性能 baseline。
- `live_handoff`：in-flight READY/FEEDBACK/UPDATE。
- `hard_dependency`：完成级依赖负对照。

机制在运行前冻结，不在脚本中硬编码“Live 必须赢”的 terminal status。最终状态由 candidate 与相同 integration assertion 决定。

### 受控结果（Controlled results）

| Case | Mode | Final | Derived rework | 解释 |
|---|---|---:|---:|---|
| Early Unblock | Sequential | PASS | 0 | 正确的完成级串行 |
| Early Unblock | Naive Parallel | FAIL | 0 | 无通信 negative control 猜错 contract |
| Early Unblock | Live Handoff | PASS | 0 | READY 在 producer 完成前解锁 consumer |
| Bidirectional Schema | Sequential | PASS | 2 | late feedback 后 producer revision + consumer rework |
| Bidirectional Schema | Naive Parallel | FAIL | 0 | 明确无 recovery；不伪造 rework |
| Bidirectional Schema | Live Handoff | PASS | 1 | producer 因 FEEDBACK 修改 candidate；consumer 完成前消费 v2 |
| Hard Control | HARD | PASS | 0 | consumer 只在 producer 完成后启动 |

`retry_count` 与 `rework_count` 来自 `LiveWorkerAttempt`：基础设施重试标成 `retry`，因新证据或晚期验证产生的新 candidate attempt 标成 `rework`。脚本不再直接填写聚合计数。

## 真实 AgentLoop 集成 Case

该 Case 使用 deterministic scripted `ModelPort`，但下面各层都是真实 NanoHarness 路径：

```text
LiveHandoffCoordinator
→ LiveAgentWorkerAdapter
→ LocalAgentWorkerAdapter worktree substrate
→ AgentLoop
→ PublishHandoffEventTool ToolCall / Observation
→ LiveHandoffRunControl safe boundary
→ replace_text workspace write
→ candidate_changes.diff
→ LiveCandidateDiffIntegration
→ executable integration assertion
```

### 实际边界顺序

```text
producer step 1  ToolCall READY(v1) accepted
consumer step 1  consumes READY before_model
consumer step 1  ToolCall FEEDBACK(v1) accepted
producer step 2  consumes FEEDBACK after_model
producer step 2  stale model response discarded; REPLAN
producer step 3  replace_text + ToolCall UPDATE(v2)
consumer step 2  consumes UPDATE after_model
consumer step 2  stale model response discarded; REPLAN
consumer step 3  replace_text
producer/consumer worktrees emit disjoint candidate diffs
integration applies both diffs and asserts {'timeout': 30}
```

派生证据记录：

- producer touched `config_schema.py`
- consumer touched `service_consumer.py`
- 两个 candidate diff 均有稳定 SHA-256
- 两个 Worker 都有 `runtime_input_changed` REPLAN
- 每条 coordination Trace 都记录 `human_authority=false`
- 最终 integration PASS

这证明 Live Handoff 已经进入真实 AgentLoop execution substrate；它不证明真实 LLM 会稳定发布正确事件，也不证明性能提升。

## 复用、新增与暂不实现（Reused / New / Omitted）

| 分类 | 内容 |
|---|---|
| 复用（Reused） | 复用 `AgentLoop`、`RunControlHandler` 安全边界、过期响应 REPLAN、`LocalAgentWorkerAdapter`、worktree、Tool Registry、candidate diff、touched-files 和 `GitFanoutWorkspace` 集成 |
| New | `LiveDependency`、`LiveHandoffEvent`、`MilestoneRegistry`、`WorkerMailbox`、`LiveHandoffRuntime`、`LiveHandoffCoordinator`、`RuntimeCoordinationSignal`、`PublishHandoffEventTool`、薄 `LiveAgentWorkerAdapter` |
| 暂不实现（Omitted） | 不实现 Planner LIVE prediction、通用 peer chat、A2A、Redis/Kafka/WebSocket、跨进程 mailbox、自动 stale rerun、coordination replay/resume 和真实模型 benchmark |

`LiveAgentWorkerAdapter` 没有复制 worktree 或 diff 逻辑；它只向 `LocalAgentWorkerAdapter.run_worker_with_options()` 注入 worker-bound Tool、coordination control 和 scripted/real model。

## 持久化边界（Persistence Boundary）

`JsonlLiveHandoffRepository` 每条 timeline 做 `write + flush`，最终 summary 使用 atomic JSON write。它没有：

- per-event fsync
- checkpoint restore
- mailbox replay
- crash continuation

因此准确表述是 `persisted/auditable coordination evidence`，不是 durable coordination recovery。Reader 不应从这些文件推断存在恢复能力。

## 能力限制（Limitations）

- Real AgentLoop Case 使用 scripted ModelPort；real-model performance 明确 `NOT EVALUATED`。
- `WorkerMailbox` 是进程内 FIFO，没有跨进程 transport。
- Provider request 和正在执行的 Tool 不可抢占；新证据在下一个 safe boundary 生效。
- stale candidate 只 detect + reject，不自动 rerun。
- semantic compatibility 未实现；即使 UPDATE 向后兼容，版本不同仍可能触发 conservative rejection。
- Controlled Case 的共享 `ExperimentState` 只用于 fixture 源文件断言，不进入模型 Conversation/Working Memory，也不是产品共享 Context。

## 结论

本轮证据支持两个有限结论：

1. HARD/LIVE milestone semantics、双向 FEEDBACK/UPDATE、因果引用与 stale rejection 在 deterministic mechanism cases 中成立。
2. 同一机制已经真实经过 NanoHarness AgentLoop、ToolCall/Observation、safe-boundary REPLAN、worktree candidate diff 和 integration。

本轮证据不支持“Live Handoff 普遍提高 Multi-Agent 编码性能”。该问题留给未来固定 provider/model、重复运行和失败分类实验。

## 复现

默认写入独立非 canonical 目录：

```bash
.venv/bin/python scripts/run_live_handoff_experiments.py
```

只运行真实 AgentLoop 集成 Case：

```bash
.venv/bin/python scripts/run_live_agent_handoff_integration.py
```

定向验证：

```bash
.venv/bin/python -m pytest -q \
  tests/test_live_handoff.py \
  tests/test_runtime_productization.py \
  tests/test_live_fanout.py \
  tests/test_architecture_boundaries.py \
  tests/test_port_implementation_hierarchy.py
```

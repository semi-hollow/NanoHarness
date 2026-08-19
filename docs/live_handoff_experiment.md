# Live Handoff 受控实验

本文记录 `live-handoff-v1` 的设计边界、三组冻结微型 Case、实际运行结果与限制。事实源是 [冻结计划](../benchmarks/experiments/live-handoff-v1/plan.json)、[汇总结果](../benchmarks/experiments/live-handoff-v1/result.json) 和每个 Run 的 `timeline.jsonl`；文中的数字不是预期值。

## Design：解决的问题

原有完成级依赖只有：

```text
A complete
→ B start
```

它适合 B 必须读取 A 最终产物的任务，但会把“只需要 A 的一个稳定中间契约”也串行化。Live Handoff 增加里程碑级依赖：

```text
A running
→ READY(key, v1)
→ Runtime validates and persists
→ B runnable
```

B 发现下游约束后只能提出结构化事实，不能直接操纵 A 或调度器：

```text
B FEEDBACK(v1)
→ Runtime
→ A mailbox
→ A drains at a named safe boundary
→ A changes remaining work
→ UPDATE(v2)
```

核心不变量仍是：`Model proposes, Runtime governs.`

## Architecture：最小控制面

### 1. Event 由谁发布

运行中的 Worker 只能通过 `LiveWorkerContext.publish()` 提交 `LiveHandoffEvent`。事件只有 `READY`、`FEEDBACK`、`UPDATE` 三类，并携带 producer、target、semantic key、version、summary、evidence 和 severity。

### 2. Runtime 把 Event 存在哪里

`LiveHandoffRuntime.publish()` 先验证发布者身份、Worker 状态、LIVE edge、版本和重复事件，再把事件追加并 flush 到该 Run 的 `timeline.jsonl`。只有持久化成功后，Runtime 才更新 `MilestoneRegistry` 与 `WorkerMailbox`；timeline append 是状态变更的 commit barrier。

### 3. Event 如何改变 readiness

`LiveHandoffRuntime.can_start()` 对两种依赖使用不同条件：

```text
HARD → producer state == completed
LIVE → MilestoneRegistry has accepted READY(producer, target, key)
```

Worker 本身没有 start/cancel 权限，真正启动仍由 `LiveHandoffCoordinator` 决定。

### 4. Event 如何进入下一 Turn

Runtime 把已接受事件放入 target 的 FIFO mailbox。Worker 只能在显式命名的协作安全边界调用 `drain_mailbox(boundary=...)`；本实验使用 `after_tool_observation_*` 和 `before_next_model_turn_*`。它不做 token-level interrupt，也不中断正在执行的 Tool。

### 5. integration 如何检查版本过期

`MilestoneRegistry` 同时记录 latest milestone version 与 consumer 实际 drain 的 consumed version。集成前，如果：

```text
consumer consumed v1
latest milestone is v2
```

`LiveHandoffCoordinator` 将状态收口为 `stale_dependency`，不调用 integration adapter。当前版本选择拒绝集成，未实现自动 rerun。

## Experiments：三个冻结 Case

| Case | A | B | 关键问题 |
|---|---|---|---|
| `early_unblock` | 迁移 core API | 迁移 SDK caller | B 只需 keyword-only signature，是否能在 A 完成前开始 |
| `bidirectional_schema` | 设计 config schema | 迁移 service consumer | B 才能发现线上配置仍使用 `legacy_timeout`，能否反向改变 A |
| `hard_dependency_control` | 生成最终常量 | 消费该常量 | B 必须等待最终产物，Runtime 是否保持串行 |

每个 Worker 都写入独立目录。候选 Python 文件先独立编译，最后由 integration adapter 合并执行并进行真实断言。

## Baselines：比较口径

前两个 Case 使用相同冻结任务比较：

- `sequential`：HARD edge，A 完成后 B 才启动。
- `naive_parallel`：无依赖、无事件，A 与 B 同时启动。
- `live_handoff`：LIVE edge，以里程碑和 mailbox 协作。

第三个 Case 只运行 `hard_dependency` negative control，因为它没有合理的 partial-dependency 语义。

## Results：实际结果

运行时间为本机一次 controlled run 的 wall clock；微型 Case 的固定等待用于稳定暴露因果顺序，不代表真实模型吞吐。

| Case | Mode | Wall time | Final test | Rework | 观察 |
|---|---:|---:|---:|---:|---|
| Early Unblock | Sequential | 206 ms | PASS | 0 | 正确但完全串行 |
| Early Unblock | Naive Parallel | 137 ms | FAIL | 0 | B 猜测旧 positional contract |
| Early Unblock | Live Handoff | 136 ms | PASS | 0 | B 在 A 完成前 99 ms 启动 |
| Bidirectional Schema | Sequential | 187 ms | FAIL | 1 | B 发现约束时 A 已完成 |
| Bidirectional Schema | Naive Parallel | 106 ms | FAIL | 1 | 更快但缺少协作，最终断言失败 |
| Bidirectional Schema | Live Handoff | 138 ms | PASS | 1 | feedback 真正改变 A，B 消费 v2 |
| Hard Control | HARD | 104 ms | PASS | 0 | B 在 A 完成的同一毫秒之后启动 |

Case 1 中，Live Handoff 相对正确的 Sequential baseline 减少 70 ms，约 34%。这个数字只说明该冻结 partial-dependency Case 暴露出了可利用重叠；不能外推到任意编码任务。

Case 2 的两个 baseline 均未通过最终测试，因此 138 ms 不能与 106 ms 解释成单纯的速度回归。Naive Parallel 的“更快”交付的是错误组合；Sequential 则缺少把 B 的新证据送回已完成 A 的机制。

## Timelines：关键事件顺序

### Case 1 — Early Unblock / Live Handoff 提前解锁

事实源：[timeline.jsonl](../benchmarks/experiments/live-handoff-v1/runs/early_unblock-live_handoff/timeline.jsonl)

```text
000 ms  producer START
036 ms  producer READY(api_contract, v1)
036 ms  consumer START
037 ms  consumer CONSUMES READY(v1)
101 ms  consumer COMPLETE
135 ms  producer COMPLETE
136 ms  INTEGRATION PASS
```

`consumer.started_at_ms=36 < producer.ended_at_ms=135`，直接证明 early unblock，不依赖日志措辞推断。

### Case 2 — Bidirectional Schema / Live Handoff 双向协作

事实源：[timeline.jsonl](../benchmarks/experiments/live-handoff-v1/runs/bidirectional_schema-live_handoff/timeline.jsonl)

```text
000 ms  producer START
036 ms  producer READY(config_schema, v1)
036 ms  consumer START and CONSUMES v1
060 ms  consumer DISCOVERS downstream constraint
060 ms  consumer FEEDBACK(blocking, v1)
063 ms  producer CONSUMES FEEDBACK at after_tool_observation_*
063 ms  producer CHANGES accepted keys and rewrites candidate
063 ms  producer UPDATE(config_schema, v2)
067 ms  consumer CONSUMES v2 and revalidates
111 ms  consumer COMPLETE
137 ms  producer COMPLETE
138 ms  INTEGRATION PASS
```

这里不仅“收到消息”：producer candidate 从只接受 `timeout` 改为把 `legacy_timeout` 映射到 `timeout`；最终集成断言用 legacy fixture 执行成功。consumer 的 `consumed_versions["producer:config_schema"]` 为 `2`。

### Case 3 — HARD 完成级依赖负对照

事实源：[timeline.jsonl](../benchmarks/experiments/live-handoff-v1/runs/hard_dependency_control-hard_dependency/timeline.jsonl)

```text
000 ms  producer START
073 ms  producer COMPLETE
073 ms  consumer START
104 ms  consumer COMPLETE
104 ms  INTEGRATION PASS
```

Runtime 没有为了并行而绕过 HARD edge。

## Failure / Limitations：当前边界

- 这是 deterministic mechanism evaluation，不是 provider/model 端到端 benchmark；它证明控制流和候选代码因 feedback 改变，不证明模型会稳定地产生高质量事件。
- mailbox 是进程内 FIFO，timeline 是本地 JSONL；没有网络 A2A、分布式队列或跨进程恢复。
- Worker 必须主动到达 named safe boundary 才能消费消息；Runtime 不会中断 model request 或正在执行的 Tool。
- 版本策略 intentionally conservative：只要 latest version 大于 consumed version 就拒绝集成，即使 update 在语义上兼容，也可能 false invalidation。
- stale candidate 当前只 reject integration；bounded rerun、semantic compatibility analysis 和自动 Planner 未实现。
- blocking feedback 被 Runtime 校验和投递，但 Worker 是否在完成前持续进入 safe boundary 仍属于 cooperative contract。
- 固定 sleep 只用于让实验顺序可重复；毫秒级绝对数字受机器调度和文件系统影响，应重点看偏序关系与最终断言。

## Conclusion：只基于本轮证据

三个 controlled cases 支持一个有限结论：milestone-level dependency 能在部分依赖任务中暴露正确并发；双向 in-flight feedback 能在上游完成前，把下游新证据转化为可观察的上游候选变更；HARD fallback 仍能保护真正的完成级依赖。

这些结果不支持“Live Handoff 普遍提升 Multi-Agent coding”的结论。下一步若继续推进，应先接入真实 AgentLoop worker，并用固定模型与重复运行测量事件质量、错误消息成本和 false invalidation 率。

## Commands：复现方式

默认写入非 canonical 的独立 Run 目录：

```bash
.venv/bin/python scripts/run_live_handoff_experiments.py
```

运行机制与相关回归测试：

```bash
.venv/bin/python -m pytest -q \
  tests/test_live_handoff.py \
  tests/test_live_fanout.py \
  tests/test_subagent_fanout.py \
  tests/test_architecture_boundaries.py \
  tests/test_port_implementation_hierarchy.py
```

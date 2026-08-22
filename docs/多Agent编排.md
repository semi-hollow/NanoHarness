# 多 Agent 编排

本文只描述 NanoHarness 当前 `feature/multi-agent-v1` 的真实实现。目标不是介绍一组 Multi-Agent 术语，而是回答三个问题：自然语言任务如何变成执行图、多个 Worker 如何在隔离环境中协作、Runtime 如何保证最终结果可信。

---

# 1. 它在系统里解决什么问题

输入是一条自然语言任务和一个 Git 仓库：

```text
Natural-language Task + Repository
```

输出不是多个 Agent 的聊天记录，而是：

```text
Integrated candidate diff
+ WorkerHandoff
+ Finalizer decision
+ auditable runtime evidence
```

Multi-Agent 层解决的是任务拆分、隔离执行、受控协作和确定性集成。它不取代 Single-Agent `AgentLoop`；每个 Worker 内部仍运行同一个真实 `AgentLoop`。

---

# 2. 唯一主链

```text
AdaptivePlanner.decide()
↓
PlanningDecision
↓
PlanningDecision.to_fanout_plan()
↓
FanoutPlan
↓
FanoutCoordinator.run()
↓
LocalAgentWorkerAdapter.run_worker()
↓
isolated AgentLoop + isolated Git worktree
↕
LiveHandoffRuntime
  ├── READY
  ├── FEEDBACK
  └── UPDATE
↓
candidate diff
↓
scope / conflict / version / apply / integration gates
↓
trusted WorkerHandoff
↓
LocalAgentWorkerAdapter.run_finalizer()
↓
Integrated result
```

系统只有一个执行计划、一个调度器、一个协作状态 Owner：

```text
FanoutPlan
FanoutCoordinator
LiveHandoffRuntime
```

没有第二套 Live plan、第二套 scheduler 或第二套 Worker framework。

---

# 3. 三个核心 Owner

## 3.1 `FanoutPlan`：执行契约

代码：`agent_forge/multi_agent/domain/live.py`。

`FanoutPlan` 保存目标、任务、全局验收标准和两类依赖。它的 `__post_init__()` 是计划规则的集中校验入口：未知任务、自依赖、重复依赖、HARD/LIVE 重叠、组合环和不安全的 LIVE write scope 都在这里 fail closed。

## 3.2 `FanoutCoordinator`：执行与集成

代码：`agent_forge/multi_agent/application/fanout.py`。

`FanoutCoordinator.run()` 负责 Worker 生命周期、HARD/LIVE readiness、并发、retry、replan、candidate gate、稳定集成和 Finalizer。它查询 `LiveHandoffRuntime` 的事实，但不自己保存第二份 mailbox/version 状态。

## 3.3 `LiveHandoffRuntime`：协作事实与版本门禁

代码：`agent_forge/multi_agent/application/live_handoff.py`。

`LiveHandoffRuntime` 在一个锁保护的状态边界内维护事件、mailbox、最新版本、已消费版本、generation、attempt 和集成封印。关键入口只有：

```text
begin_attempt()
publish()
drain_mailbox()
authorize_integration()
seal_integration()
```

Milestone、mailbox 和版本状态没有被拆成多组小服务；它们属于同一个一致性问题，因此由同一个 Runtime 管理。

---

# 4. Planner：建议计划，Runtime 决定能否执行

代码：`agent_forge/multi_agent/application/planning.py`。

```text
task
↓
AdaptivePlanner.decide()
↓
structured PlanningDecision
↓
PlanningDecision.to_fanout_plan()
↓
FanoutPlan deterministic validation
```

Planner 可以选择：

```text
mode = single
mode = fanout
```

在 fanout 模式下，Planner 提议任务、依赖、write scope、allowed tools 和 acceptance criteria。模型只提出结构化计划；是否允许执行由 `FanoutPlan` 的确定性规则决定。

核心原则：

```text
LLM proposes.
Runtime validates and executes.
```

---

# 5. HARD 与 LIVE 不是两套图

二者都属于同一个 `FanoutPlan`，并一起做 cycle 和 scope 校验。

## HARD：需要上游代码结果

真实字段：`SubagentTask.depends_on`。

```text
Producer successful integration
↓
Consumer may start
```

如果 Consumer 需要 Producer 已修改的文件，必须使用 HARD。READY、FEEDBACK 或 UPDATE 不能替代代码集成。

## LIVE：只允许提前消费语义证据

真实字段：`FanoutPlan.live_dependencies`。

```text
Producer publishes required READY/UPDATE
↓
Consumer may start before Producer completes
```

LIVE 改变的是启动时机，不降低最终正确性门槛。Consumer 最终集成前仍必须满足：

```text
relevant Producer successfully integrated and sealed
+
Consumer consumed Producer final current version
```

一句话区分：

```text
HARD controls when a Worker may start from trusted code state.
LIVE permits an early start from versioned semantic evidence.
Final integration remains fail closed.
```

---

# 6. READY / FEEDBACK / UPDATE 如何进入真实 AgentLoop

Worker 只有在 frozen plan 存在合法 LIVE route 时，才获得 `publish_handoff_event` 工具。普通 Worker 看不到这个工具。

```text
Agent ToolCall: publish_handoff_event
↓
PublishHandoffEventTool
↓
worker-bound LiveWorkerContext
↓
LiveHandoffRuntime.publish()
↓
route / type / version / causal validation
↓
target mailbox + coordination.jsonl
```

Producer identity、`plan_generation_id` 和 `worker_attempt_id` 由 Runtime 注入，模型不能伪造发布者身份。

三种事件的角色：

```text
READY
→ 上游宣布某个语义契约已可供下游开始使用

FEEDBACK
→ 下游把执行中发现的约束反馈给上游

UPDATE
→ 上游基于已经送达的 FEEDBACK 发布新版本
```

`UPDATE` 必须引用一条已验证、已送达给当前 Producer 的 `FEEDBACK`；不能用一个任意 event ID 伪造因果关系。

---

# 7. safe boundary 与 operator steer 的区别

`LiveHandoffRunControl` 复用现有 `AgentLoop` 的 RunControl seam，但 runtime coordination 不等于人类 steer。

```text
LiveHandoffRuntime mailbox
↓
RunControlHandler.consume_pending_signals(boundary="before_model")
↓
PreparedTurn model input
```

如果 coordination 在 provider request 进行中到达：

```text
provider returns old response
↓
AgentLoop checks after_model boundary
↓
old response is discarded
↓
REPLAN with current coordination evidence
```

传输给模型时可以编码为 `role=user`，但内容明确标记：

```text
[RUNTIME COORDINATION EVIDENCE]
human_authority=false
```

内部 trace/checkpoint 也使用独立的 `runtime_coordination` provenance，因此它不会被审计为 operator direction。

`coordination_publish` 是 Runtime 内部协作动作：它经过显式 route/version 授权和 Trace，但不是外部副作用，不要求 human Approval，也不进入 Operation Ledger。

---

# 8. Worker 隔离与可信集成

每个 Worker 都拥有独立：

- `AgentRunSession`；
- Conversation 和 WorkingMemory；
- Tool surface；
- Git worktree；
- candidate diff 和 trace。

Worker 不共享完整 Conversation，也不能直接读取其他 Worker 的私有 worktree。跨 Worker 只传递两类状态：

```text
semantic state
→ versioned READY / FEEDBACK / UPDATE

trusted code state
→ successful integration + WorkerHandoff
```

最终 candidate 还必须经过 declared scope、actual touched files、并发冲突、patch applicability 和 LIVE final-version gate。模型声称“完成”不能绕过这些 Runtime hard facts。

---

# 9. Attempt、Replan 与 Resume 边界

每个事件都绑定：

```text
plan_generation_id
worker_attempt_id
```

Worker retry 会使旧 attempt 的发布和消费状态失效；remaining-plan replan 会创建新 generation，旧 generation mailbox 不可复用。

当前恢复策略保持有界：

```text
worker retry <= 1
remaining-plan replan <= 1
merge conflict may use one serialized rerun
```

HARD-only plan 保留已有 checkpoint resume。LIVE plan 的 `resume_from` 当前直接拒绝，因为 mailbox、in-flight provider request 与已消费版本还没有完整 replay 语义。

Stale 只做 detect + reject，本版本不自动无限 rerun。

---

# 10. 唯一机制证据

入口：`scripts/run_multi_agent_v1_smoke.py`。

它使用 temporary Git repository 和 deterministic `ScriptedModelPort`，但经过真实：

```text
AdaptivePlanner
FanoutPlan
FanoutCoordinator
AgentLoop
ToolCall / Observation
RunControl safe boundary
isolated worktrees
candidate diffs
integration
Finalizer
```

唯一场景验证：

```text
A starts
↓
A publishes READY(v1)
↓
B starts before A completes
↓
B publishes FEEDBACK(v1)
↓
A receives it at a real AgentLoop boundary
↓
A discards stale in-flight response and publishes UPDATE(v2)
↓
B consumes v2
↓
A then B integrate under final freshness gates
↓
PASS
```

提交的派生证据位于：

```text
benchmarks/experiments/multi-agent-v1/mechanism-evidence.json
```

它证明 deterministic mechanism correctness 和 real-AgentLoop integration。它不评估真实模型效果，不声明性能提升，也不比较 sequential/naive-parallel wall time。

---

# 11. 当前明确不实现

- 真实模型 benchmark 或 Planner 质量 benchmark；
- speedup / Pass@1 提升声明；
- automatic semantic conflict resolver；
- unlimited retry/replan；
- LIVE checkpoint resume/replay；
- 分布式 mailbox、A2A 协议、Redis/MQ/Kubernetes worker platform；
- Agent 群聊、voting、recursive supervisor。

这些边界不是缺失的 CURRENT 功能，不应在面试中说成已经实现。

---

# 12. 最快的代码定位顺序

只看五处：

```text
1. agent_forge/multi_agent/application/planning.py
   AdaptivePlanner.decide()

2. agent_forge/multi_agent/domain/live.py
   FanoutPlan.__post_init__()

3. agent_forge/multi_agent/application/fanout.py
   FanoutCoordinator.run()
   FanoutCoordinator._run_live_plan()

4. agent_forge/multi_agent/application/live_handoff.py
   LiveHandoffRuntime.publish()
   LiveHandoffRuntime.drain_mailbox()
   LiveHandoffRuntime.authorize_integration()

5. agent_forge/runtime/application/agent_loop.py
   AgentLoop.run()
   before_model / after_model safe boundaries
```

如果只解释总体设计，前三处已经足够；只有需要说明“运行中的 Worker 如何收到反馈”时，再进入第 4、5 处。

30 秒版本：

> NanoHarness 先让 `AdaptivePlanner` 把自然语言任务转成唯一的 `FanoutPlan`。`FanoutCoordinator` 在隔离 worktree 中运行真实 `AgentLoop` Worker；HARD 依赖等待可信代码集成，LIVE 依赖只允许基于版本化 READY/FEEDBACK/UPDATE 提前启动。所有协作事实由一个 thread-safe `LiveHandoffRuntime` 管理，并通过真实 RunControl safe boundary 进入下一次模型输入。最终集成仍检查 Producer 已成功集成、Consumer 已消费最新版本，再由只读 Finalizer 验收。

# 功能演进：从最小 AgentLoop 到可治理 Harness

本文只回答：**先有了什么、暴露什么缺口、为何下一项能力必要**。代码分层见[代码结构演进](architecture/code-structure-evolution.md)；单次故障证据见
[失败驱动改进记录](evaluation/failure-driven-improvements.md)。

记录范围为 2026-04-29 至 2026-08-02。提交标题只作定位，成熟度以当前代码、测试和 [能力真实性矩阵](CAPABILITY_REALITY_MATRIX.md) 为准。

## 1. 先建立正确的演进顺序

NanoHarness 不是一开始就设计出 Checkpoint、HITL 和 Multi-Agent，而是从最小 AgentLoop 进入审计、治理、恢复、协作和评测闭环。

AgentLoop 未闭合时 Checkpoint 不知道保存什么；没有真实副作用时，审批、幂等账本和目标指纹也没有存在理由。

| 阶段 | 当时已经能做什么 | 暴露的问题 | 下一项能力的性质 |
| --- | --- | --- | --- |
| 04-29 至 05-06 | context -> model -> tool -> observation | 只能证明能跑，不能解释或恢复 | Trace/Artifact：必须 |
| 05-18 至 06-04 | 保存 diff、report、usage 和 run 元数据 | 工具可改变真实仓库，但边界和中断语义不清 | Policy/Sandbox/Checkpoint：必须 |
| 06-10 | 显式状态、工具可见性、路径和命令治理 | 人工等待跨进程丢失，恢复可能重复写 | Durable HITL：必须 |
| 07-10 | 审批可落盘并 Resume | 旧批准可能授权错误动作或已变化目标 | Key/Fingerprint/Ledger：必须 |
| 07-12 | 人工澄清、隔离 Worker、DAG Fanout | 并发会带来范围冲突和最终验收问题 | Scope Gate/Finalizer：必须 |
| 06-22 至 07-19 | SWE-bench、官方结果、对照 Campaign | 一次成功不能说明 Runtime 改进 | 分层证据和重复实验：必须 |
| 07-21 至 08-02 | Public API、Console、Workbench、Debug Lab | 原始 JSON 能审计但不适合操作和讲解 | 展示与学习控制面：建议但高价值 |

评测线和 Runtime 线曾并行推进，表格表达的是依赖关系，不伪造一条完全串行的开发历史。

## 2. 第一阶段：先闭合最小 AgentLoop

最初目标只是让模型在真实仓库里形成反馈循环：

```text
Task -> Context -> ModelResponse -> ToolCall -> Observation -> Next Turn
```

这一步先验证模型能否看到必要上下文、产生结构化工具意图并接收工具回填。早期 Memory 只是最近
若干条文本，审批只是进程内布尔判断，Multi-Agent 只是固定角色顺序调用。

此时不应该声称已有 Harness 控制面。没有稳定循环时先做恢复、长期记忆或并发，只会保存和放大
尚未定义清楚的状态。

代表节点：`1ecc501`、`41f3b93`。当前入口是 [`AgentLoop.run`](../agent_forge/runtime/application/agent_loop.py)。

## 3. 第二阶段：能跑以后，先让一次运行可解释

真实任务失败后，仅有最终回答无法区分模型判断错、上下文不足、工具失败还是验证环境不可用，
因此先加入 run identity、Trace、candidate diff、report、usage 和 diagnostics：

```text
一次运行
  -> 过程事实 Trace
  -> 文件改动 Candidate Diff
  -> 验证与成本 Evidence
  -> 可定位的 Artifact Directory
```

这一步解决“发生了什么”，还没有解决“中断后怎么继续”。Trace 类似支付系统的审计流水；它能
还原事实，但不能代替工作流状态。

代表节点：`13444eb`、`292670e`、`46122d2`。当前统一读模型是 [`RunStory`](../agent_forge/observability/domain/run_story.py)。

## 4. 第三阶段：真实工具迫使 Runtime 建立治理边界

当 Agent 能读写文件、运行命令和调用外部工具后，Prompt 不能再充当安全边界。项目因此增加：

- ToolRouter：只向当前任务暴露必要工具。
- Permission/CommandPolicy：把动作归类为允许、拒绝或需要人工确认。
- WorkspaceSandbox/ExecutionEnvironment：限制路径、工作区和网络边界。
- TaskCheckpoint：保存当前 step、状态、停止原因和恢复提示。

Checkpoint 是“流程走到哪里”的持久化快照，类似工作流引擎的流程实例；它不证明某个写操作是否
已经执行。这个区别直接引出了下一阶段。

### 4.1 为什么仅有最大步数还不够

仅靠 `max_steps` 时，模型会连续请求同一工具与参数。第一版统一阻断（`46122d2`），真实 Case 又
暴露合法读取被误杀，随后按副作用拆分（`d33a6d3`）。当前 `StepController` 只跟踪连续、规范化的
同一调用；不同动作重置。首次尝试加一次重试后，第三次观察/验证调用跳过，副作用调用停止。
副作用先查 Ledger；已有未漂移的 `executed` 事实只回填原 Observation，不再次执行。
这是明确的控制合同：一次重试容纳暂态失败，第三次相同动作代表没有新证据；边界由回归固定。

代表节点：`46122d2`、`d33a6d3`、`4839b80`、`993926e`、`6ab6b7a`。

## 5. 第四阶段：HITL 从按钮效果变成持久化控制协议

### 5.1 为什么不能继续使用自动批准

早期审批只在当前进程返回 `True/False`。一旦 Runtime 为等待人而退出，请求和决定都会丢失；
如果直接重跑，模型还可能重新生成一次写操作。因此审批必须变成：

```text
ToolCall
  -> Policy 返回 ASK
  -> ApprovalRequest(pending) 落盘
  -> Checkpoint(WAITING_APPROVAL)
  -> Runtime 停止
  -> 人批准或拒绝
  -> Resume
```

这是第一版 durable HITL。`ApprovalRequest` 保存的是**是否授权副作用**；后来加入的
`HumanInputRequest` 保存的是**人对模型问题的答案**。二者不能共用一个 `approved=true`：
一个改变权限，另一个只提供信息。

### 5.2 为什么审批后还需要 operation key

Resume 时 ToolCall id 和 run id 都可能变化，不能用它们寻找旧审批。Runtime 需要给“同一个操作
意图”一个跨 continuation 稳定的身份：

```text
operation key = hash(tool name + canonical arguments JSON + resolved workspace + action)
```

它类似支付系统的**幂等键或业务请求号**：回答“这是不是之前那次操作”，不回答文件有没有变化。
例如同一工作区再次请求 `replace_text(path="a.py", old="x", new="y")`，会得到同一个 key；
不同工作区或参数不会误用同一批准。

当前 owner 是 [`OperationTracker`](../agent_forge/runtime/application/operation_tracker.py) 和
[`JsonOperationLedgerRepository`](../agent_forge/runtime/adapters/operation_ledger_json.py)。

### 5.3 为什么同一个 operation key 仍然不够

人批准后到 Resume 前存在时间窗口。即使操作参数没变，目标文件可能已被用户、另一个 Agent 或
Git 操作修改。旧批准不能自动授权新状态，因此审批时保存 target fingerprint，恢复时重新计算：

```text
文件 fingerprint = resolved path + exists + sha256 + size
```

它类似数据库的**乐观锁版本或更新前镜像**：回答“批准时看到的目标现在还是不是同一状态”。

```text
key 相同 + fingerprint 相同 -> 仍是原操作和原目标，可继续
key 相同 + fingerprint 不同 -> 批准已 stale，停止并重新申请
```

Operation key 不能代替 fingerprint：前者标识意图，后者标识目标当时的状态。

### 5.4 为什么还需要 Operation Ledger

即使批准和目标都有效，进程仍可能在“文件已经写入、但 Checkpoint 尚未更新”时崩溃。只恢复
Checkpoint 会再次执行同一写操作。Operation Ledger 因而在副作用边界维护：

```text
planned -> pending -> approved -> executing -> executed / failed
```

- `executing` 在调用真实工具**之前**落盘；崩溃后结果不确定，必须 fail closed。
- `executed` 且 post-fingerprint 仍匹配：Resume 跳过重复执行，并回填已有成功事实。
- 已执行但目标后来漂移：停止，不能把旧结果冒充当前结果。

它类似支付系统的**交易订单状态机加幂等流水**。三者职责不能混在一起：

| 机制 | 回答的问题 | Java/支付类比 |
| --- | --- | --- |
| TaskCheckpoint | 整个 Agent 流程走到哪里 | 流程实例快照 |
| ApprovalRequest | 人是否授权这个动作 | 风控/人工审批单 |
| Operation key | 这是不是同一次动作 | 幂等键、业务请求号 |
| Fingerprint | 目标是否仍是批准时的状态 | 乐观锁版本、before image |
| Operation Ledger | 副作用执行到哪一步、能否重放 | 交易状态机、操作流水 |

这条链由 `a842c29 -> 0eb5c73 -> 41bd42f` 逐步补齐，不是一次性按术语清单加入。

## 6. 第五阶段：人工控制从审批扩展到澄清和 Steer

有些中断不是权限问题。模型可能缺少需求信息，操作者也可能在运行中改变优先级。因此后续拆成
三条类型化通道：

```text
approval  -> approved/rejected -> 决定副作用能否执行
ask_human -> answer            -> 作为 Observation 回填会话
steer     -> message           -> 在下一模型边界加入 User Message
```

`ask_human` 从模拟 Tool 返回升级为 pending/responded/cancelled 请求和
`WAITING_HUMAN` Checkpoint；pause/cancel/steer 则在 safe point 消费，避免在工具事务中间强行
插入控制信号。Operator Console 可以共用一个输入区域，但 Runtime 的类型、Store 和恢复分支独立。

代表节点：`fb89801`、`b6cbc8e`、`52d2d12`。

## 7. 第六阶段：长任务出现后再补 Context 与 Memory 分层

最初保留最近 N 条消息足以支撑短循环；真实长任务随后暴露上下文超预算、旧观察挤占关键事实和
压缩结果不可追溯的问题。当前按职责拆成：

- Working memory：当前 run 的消息和观察。
- Digest：超预算历史的结构化摘要。
- Long-term memory：经提议、校验后跨 run 保存的可检索事实。
- Context report：记录选择、预算、截断和压缩原因。

压缩采用“先选事实，再生成摘要，再校验预算”的事务式替换；失败时保留原上下文。当前长期记忆
是受治理的 lexical recall，不声称 Vector RAG、KV Cache 恢复或模型可任意写入权威事实。

代表节点：`6d4f696`、`5cb14fe`。

## 8. 第七阶段：Single Agent 稳定后，Multi-Agent 才有意义

Multi-Agent 也经历了三次形态变化：

```text
固定角色串行
  -> Implementer/Reviewer/Verifier 通过 Artifact 顺序交接
  -> 显式 DAG + 独立 Worktree Worker + Scope Gate + Finalizer
```

只有依赖满足、声明写入范围不重叠的任务才进入同一并发批次。Worker 在独立工作区运行真实
AgentLoop，合并前再次检查实际 diff；冲突、越界和不确定结果 fail closed，Finalizer 只读验证
整体结果。

当前不实现模型自主拆任意任务、LLM 冲突合并或 distributed swarm。代表节点：`717138d`、
`fb89801`。入口是 [`LiveFanoutCoordinator.run`](../agent_forge/multi_agent/application/live_fanout.py)。

## 9. 第八阶段：从 Patch Demo 到评测改进闭环

最初只检查“有没有生成 patch”，很快暴露过度声明：Reviewer PASS、本地测试通过和官方 Case
解决不是同一个证据层级。因此评测线逐步形成：

```text
Candidate Diff
  -> Local Validation
  -> Official Per-case Result
  -> Failure Taxonomy
  -> Baseline/Governed Paired Comparison
  -> Repeated Campaign
  -> Human-reviewed Improvement Decision
```

数据飞轮不是“每次失败自动加一个 UT”，也不是把结果再喂给 LLM 自动得出结论。当前闭环是：
收集固定 schema 的运行事实，按确定性优先级分类，人工复核代表性 badcase，提出 Runtime 改进假设，
在固定任务和预算下做 paired regression，再决定保留、回滚或继续实验。它产生工程决策证据，尚未
接入模型训练或 RL 数据管线。

`adbe5e7` 将项目收窄到 SWE-bench 主线；`5c3c83c` 至 `35faa0d` 修正 taxonomy、stale case
study 和 official solved 语义；`2a7fd69`、`0961f00` 增加官方解析、隔离执行和重复 Campaign。

## 10. 第九阶段：最后才补框架入口和展示控制面

内部 owner 稳定后，项目才提供 `Harness.run/resume`、类型化配置、Ports、Hooks 和 composition
root，让调用方不需要认识全部内部阶段。原始 Artifact 已能审计，但不适合实时操作和面试讲解，
因此再增加：

- Operator Console：运行时事件、审批、回答和 continuation。
- Workbench：把 Trace、Checkpoint、Artifact 和评测结果投影为只读 Evidence View。
- 三个 Debug Lab：分别学习受治理运行、协作执行和评测闭环。

这些是 Presentation/Read Model，不是第二套 Runtime。展示层只能解释已产生事实，不能提升证据
等级或替 Runtime 宣称 solved。

## 11. 面试时怎样讲这段历史

不要从十五个模块开始背。先用一句话给出主线，再选一条深入：

> 我先闭合了 repository AgentLoop，真实工具加入后建立策略和隔离；运行可中断后引入
> Checkpoint 与 durable HITL，而恢复又暴露重复副作用和 stale approval，所以继续加入
> operation key、target fingerprint 和 execution ledger。最后才扩展隔离 Multi-Agent、
> SWE-bench 评测闭环和操作者展示面。

深入 HITL 时按一个失败窗口讲：**为什么出错 -> 只加前一个机制为什么仍不够 -> 新机制保护什么
不变量 -> 当前还有什么边界**。这样讲的是亲历的工程推导，而不是 Agent 名词表。

当前没有实现的 hosted service、分布式 Worker、自动训练数据回流和全量 benchmark 结果，统一以
[能力真实性矩阵](CAPABILITY_REALITY_MATRIX.md)为边界；未来计划见 [Roadmap](ROADMAP.md)。

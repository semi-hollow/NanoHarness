# Runtime 控制面：暂停、人工输入与恢复

> 定位：这是面试追问和架构审计材料，不是项目首轮学习必读。首轮只看 README 的运行主链；需要
> 解释审批、恢复、重复副作用或 Ledger 时再进入本文。

本文只解释控制语义和现场展示。公开动作以 `forge run / resume / demo / inspect` 为准；不会再为
answer、approval 或展示维护第二套 CLI。

## 1. 当前任务模型

NanoHarness 仍是 **one command, one run, one task**。一次 run 通过 `AgentLoop` 推进，并把可恢复
状态写入 `TaskCheckpoint`：

```text
forge run -> Harness.run -> AgentLoop.run
  -> running
  -> completed / blocked / failed
  -> waiting_human / waiting_approval / paused / cancelled
```

`RunController` 支持协作式 pause、cancel、steer，但只在 turn 开始、模型返回、工具执行前后等
safe boundary 消费信号；它不强杀正在执行的 HTTP/进程，也不回滚已经完成的副作用。项目没有
session `active_task` pointer、任务队列或“暂停 A 后自动切换 B”的产品边界。

## 2. 四种控制不能混为一谈

| 控制 | 作用对象 | 当前入口 | 不包含什么 |
| --- | --- | --- | --- |
| 回答问题 | 一条 pending human-input request | `forge resume <run> --answer ...` | 不授权副作用 |
| 审批副作用 | 一个带 fingerprint 的 operation | `forge resume <run> --decision approved\|rejected` | 不授予永久写权限 |
| pause/cancel/steer | 当前嵌入式 run | `RunController` | 不做进程抢占、全局调度或自动回滚 |
| active-task switch | 会话中的多个任务 | 未实现 | 没有隐藏 task queue 或自动恢复旧任务 |

`resume` 先原子记录 answer/decision，再从 durable checkpoint 创建一条新的 continuation run。
它不会恢复 Python stack、HTTP connection、provider KV Cache 或模型隐藏状态。

写工具没有绕过审批。`PermissionPolicy` 对 `write` 始终返回 `ASK`；随后有两种决定来源：

- `auto_approve_writes=false`：写入 pending approval 和 `WAITING_APPROVAL` checkpoint，等待人批准。
- `auto_approve_writes=true`：Runtime 记录 `auto_approved` 后继续，主要用于受控 Lab 和测试。

Lab 2 使用第二种配置，使多 Agent 编排演示不被人工输入打断；Approval/HITL Demo 使用第一种。
两条路径都经过同一 `ToolAuthorizationGate` 和 Operation Ledger。

### 副作用为何需要独立账本

1. 尚未执行：approval/HITL barrier 保证工具不启动。
2. 正在执行：只能在工具返回后观察确定状态，当前不会中途强杀。
3. 已经执行：`OperationTracker` 保留 operation key 与 pre/post fingerprint，恢复时防重放。
4. 目标已改变：旧 approval 变 stale，不能靠历史 `approved=true` 继续执行。

因此 cancelled/blocked 描述的是 run 状态，不等于事务已经补偿。Operation ledger 是幂等审计边界，
不是 distributed transaction log。

## 3. ToolExecutionPipeline 阅读地图

```text
execute_calls
  -> _select_calls_for_turn       数量上限 + HITL barrier
  -> 对每个 ToolCall 调用 _execute_call
       -> guardrail / 路由检查
       -> ask_human 协议分支
       -> OperationTracker        identity + replay/stale
       -> 连续重复策略             first attempt + one retry
       -> ToolAuthorizationGate   allow / deny / ask
       -> 仅当 proceed=True 时调用 _run_tool
            -> 最后一次 pause / cancel 检查
            -> Ledger 进入 executing
            -> ToolGateway.execute
            -> after_tool Hook
            -> Observation / evidence / checkpoint
```

首轮只读 `execute_calls`、`OperationTracker` 和 `ToolAuthorizationGate` 的合同。`_` 方法是主链内部
分支，不是外部 facade。尤其注意：`_run_tool` 是 `_execute_call` 授权通过后的子分支，不是与
`_execute_call` 平级的第二个入口；只有排查具体 failure 时才展开。

单个调用离开主链时使用显式 `ToolCallOutcome`，而不是让 `None` 同时表示多种结果：

| 结果 | 含义 | 是否调用真实工具 |
| --- | --- | --- |
| `EXECUTED` | 已越过治理边界并取得工具结果 | 是 |
| `SKIPPED` | Ledger 已有确定事实，或无需 durable side-effect Ledger 的相同调用达到上限 | 否 |
| `FAILED` | 路由、参数、授权或工具结果失败，但 run 可继续 | 视分支而定 |
| `STOPPED` | 需要人工介入或无法安全继续 | 否或此前结果不确定 |

### “重复请求”和“已经执行”不是同一判断

| 判断 | Owner | 回答的问题 | 当前策略 |
| --- | --- | --- | --- |
| 连续重复 | `StepController` | 模型是否连续给出完全相同的工具名与规范化参数、没有推进 | 允许首次尝试和一次重试；第三次连续相同调用进入重复分支；不同动作立即重置 |
| 已经执行 | `OperationLedger` | 该副作用是否已有 durable `executed` 记录，且当前目标仍匹配执行后 fingerprint | 不再执行工具，只把上次 Observation 作为确定事实回填 |

因此一次调用可能“重复但从未执行”，例如前两次都被权限拒绝；也可能“以前执行过但不是当前连续
重复”，例如 continuation 再次生成同一写操作。副作用先查 Ledger，再执行重复策略，避免把已经成功
的写操作误判成普通模型循环。

“重放事实”不是重放副作用：Runtime 不再调用 `ToolGateway.execute`，而是把 Ledger 保存的上次
Observation 组装成成功 Tool Message，让下一轮模型知道该动作已经完成。这类似支付重试命中幂等
订单后返回原交易结果，而不是再次扣款。

### Hook 和 Trace 也不是同一机制

Hook 类似 Java Interceptor、Listener 或 AOP 切点：Runtime 到达模型前后、工具前后、Checkpoint 后
或停止前时，允许外部策略介入。它可以 `ALLOW/DENY/ASK`、改写模型响应、脱敏 Observation 或阻止
完成。Trace 类似审计流水，只记录 Hook 及其他阶段已经发生的事实，不参与决策。项目会把 Hook 决定
写入 Trace，但“Hook 被记录”不等于“Hook 就是 Trace”。

## 4. Trace、Checkpoint、Ledger 和 Artifact 的边界

这四个概念都可能落成 JSON 文件，但它们不是同一层数据。可以用支付系统类比：

| 概念 | Java / 支付类比 | 回答的问题 | 不是什么 |
| --- | --- | --- | --- |
| `TraceEvent` | 审计事件流或分布式 Trace span | 某个 step 发生过什么、顺序和结果是什么 | 不能单独恢复运行，也不直接下 solved 结论 |
| `TaskCheckpoint` | durable workflow 状态快照 | 从哪个状态、step、消息摘要继续 | 不是数据库 savepoint、进程镜像或模型 KV Cache |
| `OperationLedger` | 支付订单/幂等操作状态表 | 这个副作用是否 planned、approved、executing、executed，能否重放 | 不是普通日志，也不保存完整会话 |
| `Artifact` | 对账文件、回执或构建产物 | 哪个消费者可读取的结果文件由谁产生、证明什么 | 文件存在不等于结果正确或 official resolved |
| 普通 Log | 应用排障日志 | 开发者当时想看的诊断文本 | 不是稳定机器契约，不应驱动报告结论 |

Trace 的真实链路是：

```text
Application 中的语义动作
-> _record_model_started / _record_tool_execution_started / _record_...
-> EventSink.add(统一事件 envelope + capability payload)
-> JSON Trace Adapter 持久化 trace.json
-> Run Story Read Model 按阶段投影
-> forge inspect / Workbench 分层展示
```

`EventSink.add` 像审计 SDK 的底层写接口；Application 主流程不应直接展开十几行字段。当前原始
payload 只保留在可折叠的 `_record_*` 证据叶子，并由 `tests/test_code_navigation.py` 静态检查。
这样折叠代码时先看到业务控制流，排障或修改 schema 时才展开证据字段。

一次 turn 也不保证事件数完全相同：只读工具、写工具、审批、失败恢复和最终回答经过的条件分支
不同。人类应记住四段稳定主链，而不是背事件数量：

```text
准备输入 -> 模型决定 -> 治理并执行 -> 回填并持久化
```

原始事件是机器审计粒度；四段 Run Story 是面试和学习粒度；Failure Taxonomy 与 Evaluation 是
运行结束后的结论粒度。三层不能相互替代。

## 5. 两分钟现场展示

Approval 是默认 scenario：

```bash
forge demo
```

HITL 使用同一个命令形态：

```bash
forge demo --scenario hitl --answer "Python 3.11"
```

一个命令内部依次运行 waiting phase、记录确定性人工决定、创建 continuation，并打印可直接交给
`forge inspect` 的目标。现场只指出：ToolCall、operation/request identity、durable checkpoint、
continuation、ledger stale/replay check 和最终受治理副作用。

Demo 使用确定性 `ModelPort` 固定 tool call，但复用正式 `Harness`、`AgentLoop`、repositories、
operation ledger、checkpoint、trace 和 tool。它证明控制面接线，不证明在线模型推理、pytest 通过
或 official resolved；Demo 的 local/official 状态应分别是 Not Run / Not Evaluated。

真实任务被阻断时才使用 `forge resume`。Demo 已把两阶段收在一个命令里，不要求记忆旧的
`showcase start/continue` 命令。

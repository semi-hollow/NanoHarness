# Runtime 控制面：暂停、人工输入与恢复

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
  -> _execute_call
       -> ToolRouter / ToolGateway
       -> OperationTracker        identity + replay/stale
       -> ToolAuthorizationGate   allow / deny / ask
       -> Tool.execute            真实外部动作
       -> validation/trace/checkpoint evidence
```

首轮只读 `execute_calls`、`OperationTracker` 和 `ToolAuthorizationGate` 的合同。`_` 方法是主链内部
分支，不是外部 facade；只有排查具体 failure 时才展开。

## 4. 两分钟现场展示

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

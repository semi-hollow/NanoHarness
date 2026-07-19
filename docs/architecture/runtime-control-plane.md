# Runtime 控制面：任务、暂停、取消与恢复

本文回答四个容易混淆的问题：NanoHarness 管理的“任务”是什么、哪些状态是真实的、
取消是否会回滚副作用，以及如何现场展示 HITL 和审批恢复。

## 1. 当前任务模型

NanoHarness 当前采用 **one command, one run, one task**：一次 `forge run` 创建一个
`TaskCheckpoint`，随后由 `AgentLoop.run` 推进到完成、阻断或人工等待状态。

```text
forge run
  -> AgentLoop.run
  -> RunPreparation.start
  -> TaskCheckpoint
  -> turn loop
  -> completed / blocked / failed / waiting_human / waiting_approval
```

当前没有会话级 `TaskManager`、`active_task_id` 或 `PAUSED` 状态。因此下面这项能力
**没有实现**：

```text
Task A running -> paused
Task B pending -> active
```

这不是遗漏了一个枚举值，而是缺少持久会话调度这一完整产品边界。若未来加入交互式
长会话，需要一起增加 `SessionTaskRepository`、当前活跃任务指针、切换规则、任务级
取消协议和 UI/CLI 入口；只添加 `PAUSED` 会成为没有 Runtime owner 的装饰状态。

## 2. 三种人工控制不能混为一谈

| 控制 | 作用对象 | 当前行为 | 不包含什么 |
| --- | --- | --- | --- |
| `respond --cancel` | 一条 human-input request | request 进入 terminal `cancelled`，恢复时当前 run 被阻断 | 不取消其他 run，不回滚已执行工具 |
| `approve --decision rejected` | 一次具体副作用 intent | 工具保持未执行，审批记录为 rejected | 不等同于取消整个任务 |
| Task cancel | 整个正在运行的 task | 未实现 | 没有进程抢占、补偿事务或全局 active-task registry |

### 为什么取消必须检查副作用

“取消”不是天然的回滚。可靠实现至少要区分：

1. **尚未执行**：撤销 pending operation，保证工具不会启动。
2. **正在执行**：请求 cooperative cancellation，并等待工具返回确定状态。
3. **已经执行**：保留 operation ledger；只有定义了 compensation 才能反向操作。
4. **状态未知**：不能声称取消成功，必须先重新读取目标并核对 fingerprint。

NanoHarness 当前没有全局 Task cancel，所以也不声称自动补偿。它已经实现的安全边界是：

- `_select_calls_for_turn` 把 `ask_human` 变成同一 turn 的 barrier；即使模型同时返回
  `write_file`，本轮也只处理人工问题。
- `ToolAuthorizationGate` 在真实工具执行前持久化 pending approval；拒绝时不调用工具。
- `OperationTracker` 记录 planned/approved/executed 和 pre/post fingerprint，用于恢复时
  防止重复副作用；它是幂等账本，不是回滚系统。

因此对外说明时应说：“暂停点之前的副作用由 barrier 和 approval gate 阻止；更早 turn
已经完成的副作用不会因 request cancel 自动撤销。”

## 3. ToolExecutionPipeline 阅读地图

折叠文件后只展开两个方法：

```text
execute_calls                         外围唯一入口
  -> _select_calls_for_turn           数量上限 + HITL barrier
  -> _execute_call                    单个工具的治理主干
       -> _handle_repeat              重复调用异常分支
       -> _record_unrouted_tool       不可见工具异常分支
       -> _handle_human_question      waiting_human / 已回答
       -> OperationTracker            幂等与 fingerprint
       -> ToolAuthorizationGate       allow / deny / ask
       -> _run_tool                   真实执行与 checkpoint
            -> _record_execution_evidence
```

`_` 开头的方法全部是内部叶子，不是外围连接方法。当前这些方法均有真实调用；没有为
“显得分层”而保留的未使用步骤。

## 4. 两分钟现场展示

### HITL：提问、checkpoint、回答、恢复

```bash
forge showcase hitl start
```

输出会显示 `State: waiting_human`、checkpoint、trace、request id 和下一条完整命令。
直接执行输出中的 `Next command`，例如：

```bash
forge showcase hitl continue .agent_forge/showcases/hitl-<id> \
  --answer "Python 3.11"
```

第二步应显示 `State: completed`。可现场打开：

- `showcase.json`：当前状态和所有 artifact 路径；
- `showcase.md`：当前状态、安全断言、证据路径与下一条命令的一页现场报告；
- `start-trace.json`：`human_input_requested`；
- `continuation-trace.json`：`resume_state_loaded` 和 `human_input_response_loaded`；
- `task_state/*.json`：前后两个 checkpoint。

### Approval：写入前暂停、批准、继续

```bash
forge showcase approval start
```

此时 `workspace/target.py` 仍是 `value = 1`。执行输出中的下一条命令：

```bash
forge showcase approval continue .agent_forge/showcases/approval-<id>
```

继续后状态为 `completed`，文件才变成 `value = 2`。审批文件、operation ledger、前后
checkpoint 与 trace 都位于同一个 run 目录。

## 5. 演示真实性边界

Showcase 为了稳定，使用确定性 `ModelPort` 固定“模型会发出哪个 tool call”。被模拟的
只有模型输出。以下部分全部复用正式实现：`AgentLoop`、工具路由、HITL repository、
approval repository、operation ledger、fingerprint、checkpoint、trace 和
`ApplyPatchTool`。它证明的是 Harness 控制面，而不是模型推理能力。

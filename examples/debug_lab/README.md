# NanoHarness Debug Lab：三类可复现场景

本目录提供项目统一的动态调试入口，覆盖安装、运行、断点、状态预测和 Evidence 复核。
机制定义见[核心运行机制与代码索引](../../docs/核心运行机制与代码索引.md)；Debugger 用于
观察状态变化，Workbench 用于复核持久化证据。

## 首次准备

首次 clone 后，在项目根目录执行 README 的标准安装命令：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

四个 `.run` 配置已经随仓库提供。只有需要自动安装阅读 Scope 和按函数名重新定位断点时，才关闭
PyCharm 并额外执行一次 `.venv/bin/python scripts/install_pycharm_debug_lab.py`；平时不需要阅读该脚本。

然后用 PyCharm 单独打开 NanoHarness，使用右上角绿色 Debug 按钮。前三个配置负责运行，最后一个只读复盘：

| 顺序 | PyCharm 配置 | 主要验证问题 | 模型 |
| --- | --- | --- | --- |
| 1 | `NanoHarness Lab 1 - Governed Repair` | 人工选择与补丁审批如何经按钮控制、Checkpoint、恢复和验证形成一条证据链 | 确定性适配器，免费 |
| 2 | `NanoHarness Lab 2 - Coordinated Agents` | 正常路径与异常分支如何拆给并行 Worker、依赖验证器和只读 Finalizer | 确定性适配器，免费 |
| 3 | `NanoHarness Lab 3 - Complex Live Repair` | 真实模型怎样在多模块缺陷中检索、试错、修改、回归并收敛 | DeepSeek，产生费用 |
| - | `NanoHarness Evidence Workbench - Read Only` | 不重跑 Lab，直接打开最近一次 Lab 3 Evidence；页面内可切换其他来源 | 不调用模型，免费 |

断点按场景自动隔离：Lab 1 有 7 个、Lab 2 有 5 个、Lab 3 有 5 个。运行一个 Lab 不会停进另一个
Lab。Project 面板优先选择 `00 NanoHarness Review Path`；Lab 2/3 再切到
`05 NanoHarness Extended Flows`。

## Lab 1：受治理运行

```text
run_governed
→ GovernedShowcaseConsoleApp
→ HumanInput choice
→ Harness.run / continuation
→ AgentLoop.run
→ ToolExecutionPipeline._execute_call
→ OperationTracker / ToolAuthorizationGate
→ Approval decision
→ PythonValidationTool
→ RunLifecycle.finalize_run
→ Evidence Workbench
```

运行配置会打开一屏按钮式控制台，不需要在命令行输入 request id 或 operation key：

1. 点击“开始受治理任务”，Runtime 先保存人工问题并停在 `waiting_human`。
2. 点击一个兼容目标，回答写入 HumanInput Repository；continuation 随后生成具体补丁。
3. 控制台展示当前文件、待执行内容和 operation key；此时文件仍未改变。
4. 点击“批准并继续”后才执行写工具与 focused pytest；点击拒绝则保持 workspace 不变。
5. 终态自动打开同一份只读 Workbench，也可在控制台中再次点击打开。

重点观察五个对象：

1. `session`：一次 run 的消息、观察、工具历史和生命周期。
2. `tool_call`：模型提出的意图，还不代表允许执行。
3. `operation_intent`：可能改变持久状态的操作意图、operation key 和目标 fingerprint。
4. `checkpoint`：可恢复业务状态，不是 Python 调用栈。
5. `validation_evidence`：工具真实执行的 focused pytest 结果。

停在 `ToolExecutionPipeline._execute_call` 时，只按四层观察，不展开所有私有方法：

| 层级 | 当前断点要确认什么 | 继续下钻的代码 |
| --- | --- | --- |
| 入口控制 | 工具是否属于本轮允许集合，ToolCall 是否符合调用契约 | `ToolRouter`、`ToolRegistry` |
| 执行决策 | 操作是否已有结果、是否允许或需要人工授权 | `OperationTracker`、`ToolAuthorizationGate` |
| 受限执行 | 工具是否仍受命令、路径和运行环境约束 | `CommandPolicy`、`WorkspaceSandbox`、`ExecutionEnvironment` |
| 结果与恢复 | Observation、操作状态和 Checkpoint 怎样提交 | `_run_tool`、`RunLifecycle` |

验证标准：能预测并看到
`waiting_human → waiting_approval → completed`；两次人工动作都先持久化再 continuation；
审批前 `compatibility.py` 保持 `unselected`；批准后才出现真实写入和 pytest 证据。拒绝分支必须保持
文件不变。Workbench 中可继续核对 request、operation fingerprint、Checkpoint、实际写入和验证结果。

## Lab 2：多 Agent 协同

```text
run_coordinated
→ LiveFanoutCoordinator.run
→ pricing-policy + shipping-policy（并行批次）
→ LocalAgentWorkerAdapter.run_worker
→ scope gate / merge
→ edge-case-verifier（依赖批次）
→ LocalAgentWorkerAdapter.run_finalizer
```

第一个批次的两个 Worker 在独立 Git worktree 修改 `pricing.py` 和 `shipping.py`。它们分别处理价格
输入约束与运输策略，写入范围不重叠，因此允许并行。第二个只读 Worker 同时依赖两项实现，只有前序
Diff 通过 scope gate 并合并后，才运行异常场景矩阵：

- 负数小计、负折扣和超额折扣必须 fail closed。
- 普通国内订单达到阈值后免运费。
- 加急订单不能被免运费规则误伤。
- 未知运输区域必须明确拒绝，不能静默返回零费用。

声明写入范围不等于实际安全；Coordinator 仍核对 touched files、candidate Diff 可应用性和动态冲突。
三个任务全部完成后，独立只读 Finalizer 再检查合并 Diff 并重跑 `test_checkout.py`，且无权修改代码。

验证标准：Workbench 能明确显示两个依赖批次、三个任务契约、正常/异常场景矩阵、每个 Worker 的允许
范围与实际 touched files，以及 `3/3 completed → Finalizer PASS`。如果任一实现失败或越界，依赖验证器
不得启动，Finalizer 也不能把部分结果包装成通过。

## Lab 3：复杂真实任务

Lab 3 首次从 [`complex_repository`](complex_repository/) 创建 Git 仓库；之后默认复用该运行模式的
Workspace。Operator Console 会用人类可读的 Task Session 归组多次 Run，可直接打开历史会话并从最新
Checkpoint 继续，不需要查随机目录。任务不是一步改错：

- 渠道重试的 provider / event id 需要规范化，否则幂等失效。
- `39.995` 必须按业务规则舍入为 `40.00`，否则 partial capture 无法正确收口。
- 拒绝的 capture 不能提前污染业务幂等键集合或结算账本。
- focused tests 只覆盖前半部分；完整回归才检查失败原子性和可重试性。

点击绿色 Debug 后先选择一种模式：

| 模式 | 建议顺序 | 主要观察点 |
| --- | --- | --- |
| `1 自然修复` | 第一次必跑 | 不干预模型；记录它如何定位文件、选择工具、遇到失败并收敛 |
| `2 上下文压力` | 自然修复看懂后 | 同一任务降到 6500 字符预算；观察压缩、重复检索、遗漏和恢复 |
| `3 人工控制与恢复` | 最后运行 | 输入 steer、按 F6 暂停、继续、检查写参数并批准 |
| `4 全自动修复` | 先建立全局视角时 | 隔离工作区内自动批准普通写操作，一次看完检索、修改、验证与收敛；越界路径、网络和危险命令仍拒绝 |

进入 Operator Console 后，新任务点击“运行新会话”；中断过的任务从会话列表选择后点击“打开”，再按
“继续”。模式 1/2/3 的写操作会停在批准界面；确认目标和参数后再批准。模式 4 会自动批准隔离
Workspace 内的普通写操作，但不会绕过路径、命令和网络边界。运行结束后按
`Ctrl+Q` 退出，脚本会把最终 run 固定为 `complex_artifact.txt`，并自动在 Chrome 打开 Lab 3 页面。

Lab 3 的五个断点分别回答：

```text
operator_console.main               本次场景怎样装配
AgentLoop._run_turn                 一轮怎样推进
ContextWindowManager.prepare       模型实际看到什么、是否压缩
ToolExecutionPipeline._execute_call 一个工具意图怎样被治理和执行
PythonValidationTool.execute       focused/full pytest 怎样成为证据
```

每种模式运行结束后，使用本次 Trace 核对以下问题：

1. 模型第一条错误判断是什么？哪条 Observation 迫使它换方向？
2. 哪次工具调用失败了？失败来自参数、权限、代码还是环境？
3. 修改前读取了哪些事实？是否存在无效的重复 grep/read_file？
4. focused tests 和 full suite 分别证明了什么？为什么不能互相替代？
5. 如果最终没有完成，是模型能力、步数、上下文、工具、审批还是 Runtime 策略导致？
6. 下一轮若只改变一个变量，应选择什么变量，预期哪个 Trace 指标发生变化？

以上结论必须由本次 Trace 支撑，不能仅根据功能名称推断上下文压缩、HITL 或恢复已经生效。

## Workbench 阅读顺序

Workbench 始终使用同一个地址。先在“选择运行证据”中切换本次运行，再按固定顺序阅读；普通
`Harness.run`、三个预置场景和已发布评测共用这套结构，不需要记不同页面链接。

1. **运行概览：**确认任务、最终状态、关键计数和证据边界。
2. **执行过程：**先看“准备模型输入 → 模型提出意图 → Runtime 处理意图 → 结果回填”时间线；
   遇到 ToolCall 时再按“入口控制 → 执行决策 → 受限执行 → 结果与恢复”下钻。
3. **上下文与决策：**观察上一轮 Evidence 怎样改变本轮输入、工具决定与反馈；所有带 Trace 的运行都支持。
4. **结果与证据：**看 candidate、验证、恢复、编排或评测结论，只有结论不清楚时才展开底层事件。

“上下文与决策”是 Workbench 对 AgentLoop Trace 的通用投影，不是固定样例，也不会重新调用模型。
它展示实际记录的 Context 组成、可见工具、Skill、文件、模型响应摘要和 Observation；完整 Prompt 与隐藏
思维链不会被复制到页面。

Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence。Benchmark 视图默认显示当前
Canonical Showcase 状态；Golden-10 和 Infrastructure Smoke-5 只作为开发/健康证据，历史 Campaign 只在归档中复核。

三个 Lab 的原生运行只保存在 `.agent_forge/runs/showcases/<run>/`；Workbench 通过
`.agent_forge/internal/index/run.txt` 找到最新一次，不生成第二份分类视图。Lab 1 的原生状态全在
同一个 `governed-*` 目录：`human_input/`、`approvals/`、`operation_ledger/` 以及
`phases/<phase-run>/task_state/`。普通 Harness 和 Operator 的跨 Run 控制状态则位于
`.agent_forge/internal/state/`。这些都是 Runtime 直接写入的权威 JSON，不是展示副本。

## 场景验证标准

一个机制只有同时满足以下条件，才视为已完成场景验证：

- 可直接定位入口和核心 owner。
- 断点执行前能预测下一状态与关键字段。
- 能指出一次真实失败、触发原因和保护行为。
- 能从 Workbench 找到证据，而不是只复述文档。
- 备选方案、当前取舍和结论边界均有明确说明。

完成 Lab 3 所需模式并形成可复核 Evidence 后，再决定是否扩大到真实 SWE-bench Case。新增 Feature
应由现有场景无法覆盖的真实需求或故障触发。

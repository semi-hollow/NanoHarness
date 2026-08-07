# NanoHarness Debug Lab：三条主线

这是项目唯一的动态学习入口。先在 Debugger 中看状态怎样变化，再到 Workbench 复核这一次运行留下的
Evidence。不要通读仓库，也不要另记长命令。

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

| 顺序 | PyCharm 配置 | 你要掌握的主问题 | 模型 |
| --- | --- | --- | --- |
| 1 | `NanoHarness Lab 1 - Governed Repair` | 写操作如何经过审批、Checkpoint、恢复、幂等与验证 | 确定性适配器，免费 |
| 2 | `NanoHarness Lab 2 - Coordinated Agents` | 无冲突任务如何并行，怎样隔离、校验范围、合并并最终收口 | 确定性适配器，免费 |
| 3 | `NanoHarness Lab 3 - Complex Live Repair` | 真实模型怎样在多模块缺陷中检索、试错、修改、回归并收敛 | DeepSeek，产生费用 |
| - | `NanoHarness Evidence Workbench - Read Only` | 不重跑 Lab，直接打开最近一次 Lab 3 Evidence；页面内可切换其他来源 | 不调用模型，免费 |

断点按场景自动隔离：Lab 1 有 7 个、Lab 2 有 5 个、Lab 3 有 5 个。运行一个 Lab 不会停进另一个
Lab。Project 面板优先选择 `00 NanoHarness Review Path`；Lab 2/3 再切到
`05 NanoHarness Extended Flows`。

## Lab 1：受治理运行

```text
run_governed
→ Harness.run
→ AgentLoop.run
→ ToolExecutionPipeline._execute_call
→ OperationTracker / ToolAuthorizationGate
→ RunLifecycle.finalize_run
```

只观察五个对象：

1. `session`：一次 run 的消息、观察、工具历史和生命周期。
2. `tool_call`：模型提出的意图，还不代表允许执行。
3. `operation_intent`：副作用操作的 operation key 和目标 fingerprint。
4. `checkpoint`：可恢复业务状态，不是 Python 调用栈。
5. `validation_evidence`：工具真实执行的 focused pytest 结果。

学会标准：你能在审批发生前预测 `waiting_approval`，解释为什么批准绑定 operation key，说明
`executing` 状态崩溃后为何 fail closed，并在 Workbench 找到批准、恢复、实际写入和 pytest 证据。

## Lab 2：多 Agent 协同

```text
run_coordinated
→ LiveFanoutCoordinator.run
→ dependency batch
→ LocalAgentWorkerAdapter.run_worker
→ scope gate / merge
→ LocalAgentWorkerAdapter.run_finalizer
```

两个 Worker 在独立 Git worktree 修改 `pricing.py` 和 `shipping.py`。声明写入范围不够，Coordinator
还会检查实际 touched files；只有 Worker 全部成功、无范围逃逸且候选 Diff 可合并，Finalizer 才在
合并结果上运行 `test_checkout.py`。

学会标准：你能画出 `DAG → batch → worker worktree → scope gate → merge → finalizer`，解释什么
任务可以并行、什么任务必须串行，并在 Workbench 找到每个 Worker 的真实改动和最终验证。

## Lab 3：复杂真实任务

Lab 3 首次从 [`complex_repository`](complex_repository/) 创建 Git 仓库；之后默认复用该练习模式的
Workspace。Operator Console 会用人类可读的 Task Session 归组多次 Run，可直接打开历史会话并从最新
Checkpoint 继续，不需要查随机目录。任务不是一步改错：

- 渠道重试的 provider / event id 需要规范化，否则幂等失效。
- `39.995` 必须按业务规则舍入为 `40.00`，否则 partial capture 无法正确收口。
- 拒绝的 capture 不能提前污染 operation-key 集合或 ledger。
- focused tests 只覆盖前半部分；完整回归才检查失败原子性和可重试性。

点击绿色 Debug 后先选择一种模式：

| 模式 | 什么时候跑 | 你要观察什么 |
| --- | --- | --- |
| `1 自然修复` | 第一次必跑 | 不干预模型；记录它如何定位文件、选择工具、遇到失败并收敛 |
| `2 上下文压力` | 自然修复看懂后 | 同一任务降到 6500 字符预算；观察压缩、重复检索、遗漏和恢复 |
| `3 人工控制与恢复` | 最后跑 | 亲自输入 steer、按 F6 暂停、继续、检查写参数并批准 |
| `4 全自动修复` | 先建立全局视角时 | 隔离工作区内自动批准普通写操作，一次看完检索、修改、验证与收敛；越界路径、网络和危险命令仍拒绝 |

进入 Operator Console 后，新任务点击“运行新会话”；中断过的任务从会话列表选择后点击“打开”，再按
“继续”。模式 1/2/3 的写操作会停在批准界面；确认目标和参数后再批准。模式 4 会自动批准隔离
Workspace 内的普通写操作，但不会绕过路径、命令和网络边界。运行结束后按
`Ctrl+Q` 退出，脚本会把最终 run 固定为 `complex_artifact.txt`，并自动在 Chrome 打开 Lab 3 页面。

Lab 3 的五个断点分别回答：

```text
operator_console.main               这次练习怎样装配
AgentLoop._run_turn                 一轮怎样推进
ContextWindowManager.prepare       模型实际看到什么、是否压缩
ToolExecutionPipeline._execute_call 一个工具意图怎样被治理和执行
PythonValidationTool.execute       focused/full pytest 怎样成为证据
```

每跑完一种模式，先不要看答案，口头回答：

1. 模型第一条错误判断是什么？哪条 Observation 迫使它换方向？
2. 哪次工具调用失败了？失败来自参数、权限、代码还是环境？
3. 它修改前读了哪些事实？有没有无效的重复 grep/read_file？
4. focused tests 和 full suite 分别证明了什么？为什么不能互相替代？
5. 如果最终没有完成，是模型能力、步数、上下文、工具、审批还是 Runtime 策略导致？
6. 你下一次只允许改变一个变量，会改什么？预期哪个 Trace 指标变化？

六问不过，就不算学会。这六问必须用本次 Trace 回答；只记“项目有上下文压缩、HITL、恢复”不算实践经验。

## Workbench 阅读顺序

Workbench 始终使用同一个地址。先在“选择运行证据”中切换本次运行，再按固定四层阅读；普通
`Harness.run`、三个预置场景和已发布评测共用这套结构，不需要记不同页面链接。

1. **运行概览：**确认任务、最终状态、关键计数和证据边界。
2. **执行过程：**看“准备输入 → 模型决定 → 治理并执行 → 回填持久化”四个稳定阶段。
3. **上下文与决策：**观察上一轮 Evidence 怎样改变本轮输入、工具决定与反馈；所有带 Trace 的运行都支持。
4. **结果与证据：**看 candidate、验证、恢复、编排或评测结论，只有结论不清楚时才展开底层事件。

“上下文与决策”是 Workbench 对 AgentLoop Trace 的通用投影，不是固定样例，也不会重新调用模型。
它展示实际记录的 Context 组成、可见工具、Skill、文件、模型响应摘要和 Observation；完整 Prompt 与隐藏
思维链不会被复制到页面。

Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence。历史 SWE-bench campaign 仍保留在
Workbench 的“评测档案”，但它不再占用 Lab 编号，也不是首次实践前置条件。

## 统一验收

一个机制只有同时满足以下条件，才算你已经掌握：

- 30 秒内找到入口和核心 owner。
- 断点执行前能预测下一状态与关键字段。
- 能指出一次真实失败、触发原因和保护行为。
- 能从 Workbench 找到证据，而不是只复述文档。
- 能说清备选方案、当前取舍和不能声称的边界。

先跑完 Lab 3 的三种模式，再决定是否扩大到真实 SWE-bench Case。不要在没有形成运行直觉前继续增加
Feature 或阅读材料。

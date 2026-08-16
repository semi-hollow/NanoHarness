# NanoHarness 可复现调试场

本目录提供两项确定性 Lab 和一套只读 Evidence Workbench。它们分别覆盖持久化控制面、
Coordinated Agents；真实 repository repair 则直接使用已经发布的 SWE-bench Verified Mini-50
证据，不再维护一套功能重叠的第三个样例。

机制与源码入口见[核心运行机制与代码索引](../../docs/核心运行机制与代码索引.md)。Debugger
用于观察动态因果，Workbench 用于复核 Runtime 已经写下的权威 Evidence；二者不生成第二份
展示数据。

## 快速开始

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

仓库提供以下 PyCharm Run Configuration：

| 配置 | 回答的问题 | 模型 |
| --- | --- | --- |
| `NanoHarness Lab 1 - Governed Repair` | Human Input、Approval、Pause、Resume、Cancel 如何形成可恢复状态链 | 确定性适配器，免费 |
| `NanoHarness Lab 2 - Coordinated Agents` | 依赖任务如何并行、隔离、合并，并由只读 Finalizer 验证 | 确定性适配器，免费 |
| `NanoHarness Evidence Workbench - Read Only` | 如何读取运行证据，并复核版本化实验的变量、结果、Case 转移与证据边界 | 不调用模型，免费 |
| `NanoHarness Benchmark - SWE-bench Verified Mini 50` | 当前质量配置在 50 个真实 Case 上的 Pass@1 | 正式模型与 Official evaluator |

如需重装 PyCharm 阅读 Scope 与按 symbol 定位的断点，关闭 PyCharm 后执行：

```bash
.venv/bin/python scripts/install_pycharm_debug_lab.py
```

Lab 1 有 7 个断点，Lab 2 有 5 个断点；条件均绑定 `NANOHARNESS_DEBUG_LAB`，运行一项
Lab 不会停进另一项。Project 面板优先使用 `00 NanoHarness Review Path`，需要查看多 Agent
实现时切到 `05 NanoHarness Extended Flows`。

## Run 命名与证据定位

新 Run 使用统一的人类可读格式：

```text
<业务标签>__YYYY-MM-DD_HH-MM-SS__<短ID>
```

例如：

```text
lab1-governed-change-control__2026-08-16_14-30-25__a1b2c3d
lab2-checkout-policy-agents__2026-08-16_14-42-10__d4e5f6a
swebench-django__django-11451__2026-08-16_15-05-30__91ac240
```

业务标签让人立即知道内容，时间用于现场定位最近运行，短 ID 只负责同秒防冲突。运行目录是
权威存储对象，不是软链接或二次投影：

```text
.agent_forge/runs/showcases/<human-readable-run>/
.agent_forge/runs/benchmarks/<campaign>/<human-readable-run>/
```

Workbench 的三级选择与磁盘身份一一对应：

```text
证据类型（Lab 1 / Lab 2 / Mini-50）
└── 不可变 Run（人类可读目录名）
    └── Case / Worker（整体、单个 Worker 或单个 SWE-bench Case）
```

## Lab 1：持久化控制面

```text
GovernedShowcaseConsoleApp
→ HumanInput Repository
→ Checkpoint
→ explicit Resume
→ Operation Intent / Approval Repository
→ explicit Resume | Pause | Cancel
→ ToolExecutionPipeline
→ focused validation
→ RunLifecycle
```

PyCharm 会打开按钮式 TUI。每个动作都刻意与 continuation 分开，以便在两个动作之间查看真实
JSON，而不是点击一次就直接跳到终态：

1. 点击“开始受治理任务”。Runtime 创建问题并停在 `waiting_human`。
2. TUI 的“权威文件”面板列出 HumanInput 与 Checkpoint 的短路径；需要精确绝对路径时打开 `showcase.json`。
3. 输入一条具体变更要求并点击“仅保存输入”。此动作**只保存回答**，不会自动 Resume，也不会修改 workspace。
4. 对比 HumanInput JSON 的 `status`、`answer`、`updated_at`，再点“显式 Resume”。
5. Runtime 生成补丁意图并停在 `waiting_approval`；此时目标文件仍为 `NO_OPERATOR_REQUEST`。
6. 点击“仅保存批准”或“仅保存拒绝”，检查 Approval JSON；真实工具仍未执行。
7. 可选择“Pause 到安全边界”观察 paused Checkpoint，或“Cancel 任务”观察 cancelled 终态；
   也可点“显式 Resume”继续消费原决定。
8. 批准并 Resume 后，Operation Ledger、workspace、Trace 与 focused pytest 证据依次提交。

一次 Lab 1 的权威目录形状如下：

```text
.agent_forge/runs/showcases/<lab1-run>/
├── showcase.json                 # 本次步骤导航清单
├── showcase.md                   # 同一事实的可读摘要
├── human_input/<request-id>.json # pending → responded
├── approvals/<operation-key>.json
├── operation_ledger/<operation-key>.json
├── phases/<phase-run>/
│   ├── task_state/<run-id>.json  # waiting / paused / cancelled / completed
│   ├── trace.json
│   └── usage.json
└── workspace/operator_request.txt
```

重点不是记住随机 ID，而是先用 TUI 的短路径识别对象，需要精确定位时再查 `showcase.json`。
HumanInput、Approval、Operation Ledger、TaskState 和 Trace 才是各自 owner 写入的权威状态。

验证标准：能够观察 `waiting_human → waiting_approval → completed`，也能单独复现
`paused` 与 `cancelled`；审批前文件不变，批准并 Resume 后才出现写入与验证证据。

## Lab 2：协同智能体

```text
LiveFanoutCoordinator.run
→ pricing-policy + shipping-policy（并行）
→ scoped merge
→ edge-case-verifier（依赖前两项）
→ read-only Finalizer
```

两个实现 Worker 在独立 Git worktree 修改互不重叠的文件。依赖验证器只有在两份 Diff 都通过
写入范围与可应用性检查后才启动，最终只读 Finalizer 再验证合并状态。测试矩阵刻意包含异常分支：

- 负数小计、负折扣和超额折扣必须 fail closed。
- 普通订单达到阈值后免运费，加急订单不能被该规则误伤。
- 未知区域必须明确拒绝，不能静默返回零费用。

验证标准：Workbench 显示两个依赖批次、三个 Worker、各自 touched files、合并结果与
`Finalizer PASS`；任一任务失败或越界时，后续依赖任务不得被包装成成功。

## Mini-50：真实 Repository Repair

Mini-50 取代旧的第三个样例。它保留真实 SWE-bench Case 输入、Agent Trace、candidate patch、
Official evaluator 与完整分母。在 Workbench 依次选择：

```text
Mini-50 · 真实仓库能力评测
→ 某次不可变 Run
→ 50 Case 总览 / <具体 instance_id>
```

选择某个 Case 后，“执行过程”与“上下文与决策”用于定位重复 ToolCall、失败 Observation、
上下文变化和停止原因；“结果与证据”用于区分 Resolved、Unresolved、Empty Patch 与基础设施状态。

## Workbench 阅读顺序

Workbench 顶部有两个只读模式，共用同一个服务和同一套版本化资产：

- **运行证据：**证据类型 → 不可变 Run → Case/Worker，再读取运行概览、执行过程、上下文与决策、结果与证据。
- **实验对比：**实验方向 → 轮次/测量 → 概览、变量、结果、具体 Case 或证据边界。

运行证据模式固定使用三级选择和四个视图：

1. **证据类型 → 不可变 Run → Case/Worker：**先锁定唯一存储对象，不让 latest 覆盖身份。
2. **运行概览：**确认任务、状态、关键计数和证据边界。
3. **执行过程：**查看模型输入准备、意图、Runtime 处理和 Observation 回填。
4. **上下文与决策：**查看可审计输入与显式 ToolCall，不声称展示隐藏思维链。
5. **结果与证据：**检查持久化控制、Patch、验证、Official 与失败分类。

Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence。只有 Runtime 真实写入的文件才
进入运行视图；实验视图直接读取 `benchmarks/experiments/*/experiment.json` 声明的冻结计划、机器结果、
execution index 与 provenance。页面本身只读，也不会为了展示重跑 Agent、补造历史数据或把解释层冒充原始测量。

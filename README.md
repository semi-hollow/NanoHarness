# NanoHarness

[![NanoHarness CI](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml/badge.svg)](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**一个可治理、可恢复，并以运行证据为核心的 Coding Agent Runtime。**

NanoHarness 在隔离的真实代码仓库中驱动模型检索、编辑和验证代码。它不只交付一个
final answer，还把上下文、工具、权限、人工控制、恢复状态、成本和评测结论收敛为可检查的
Run Evidence。

```text
repository task
  -> isolated workspace
  -> governed AgentLoop
  -> candidate diff + validation
  -> trace + checkpoint + usage
  -> evaluation + failure diagnosis
```

## 项目解决什么

模型会写代码，不等于它能稳定完成长周期工程任务。真实运行中还需要回答：

- 模型这一轮应该看到哪些 Context 和 Tool？
- 写操作、命令和越界路径如何被确定性约束？
- 审批、进程中断或目标文件漂移后，状态变更操作能否避免重复执行并安全恢复？
- 多个 Agent 什么时候可以并行，最终由谁检查合并后的整体结果？
- 生成了 Diff、本地测试通过和官方解决之间，证据边界在哪里？

NanoHarness 把这些问题放进 Runtime 控制面，而不是交给 Prompt 或模型自我声明。

## 阅读入口

项目文档保留一份系统概览和两份代码索引。首次阅读从“系统概览与核心设计”开始；需要核对具体运行规则
或定位源码时，再进入对应索引。其余页面都是运行入口或机器证据，不维护平行说明文档。

| 当前问题 | 唯一入口 |
| --- | --- |
| 从定位、功能、生命周期到评测，了解整体设计 | [系统概览与核心设计](docs/系统概览与核心设计.md) |
| 已知能力名称，直接进入核心 Owner | [核心能力与代码入口](docs/核心能力与代码入口.md) |
| 按触发时机理解规则、失败和恢复行为 | [核心运行机制与代码索引](docs/核心运行机制与代码索引.md) |
| 动手跑断点和查 Evidence | [Debug Lab](examples/debug_lab/README.md) |
| 查当前质量结果、证据边界与下一轮确认实验 | [Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json) |

## 当前质量结果

NanoHarness 当前对外使用一个简单、可解释的工程口径：**单 Agent 在固定 SWE-bench Verified
开发样本上的 Pass@1 official resolved 约为 40%（4/10）**。10 个 planned Case 全部保留在
分母中；其中 4 个由 official evaluator 判定 resolved。这个数字用于说明系统已经具备端到端解决
真实仓库任务的基础能力，不冒充完整 500 题排行榜成绩，也不证明 Harness 相对底座模型的独立增益。

公开说明优先展示数字背后的完整链路：真实 issue 与 base commit、隔离 worktree、受治理 AgentLoop、
candidate Patch、本地验证、official evaluator、Trace、Usage 与 Workbench。下一轮确认实验会在冻结的
100-Case、按仓库分层样本上使用质量优先配置复测；在它完成前不把 `4/10` 改写成虚构的 `40/100`。

当前结果、证据来源和声明边界见 [Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json)。
早期低预算、模型选型尝试、被拒绝 Treatment 和旧 Campaign 已退出当前项目说明；原始演进仍可从
Git 历史恢复，但不进入 README 阅读路径。

## 证据与可复现场景

Workbench 只读真实 Runtime Event 与已保存的评测制品，把运行概览、执行过程、上下文与决策、
结果与证据投影成可查阅页面；它不伪造隐藏思维链，也不用另一个模型重写事实。

三个 Debug Lab 分别复现按钮式 HITL/审批与恢复、含异常分支的多 Agent 依赖协作、真实模型复杂修复。运行方式、断点和
Workbench 阅读顺序只在 [Debug Lab](examples/debug_lab/README.md) 说明；结果、分母和声明边界由
[Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json)统一提供。

## 快速体验

环境要求为 Python 3.11。安装依赖后，可以打开实时操作台或只读 Workbench：

```bash
python -m pip install -e '.[dev]'
forge console
forge ui
```

具体 PyCharm 配置和运行顺序见 [Debug Lab](examples/debug_lab/README.md)。外部项目可通过
[`Harness`](agent_forge/harness.py) facade 注入自己的 Model 和 ToolGateway。

需要建立整体认识时使用[系统概览与核心设计](docs/系统概览与核心设计.md)；需要从能力名称进入源码时查
[核心能力与代码入口](docs/核心能力与代码入口.md)；需要解释运行行为和边界时查
[核心运行机制与代码索引](docs/核心运行机制与代码索引.md)。

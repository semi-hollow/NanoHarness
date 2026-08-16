# NanoHarness

[![NanoHarness CI](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml/badge.svg)](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**一个可治理、可恢复，并以运行证据为核心的 Coding Agent Runtime。**

NanoHarness 在隔离的真实代码仓库中驱动模型检索、编辑和验证代码。它不只返回本次停止输出；
只有被接受的完成才发布 final answer，同时把上下文、工具、权限、人工控制、恢复状态、成本和评测结论收敛为
可检查的 Run Evidence。

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

项目文档按架构导览、系统概览、上下文工程、工具治理、运行机制、源码入口和持久化契约分工。全局源码定位只由
“核心能力与代码入口”维护；其余页面不复制第二份全局类索引。

| 使用场景 | 唯一入口 |
| --- | --- |
| 第一次建立 System、Context、Governance、Durability 与 Evaluation 主链 | [架构导览](docs/架构导览.md) |
| 评审系统定位、模型输入、Turn 分流、生命周期、核心取舍和架构演进 | [系统概览与核心设计](docs/系统概览与核心设计.md) |
| 沿模型输入主线理解 Runtime Context、History、WorkingMemory 与压缩 | [上下文工程](docs/上下文工程.md) |
| 沿 ToolCall 主线理解路由、授权、Ledger、执行与 Observation | [工具治理与执行](docs/工具治理与执行.md) |
| 核对跨能力的触发条件、异常分支和恢复规则 | [核心运行机制与代码索引](docs/核心运行机制与代码索引.md) |
| 已知能力名称，直接进入首个核心 Owner | [核心能力与代码入口](docs/核心能力与代码入口.md) |
| 查持久化文件的形式、基数、写入时机和恢复权威 | [运行产物与持久化契约](docs/运行产物与持久化契约.md) |
| 动手跑断点和查 Evidence | [Debug Lab](examples/debug_lab/README.md) |
| 查当前质量结果、证据边界与下一轮确认实验 | [Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json) |
| 查历次实验、结果、回滚与证据恢复点 | [实验总览](benchmarks/experiments/README.md) |

## 当前质量结果

NanoHarness 当前对外使用一个简单、可解释的工程口径：**单 Agent 在固定 SWE-bench Verified
Mini-50 上的 Pass@1 official resolved 为 28/50（56%）**。50 个 Case 全部保留在分母中：
28 resolved、16 official unresolved、6 Agent terminal Empty Patch。这个数字用于说明系统已经具备
端到端解决真实仓库任务的基础能力，不冒充完整 500 题排行榜成绩，也不证明 Harness 相对底座模型的独立增益。

公开说明优先展示数字背后的完整链路：真实 issue 与 base commit、隔离 worktree、受治理 AgentLoop、
candidate Patch、本地验证、official evaluator、Trace、Usage 与 Workbench。原始 Mini-50 的
`23/50` 因基础设施发布门被拒绝；只对预先分类的 provider/外部中断槽位补全后，最终 50/50 均有
可归因终态并通过发布门。完整方法与边界见
[Mini-50 实验报告](benchmarks/experiments/mini50-v1-deepseek-v4-flash/report.md)。

当前结果、证据来源和声明边界见 [Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json)。
早期低预算、模型选型尝试、被拒绝 Treatment 和旧 Campaign 已退出当前项目说明；原始演进仍可从
Git 历史恢复，但不进入 README 阅读路径。

## 证据与可复现场景

Workbench 只读真实 Runtime Event 与已保存的评测制品。运行证据模式把运行概览、执行过程、
上下文与决策、结果与证据投影成可查阅页面；实验对比模式按实验方向、轮次和 Case 读取变量、
结果转移、过程指标与 provenance。两种模式都不伪造隐藏思维链，也不用另一个模型重写事实。

两个 Debug Lab 分别复现按钮式 HITL/审批与恢复、含异常分支的多 Agent 依赖协作；真实模型复杂修复直接读取
Mini-50 Case。运行方式、断点和 Workbench 阅读顺序只在 [Debug Lab](examples/debug_lab/README.md) 说明；结果、分母和声明边界由
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

需要快速建立主链时先看[架构导览](docs/架构导览.md)，需要进一步理解功能地图、核心取舍与架构演进时使用
[系统概览与核心设计](docs/系统概览与核心设计.md)；模型输入下钻见
[上下文工程](docs/上下文工程.md)，工具执行下钻见[工具治理与执行](docs/工具治理与执行.md)。需要从能力名称进入源码时查
[核心能力与代码入口](docs/核心能力与代码入口.md)；需要解释跨能力规则时查[核心运行机制与代码索引](docs/核心运行机制与代码索引.md)，
需要核对落盘事实时查[运行产物与持久化契约](docs/运行产物与持久化契约.md)。

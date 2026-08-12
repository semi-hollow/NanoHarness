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

常用入口只有两份：按能力名称找代码使用“核心能力与代码入口”，按触发时机查运行规则使用
“核心运行机制与代码索引”。生命周期、架构和证据文档只在需要完整状态或边界时打开。

| 当前问题 | 唯一入口 |
| --- | --- |
| 已知能力名称，直接进入核心 Owner | [核心能力与代码入口](docs/核心能力与代码入口.md) |
| 按触发时机理解规则、失败和恢复行为 | [核心运行机制与代码索引](docs/核心运行机制与代码索引.md) |
| 串起完整生命周期、状态和异常恢复 | [运行生命周期与异常处理机制](docs/运行生命周期与异常处理机制.md) |
| 找代码、依赖和状态 owner | [项目架构与代码导航](docs/项目架构与代码导航.md) |
| 核对能力是否实现以及不能声称什么 | [能力实现状态与使用边界](docs/能力实现状态与使用边界.md) |
| 动手跑断点和查 Evidence | [Debug Lab](examples/debug_lab/README.md) |
| 查当前质量结果、证据边界与下一轮确认实验 | [Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json) |
| 查评测分层、运行方法和声明边界 | [回归测试与评测范围](docs/evaluation/回归测试与评测范围.md) |
| 查真实失败、根因和回归证据 | [典型故障与系统调优记录](docs/evaluation/典型故障与系统调优记录.md) |

历史背景按需查[功能演进与设计取舍](docs/功能演进与设计取舍.md)和
[代码结构演进与可读性治理](docs/architecture/代码结构演进与可读性治理.md)。

## 当前质量结果

NanoHarness 当前对外使用一个简单、可解释的工程口径：**单 Agent 在固定 SWE-bench Verified
开发样本上的 Pass@1 official resolved 约为 40%（4/10）**。10 个 planned Case 全部保留在
分母中；其中 4 个由 official evaluator 判定 resolved。这个数字用于说明系统已经具备端到端解决
真实仓库任务的基础能力，不冒充完整 500 题排行榜成绩，也不证明 Harness 相对底座模型的独立增益。

公开说明优先展示数字背后的完整链路：真实 issue 与 base commit、隔离 worktree、受治理 AgentLoop、
candidate Patch、本地验证、official evaluator、Trace、Usage 与 Workbench。下一轮确认实验会在冻结的
100-Case、按仓库分层样本上使用质量优先配置复测；在它完成前不把 `4/10` 改写成虚构的 `40/100`。

当前结果、证据来源和声明边界见 [Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json)。
早期低预算、模型选型尝试、被拒绝 Treatment 和旧 Campaign 已退出主动展示，仅在被追问实验纪律时
从[评测历史归档](benchmarks/archive/README.md)按需查阅。

## 证据与可复现场景

Workbench 只读真实 Runtime Event 与已保存的评测制品，把运行概览、执行过程、上下文与决策、
结果与证据投影成可查阅页面；它不伪造隐藏思维链，也不用另一个模型重写事实。

三个 Debug Lab 分别复现审批与恢复、多 Agent 隔离合并、真实模型复杂修复。运行方式、断点和
Workbench 阅读顺序只在 [Debug Lab](examples/debug_lab/README.md) 说明；评测结论的证据边界只在
[回归测试与评测范围](docs/evaluation/回归测试与评测范围.md) 说明。

## 快速体验

环境要求为 Python 3.11。安装依赖后，可以打开实时操作台或只读 Workbench：

```bash
python -m pip install -e '.[dev]'
forge console
forge ui
```

具体 PyCharm 配置和运行顺序见 [Debug Lab](examples/debug_lab/README.md)。外部项目可通过
[`Harness`](agent_forge/harness.py) facade 注入自己的 Model 和 ToolGateway。

完整能力边界统一查[能力实现状态与使用边界](docs/能力实现状态与使用边界.md)，README 不复制第二份边界清单。

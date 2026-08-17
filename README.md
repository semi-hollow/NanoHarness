# NanoHarness

[![NanoHarness CI](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml/badge.svg)](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

**一个可治理、可恢复，并以运行证据为核心的 Coding Agent Runtime。**

NanoHarness 在隔离的真实代码仓库中驱动模型检索、编辑和验证代码。模型负责提出行动，
Runtime 负责上下文、工具授权、持久化控制和停止条件；运行过程最终收敛为可检查的
Trace、Checkpoint、Operation Ledger、Usage 与 Evaluation Evidence。

```text
repository task
  → isolated workspace
  → governed AgentLoop
  → candidate diff + validation
  → trace + checkpoint + usage
  → evaluation + failure diagnosis
```

## 为什么需要它

模型会写代码，不等于它能稳定完成长周期工程任务。真实运行还需要回答：

- 每一 Turn 应该看到哪些 Context 和 Tool？
- 写操作、命令和越界路径如何被确定性约束？
- 审批、人工输入、进程中断或目标漂移后，副作用能否避免重复并安全恢复？
- 多个 Agent 何时可以并行，谁负责合并后的整体检查？
- 生成 Diff、本地测试通过和 official resolved 之间，证据边界在哪里？

NanoHarness 把这些问题放进 Runtime 控制面，而不是交给 Prompt 或模型自我声明。

## 快速开始

环境要求为 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
forge console
forge ui
```

PyCharm 配置、两个确定性 Lab 和证据路径见 [Debug Lab](examples/debug_lab/README.md)。
外部项目可通过 [`Harness`](agent_forge/harness.py) facade 注入自己的 Model 和 ToolGateway。

## 架构与源码

[架构导览](docs/架构导览.md) 用五个章节建立主链；需要下钻时再进入唯一 owner 文档：

| 问题 | 入口 |
| --- | --- |
| Runtime 全局主链 | [架构导览](docs/架构导览.md) |
| 模型每轮看到什么 | [上下文工程](docs/上下文工程.md) |
| ToolCall 如何治理和执行 | [工具治理与执行](docs/工具治理与执行.md) |
| 多个 Agent 如何隔离、并发和稳定集成 | [多 Agent 编排](docs/多Agent编排.md) |
| 哪个能力由哪段核心代码拥有 | [核心能力与代码入口](docs/核心能力与代码入口.md) |
| 哪个 JSON 是恢复权威 | [运行产物与持久化契约](docs/运行产物与持久化契约.md) |

## 只读证据审阅

先运行 `forge ui`，再按同一条 Review Path 查看三类真实制品：

1. [Lab 1 · Durable Control](http://127.0.0.1:8765/?source=governed&view=overview)：
   状态链为 HumanInput → Resume → Approval → Ledger → side effect → Validation。
2. [Lab 2 · Agent Coordination](http://127.0.0.1:8765/?source=orchestration&view=overview)：
   DAG、并发批次、隔离 worktree、三道冲突门和只读 Finalizer。
3. [Mini-50 · Real Repository Capability](http://127.0.0.1:8765/?source=evaluation&view=overview)：
   固定 50 个 Case、发布漏斗、代表 Case 和 evaluated revision provenance。

Workbench 只投影本机 `.agent_forge/` 中的真实 Runtime 或 Evaluation artifact；它不执行操作，
也不把设计契约伪装成观测事实。版本控制只保存审阅 manifest 与 provenance，缺少对应 raw evidence
时 preflight 会 fail closed。

```bash
.venv/bin/python scripts/review_preflight.py
```

## 已评测结果与边界

固定 SWE-bench Verified Mini-50 的已发布结果是 **28/50 official resolved（56%）**：

```text
28 resolved + 16 official unresolved + 6 Agent terminal Empty Patch = 50
```

初始运行得到 23 resolved、12 unresolved、5 empty patch 和 10 个基础设施无效槽位，未通过发布门；
只补全预先分类的 provider / external interruption 槽位后，61 次总 launch 收敛为 50 条可归因终态，
没有重跑 correctness-terminal Case。该结论属于 evaluated revision
`3ec537113a26491b7b7a51e323a3d3af40f4754f`，不能自动继承给后续 HEAD。

Mini-50 是固定样本，不是完整 500 题排行榜；Golden-20 是反复使用的开发集，不是 holdout；
该实验也不隔离 Harness 相对底座模型的单因素增益。完整方法与结果见
[Mini-50 实验报告](benchmarks/experiments/mini50-v1-deepseek-v4-flash/report.md)，当前声明边界见
[Quality Showcase](benchmarks/showcase/canonical-showcase-v1.json)，历次实验入口见
[实验总览](benchmarks/experiments/README.md)。

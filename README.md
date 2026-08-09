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

运行机制总览由第一项负责；其余文档按具体问题提供代码导航、能力边界和证据索引。

| 当前问题 | 唯一入口 |
| --- | --- |
| 先理解整体机制、异常和恢复 | [运行生命周期与异常处理机制](docs/运行生命周期与异常处理机制.md) |
| 找代码、依赖和状态 owner | [项目架构与代码导航](docs/项目架构与代码导航.md) |
| 核对能力是否实现以及不能声称什么 | [能力实现状态与使用边界](docs/能力实现状态与使用边界.md) |
| 动手跑断点和查 Evidence | [Debug Lab](examples/debug_lab/README.md) |
| 查评测证据范围 | [回归测试与评测范围](docs/evaluation/回归测试与评测范围.md) |
| 查真实失败、根因和回归证据 | [典型故障与系统调优记录](docs/evaluation/典型故障与系统调优记录.md) |

历史背景按需查[功能演进与设计取舍](docs/功能演进与设计取舍.md)和
[代码结构演进与可读性治理](docs/architecture/代码结构演进与可读性治理.md)。

## 固定样本实测

2026-08-08 使用 `deepseek-v4-flash` 在预注册的 SWE-bench Verified 50 题分片上，分别运行
`minimal-control` 与 `governed-runtime`，共完成 100 个运行槽位。两套配置使用相同 AgentLoop、
模型、任务、预算、安全和执行环境；治理版同时启用 task-aware Tool Routing 与 Skills，因此这是
Runtime preset 对比，不是单因素消融。

原始运行绑定 clean revision `34cbe91`；运行后的审计只修正统计口径和展示，不改写单 Case
scorecard。

| 配置 | Official resolved / 50 | Candidate patch | 失败工具调用率 | Token | 实际执行成本 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Minimal Control | 20/50（40.0%） | 32/50 | 36/993（3.63%） | 8,308,931 | $1.242115 |
| Governed Runtime | 14/50（28.0%） | 27/50 | 14/973（1.44%） | 8,078,883 | $1.124990 |

治理配置减少了约 60.3% 的失败工具调用率和 2.8% 的 Token，但降低了候选改动覆盖和当前样本
correctness。项目因此**没有**把它包装成全面提升，而是拒绝整套采纳，并将 Tool Routing 与 Skills
拆开定位。50 题每种配置只运行一次；其中一组治理版在一次重试后仍发生 provider 基础设施错误，
所以配对裁决为 49 组（Minimal 6 胜、Governed 1 胜、42 平）。

表中的 Governed 是被拒绝的 v1 preset，不代表当前冻结实现。后续开发集上，精简为单一 Skill、
canonical Tool schema 和零工具终态后，7 个高差异 Case 从 v1 的 1/7 恢复到 v3 的 4/7；更复杂的
v4/v5 又退化到 2/7，因此已回滚。该 7 题集合参与过调试，只能证明故障修复方向，不能作为盲测
解决率。当前代码另有 405/405 项自动化行为回归通过；这衡量 Runtime 契约，不替代 SWE-bench。

[查看脱敏证据包](benchmarks/campaigns/swebench-verified-100-v1-a-flash-20260808/README.md) ·
[查看典型故障与系统调优记录](docs/evaluation/典型故障与系统调优记录.md)

## 证据与可复现场景

Workbench 只读真实 Runtime Event，把运行概览、执行过程、上下文与决策、结果与证据
投影成可查阅页面；它不伪造隐藏思维链，也不用另一个模型重写事实。

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

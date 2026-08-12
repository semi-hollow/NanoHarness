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
| 查评测证据范围 | [回归测试与评测范围](docs/evaluation/回归测试与评测范围.md) |
| 查功能冻结后的质量实验与结果 | [Runtime 质量实验](docs/evaluation/功能冻结后的Runtime质量实验.md) |
| 查真实失败、根因和回归证据 | [典型故障与系统调优记录](docs/evaluation/典型故障与系统调优记录.md) |

历史背景按需查[功能演进与设计取舍](docs/功能演进与设计取舍.md)和
[代码结构演进与可读性治理](docs/architecture/代码结构演进与可读性治理.md)。

## 量化方法与边界

NanoHarness 把“机制是否存在”和“Runtime 质量如何”分开验证：前者由 Debug Lab
和行为回归验收，后者使用预注册 SWE-bench Case、单因素配对实验和 official
evaluator。

功能冻结后的证据分成两阶段。Phase 1 在固定 DeepSeek Golden-10 上建立 `4/10 planned`
official resolved 的正式 R0；R1-R3 虽出现局部正向或机制命中，却触发语义/correctness gate，
因此全部拒绝并回滚，旧 `8/10` 只是探索期 candidate Patch 覆盖，从未被采纳为解决率提升。

Phase 2 更换为 `opencode-go / glm-5.2` 后重新建立独立基线，不与 Phase 1 做严格 A/B。一个
Operation Ledger 的 68 行通用候选，只在同 Run、完整 fingerprint 证明目标被逆操作恢复到
precondition 时允许一次重放：历史 Pytest 8399 `0/2` official unresolved 转为 fresh Treatment
`2/2` resolved，机制标记 `2/2`；三个冻结 Guard 保持 `3/3` resolved 且标记为 0。这个结果是
post-hoc selected、Case-level 的正向复现，本地 validation 仍失败，不能外推为 SWE-bench 总体
uplift。唯一 Golden-10 扩跑随后只有 `4 resolved / 5 unresolved / 1 empty`，低于 Phase-2 R0 的
`5/10`，原五个 resolved 只保留 `3/5`；另有 1 次 Provider transport timeout 使 comparison
protocol-invalid。即使忽略该 infra，净退化和两个锚点回归也分别触发拒绝。因此该通用 Treatment
未被采纳，并已由可审计 revert commit `042846a` 回滚；当前 Goal/Gate 4 未完成。窄 Case 证据保留，
但不能称为 Golden non-regression。
完整协议、Golden-10 单次扩跑、负结果和声明边界见
[Runtime 质量实验](docs/evaluation/功能冻结后的Runtime质量实验.md)，机器可读摘要见
[Golden-10 证据](benchmarks/runtime-quality/golden-10-v1.json)。

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

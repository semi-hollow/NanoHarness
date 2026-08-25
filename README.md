# NanoHarness

[![NanoHarness CI](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml/badge.svg)](https://github.com/semi-hollow/NanoHarness/actions/workflows/agent-forge-ci.yml)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

**面向真实代码仓库的可治理 Coding Agent Harness。**

NanoHarness 将持久对话、增量上下文、受控工具执行、多 Agent 协作和运行证据组织在
同一套工作流中。模型提出动作；Runtime 管理身份、权限、副作用、恢复和事实落盘。

```text
ConversationThread → Turn → Run → Model Step → Tool Transaction → Durable Evidence
```

## 核心能力

| 能力 | Runtime 负责什么 | 形成的结果 |
|---|---|---|
| **Durable Conversation** | 用 `ConversationThread` 保存用户对话，以 `Turn` 表示一次顶层请求，以 `Run` 表示一次执行尝试 | 可继续的完整对话与清晰身份链 |
| **Incremental Context & Memory** | 冻结 Turn 稳定输入，按预算投影原始对话、摘要、动态仓库证据和工具契约 | `PreparedModelStep`、Conversation Digest、Working Memory |
| **Governed Tool Execution** | 将模型 ToolCall 统一经过路由、授权、Approval、Hook、Ledger 和执行门禁 | 唯一 Observation、副作用事实、可恢复 cursor |
| **Multi-Agent Coordination** | Ultra 先生成受校验 Single/Multi 决策；Multi 使用统一 HARD/LIVE readiness scheduler、隔离 Worker 和 candidate gates | Worker 交付物、集成结果、协调证据 |
| **Evaluation & Workbench** | 统一读取 Trace、Usage、Patch、Validation 与冻结实验资产 | 可筛选、可比较的运行和评测视图 |

## 快速开始

需要 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

无需在线模型即可生成一条确定性运行记录，并在浏览器中查看：

```bash
forge demo --scenario governed
forge ui
```

连接模型后，可用交互式 Console 运行仓库任务：

```bash
forge console --workspace /path/to/repository
```

需要可重复执行时，使用版本化配置：

```bash
forge run \
  --config /path/to/run.yaml \
  --workspace /path/to/repository \
  "Implement the requested change and run focused validation"
```

Provider、Context、Tool、Approval、Execution Environment 和 Agent Mode 均可由配置管理；
显式 CLI 参数覆盖配置值。

Agent Mode 只有两种公开策略：`single` 直接进入 canonical AgentLoop；
`ultra` 强制先 Planning，再自动选择同一 Single path 或受治理 Multi-Agent。

```bash
forge run --agent-mode single "Fix the focused issue"
forge run --agent-mode ultra "Implement the repository-wide change"
```

## 运行生命周期

```text
ConversationThread                         durable user conversation
└── Turn                                  one top-level user request
    ├── Run 1                             execution attempt
    └── Run 2 (resume)                    same Turn, new attempt
        └── Model Step 1..N               one model request per step
```

- 普通后续消息：相同 `thread_id`，新 `turn_id`，新 `run_id`；
- 恢复未完成工作：相同 `thread_id` 和 `turn_id`，新 `run_id`；
- 同一用户 Thread 同时最多一个 active Turn；
- `conversation.jsonl` 是带 sequence、稳定 item identity 和 hash chain 的对话事实；
- `trace.jsonl` 是执行证据，不是原始对话权威存储。

`StableTurnContextSnapshot` 在 Turn 创建后首次准备时冻结 Prompt profile、项目指令、Skill、
Long-Term Memory recall 与基础 Tool schemas；同一 Turn 的 Resume 复用该快照。当前 Runtime
若与快照契约不兼容会 fail closed，新 Turn 才重新发现稳定输入。

`TaskCheckpoint` v4 只保存一次 Run 的执行恢复事实、context revision 和未完成工具批次
cursor，不复制 root task 或完整 Conversation。

## 模型输入与增量上下文

```text
ConversationThread raw items
+ ConversationHistoryDigest
+ StableTurnContextSnapshot stable prefix
+ current repository / policy facts
+ routed Tool schemas
        ↓
ModelStepPreparation.prepare_model_step()
        ↓
PreparedModelStep
        ↓
ModelPort.chat(...)
```

`PromptWindowManager` 只压缩模型输入投影：原始 Thread journal 不会因窗口收缩而被删除。
滚动摘要只合并尚未覆盖的对话前缀，并保留最近 raw tail；assistant ToolCall batch 与对应
Observation 在窗口切分和 Provider 投影中保持协议完整。

核心入口：[`model_step_preparation.py`](agent_forge/runtime/application/model_step_preparation.py)、
[`compaction.py`](agent_forge/context/application/compaction.py) 和
[`working_memory.py`](agent_forge/runtime/application/working_memory.py)。

## 受控工具执行

模型响应中的完整 assistant content 与 ToolCall batch 会先写入 Thread，再逐项执行：

```text
assistant batch durable append
→ pending batch cursor
→ Routing / Guardrail
→ Authorization（ALLOW / ASK / DENY）
→ Approval / Operation Ledger
→ ToolGateway.execute()
→ durable Tool Observation
→ cursor advances
```

进程中断后，Resume 从原 assistant item 和 cursor 继续，不要求模型重新生成相同 ToolCall。
副作用由 operation identity/fingerprint 防止重复执行；每个 ToolCall 最终只能对应一条合法
Observation。`ask_human` 与 Approval 是可恢复屏障，不是临时终端交互。

完整设计见 [`运行治理与工具执行`](docs/运行治理与工具执行.md)。

## 多 Agent 协作

```text
Natural-language Task
→ AdaptivePlanner
→ deeply frozen FanoutPlan
→ readiness-driven Scheduler
→ HARD dependencies / LIVE routes
→ isolated Worker Attempts
→ Strict Integration Frontier
→ read-only Finalizer
```

Worker 和 Finalizer 复用 canonical `AgentLoop`，但各自拥有私有 durable
`ConversationThread`、执行目录和上下文；内部 raw token/tool history 不写入用户主 Thread。
Coordinator 是唯一 execution authority，拥有调度、Candidate gates 与确定性集成，
不拥有 AgentLoop conversation。

- HARD dependency 等待可信代码集成后再启动消费者；
- LIVE route 通过 READY / FEEDBACK / UPDATE 交换有界语义证据，只放松启动门；
- Worker 在独立 Git worktree 中执行，并受写入范围与工具集合约束；
- Worker Attempt 的 `candidate_produced` 不等于 Task `integrated`；
- Candidate 经过本地完整性/范围检查，再由 strict frontier、HARD/LIVE readiness、freshness 和 Patch applicability 授权；
- 只有确定性 Runtime failure taxonomy 可以触发最多一次 Worker retry；
- Finalizer 使用独立只读执行对最终结果做语义检查。

Public API 与控制流见 [`agent_forge/multi_agent/api.py`](agent_forge/multi_agent/api.py) 和
[`多 Agent 编排`](docs/多Agent编排.md)。

## 运行证据与 Workbench

`forge ui` 启动只读 Workbench。Workbench 只投影已有证据，不运行 Agent，也不拥有
Checkpoint、Task result 或 Benchmark outcome。

Workbench 可以审阅三类冻结证据：

1. [Durable Control](http://127.0.0.1:8765/?source=governed&view=overview)：人工输入、审批、Ledger 与恢复；
2. [Multi-Agent Runtime](http://127.0.0.1:8765/?source=orchestration&view=overview)：Frozen Plan、Launch Waves、LIVE timeline、Candidate gates 与 Finalizer；
3. [Mini-50 · Repository Capability](http://127.0.0.1:8765/?source=evaluation&view=overview)：固定 Case、代表案例与版本来源。

实验对比页保留 [R0→R1](http://127.0.0.1:8765/?mode=experiments&source=tool-aci-r1%3Aoverview)、
[R0→R2](http://127.0.0.1:8765/?mode=experiments&source=tool-aci-r2%3Aoverview) 和 Mini-50。
Workbench 不重跑、不移动、不改写历史实验资产。

```bash
.venv/bin/python scripts/review_preflight.py
```

## CLI 与嵌入式使用

```text
forge console   交互运行 Repository Agent
forge run       执行新 Turn
forge resume    从 v4 Checkpoint 恢复同一 Turn
forge demo      生成确定性运行故事
forge inspect   只读查看 Run、Artifact 或源码 Symbol
forge bench     运行批量 Benchmark
forge ui        打开只读 Evidence Workbench
```

外部应用通过 [`Harness`](agent_forge/harness.py) 注入 Model、ToolGateway、Hook、Repository
或 Execution Environment。`Harness.run()` 创建新 Thread/Turn 或同 Thread 的后续 Turn；
`Harness.resume()` 只恢复未完成 Turn。`RunResult` 返回状态、三层身份和产物路径。

## 源码导航

| 想理解的能力 | 第一个入口 |
|---|---|
| Public composition | [`agent_forge/harness.py`](agent_forge/harness.py) |
| Thread/Turn durable truth | [`agent_forge/runtime/domain/thread.py`](agent_forge/runtime/domain/thread.py) |
| Agent 主循环 | [`agent_forge/runtime/application/agent_loop.py`](agent_forge/runtime/application/agent_loop.py) |
| Context 与 Compaction | [`agent_forge/context/application/`](agent_forge/context/application/) |
| Tool 治理 | [`agent_forge/runtime/application/tool_execution.py`](agent_forge/runtime/application/tool_execution.py) |
| Multi-Agent Single/Ultra 路由 | [`apps/repository_run.py`](apps/repository_run.py) |
| Multi-Agent COMMON Domain | [`agent_forge/multi_agent/domain/fanout.py`](agent_forge/multi_agent/domain/fanout.py) |
| Multi-Agent 统一 Scheduler | [`agent_forge/multi_agent/application/fanout.py`](agent_forge/multi_agent/application/fanout.py) |
| LIVE coordination consistency | [`agent_forge/multi_agent/application/live_handoff.py`](agent_forge/multi_agent/application/live_handoff.py) |
| Fanout Checkpoint / Summary | [`agent_forge/multi_agent/adapters/fanout_files.py`](agent_forge/multi_agent/adapters/fanout_files.py) |
| Benchmark / Evaluation | [`agent_forge/bench/`](agent_forge/bench/)、[`agent_forge/evaluation/`](agent_forge/evaluation/) |
| Evidence Workbench | [`apps/workbench/`](apps/workbench/) |

## 文档

- [`架构导览`](docs/架构导览.md)：Ownership 与端到端主链；
- [`Agent 运行数据结构与模型输入`](docs/Agent运行数据结构与模型输入.md)：Thread、Turn、Run、Model Step 与输入快照；
- [`上下文工程`](docs/上下文工程.md)：System Context、Memory、Instructions 与 Tool Schema；
- [`上下文压缩与长任务设计`](docs/上下文压缩与长任务设计.md)：raw Thread、Digest、窗口投影与恢复；
- [`运行治理与工具执行`](docs/运行治理与工具执行.md)：Tool batch、权限、Approval、Ledger 与 Observation；
- [`多 Agent 编排`](docs/多Agent编排.md)：Planning、HARD/LIVE、私有 Worker Thread 与集成；
- [`核心能力与代码入口`](docs/核心能力与代码入口.md)：能力到唯一代码 owner；
- [`运行产物与持久化契约`](docs/运行产物与持久化契约.md)：权威状态、执行证据和派生视图；
- [`生产化边界与扩展`](docs/生产化边界与扩展.md)：当前实现与规模边界；
- [`Debug Lab`](examples/debug_lab/README.md)：确定性运行和按钮式观察；
- [`实验总览`](benchmarks/experiments/README.md)：版本化实验配置与结果入口。

## 开发

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy agent_forge apps
```

NanoHarness 使用 [MIT License](LICENSE)。

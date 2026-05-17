# 08 面向字节/小红书社招的生产化升级说明

这份文档是你面试时的“项目升级版口径”。不要再把项目讲成 toy demo，而要讲成一个 OpenCode-like coding agent runtime 的核心工程切片。

## 当前项目定位

> Agent Forge 是一个 production-oriented coding agent runtime。它不是完整 OpenCode 产品，但实现了社招面试会追问的关键工程层：AgentLoop、runtime-backed multi-agent、TaskGraph、ModelGateway、SessionStore、DiagnosticsTool、DiffTracker、RunReport、EvalHistory、安全策略和 trace。

## 已经补上的能力

| 能力 | 代码位置 | 面试价值 |
| --- | --- | --- |
| AgentLoop | `agent_forge/runtime/agent_loop.py` | 证明你理解 action-observation loop。 |
| AgentRuntime / AgentSpec | `runtime/agent_runtime.py`, `runtime/agent_spec.py` | 证明 multi-agent worker 不是普通函数，而是复用 runtime。 |
| TaskGraph / Scheduler | `workflows/task_graph.py` | 证明你知道多 agent 需要依赖、状态和调度。 |
| ModelGateway | `models/gateway.py` | 证明你知道模型接入要 retry/fallback/usage，不只是 base_url。 |
| SessionStore | `runtime/session.py` | 证明运行可审计、可恢复、可查看历史。 |
| DiagnosticsTool | `tools/diagnostics.py` | 证明 agent 有轻量代码诊断能力，对标 LSP diagnostics。 |
| DiffTracker / rollback | `production/diff_tracker.py` | 证明 agent 改代码可追踪、可回滚。 |
| RunReport | `production/run_report.py` | 证明每次运行有报告，不是终端一闪而过。 |
| EvalHistory | `eval/eval_history.py` | 证明评估能沉淀历史，不是只跑一次。 |

## 和 OpenCode 的差距

OpenCode 是完整产品，通常包含 TUI/IDE/desktop、多 provider、多 session、LSP、MCP、权限、长期会话等。Agent Forge 当前实现的是核心 runtime，不是完整产品形态。

仍然差：

- 没有完整 TUI；
- scheduler 还是顺序，不是并发 ready-node 执行；
- diagnostics 是 Python compile/unittest，不是完整 LSP server；
- rollback 是文件快照，不是完整 patch transaction；
- tool plugin 还不是完整 MCP marketplace；
- 没有真实 token cost/pricing；
- 没有线上多租户、权限系统、容器隔离。

面试时不要硬吹“我复刻了 OpenCode”。更好的说法：

> 我没有复刻 OpenCode 的完整产品形态，而是实现了 OpenCode-like 的核心 runtime：模型网关、agent loop、runtime-backed multi-agent、工具权限、diagnostics、session artifact、diff tracking、rollback 和 eval history。这些是 coding agent 真正落地时的工程底座。

## 现场可演示命令

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate

python run_demo.py --mode single
python run_demo.py --mode multi
python run_demo.py --list-sessions
python run_demo.py --show-run <session_id>
python run_demo.py --rollback-run <session_id>
scripts/verify.sh
```

每次默认会生成：

```text
.agent_forge/runs/<run_id>/
  session.json
  trace.json
  metrics.json
  diff.patch
  report.md
  rollback/
```

这些文件就是你证明“可审计、可恢复、可复盘”的证据。

## 字节/小红书追问时怎么答

### 1. 怎么证明不是 toy project？

答：

> 我从 toy agent loop 往 production-oriented runtime 补了几层：ModelGateway 处理 provider 失败和 fallback；SessionStore 保存每次运行；DiffTracker 记录改动和 rollback bundle；multi-agent 通过 TaskGraph 调度 AgentRuntime-backed workers；DiagnosticsTool 提供 compile/unittest 诊断；EvalHistory 保存回归结果。这些都能本地运行和测试，不只是文档。

### 2. multi-agent 现在是不是生产级？

答：

> 它是生产形状的最小实现。每个 subagent 不是普通函数，而是 AgentRuntime-backed worker，有自己的 AgentSpec 和工具 allowlist。当前 scheduler 为了 demo 稳定仍然顺序执行，下一步会并发 ready nodes，并加入文件 ownership 和 patch conflict merge。

### 3. 模型接入为什么要 gateway？

答：

> agent runtime 不应该直接依赖某个 provider。ModelGateway 统一 retry、fallback、latency、错误归一化和 usage telemetry，这样可以平滑切公司 API、Ollama、OpenAI-compatible provider，也能做灰度和降级。

### 4. agent 改坏代码怎么办？

答：

> 每次 run 前 DiffTracker 会记录文件快照；run 后生成 changed_files、diff.patch、report.md 和 rollback bundle。CLI 支持 `--rollback-run <session_id>`，不会用危险的 git reset，而是只恢复这次捕获到的文件。

### 5. 怎么做评估？

答：

> `eval_cases` 每个 case 有 task 和 verify.py，runner 真实执行验证逻辑。现在还会把每次 eval 写进 `.agent_forge/eval_history.jsonl`，能沉淀 pass rate、失败 case 和 metrics。

## 你应该主动承认的边界

> 当前项目不是完整线上系统。它还没有容器级隔离、完整 LSP、并发 scheduler、MCP marketplace、真实 cost accounting 和 IDE/TUI 产品形态。但它已经覆盖了 coding agent 生产化最核心的工程问题：工具安全、模型网关、上下文、运行时、调度、诊断、变更治理、可观测和评估。

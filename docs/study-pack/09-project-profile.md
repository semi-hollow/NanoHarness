# 09 Project Profile

这份文件用于把 Agent Forge 写成一个对外可讲的开源项目。重点是两件事：

1. 怎么准确描述项目价值，不夸大成已经线上大规模服务。
2. 哪些成熟度短板容易被追问，以及怎么补、怎么解释边界。

## 对外一句话

Agent Forge is a production-style CodingAgent runtime core that turns an LLM into a controlled coding system through context engineering, tool governance, execution policies, MCP tools, trace, usage accounting, evals, and review gates.

中文版本：

Agent Forge 是一个 production-style CodingAgent runtime core，重点不是模型训练或 UI 外壳，而是把 LLM 接入受控代码执行系统所需的上下文工程、工具治理、执行控制、MCP 外部工具、trace、usage、eval 和 review gate。

## 项目定位不要说错

推荐说法：

- Production-style runtime core.
- CodingAgent harness.
- Open-source learning and engineering reference implementation.
- Focused on runtime control plane, not product UI or cloud platform.

不要说：

- 已经是 Codex / Claude Code 同等级产品。
- 已经线上服务真实用户。
- 已经具备企业级容器调度、IDE 插件、远程 MCP 网关。
- 已经做了模型训练或 RL。

更稳的表达是：

> I built a production-style CodingAgent runtime core. It is not a full IDE product, but it implements the core control-plane pieces that such systems need: context construction, tool routing, sandbox and command policies, approval hooks, task state, MCP tool integration, observability, usage reporting, regression evals, and a realistic webhook benchmark.

## 当前最能展示的能力

| capability | project evidence | why it matters |
|---|---|---|
| Agent loop | `agent_forge/runtime/agent_loop.py` | 证明不是一次性 prompt，而是 context -> LLM -> tool -> observation -> recovery 的闭环。 |
| Context engineering | `agent_forge/context/` | 证明知道 repo map、file ranking、retrieval、memory、budget，不是把全仓库硬塞给模型。 |
| Tool governance | `agent_forge/tools/`, `agent_forge/safety/` | 证明 tool calling 是权限、schema、sandbox、command policy、observation 的治理系统。 |
| Execution control | `execution_environment.py`, `hooks.py`, `control.py` | 证明有 max steps、timeout、repeat detection、failure classification、approval mode、worktree 隔离。 |
| MCP integration | `agent_forge/mcp/`, `mcp_tools.example.json` | 证明可以启动 MCP server、发现工具、注册到 agent、调用外部工具。 |
| Observability | `trace.py`, `usage_report.py` | 证明能看每步 token、cost、latency、context breakdown、tool efficiency。 |
| Eval and regression | `eval_cases/`, `eval_runner.py`, `eval_history.py` | 证明不是只跑一个 demo，而是有 capability cases 和回归记录。 |
| Review gate | `review_workflow.py` | 证明 agent 改代码后有确定性审查层。 |
| Realistic fixture | `examples/webhook_service_repo/` | 证明 demo 不只是 calculator，而是涉及签名校验、幂等、side effect、secret boundary。 |

## 对外项目写法

### 一行标题

Agent Forge - Production-style CodingAgent Runtime Core, Python

### 三行简介

Built an open-source CodingAgent runtime core that converts LLM outputs into controlled coding actions through context engineering, tool routing, sandboxed execution, approval hooks, MCP external tools, trace, usage accounting, and regression evals.

Implemented a realistic WebhookPatchBench scenario to validate issue-driven code repair under security and reliability constraints, with per-step trace and cost reports for debugging agent behavior.

Designed the project as a readable systems reference for agent runtime architecture, keeping UI/cloud deployment out of scope while preserving extension points for IDE, remote sandbox, and tool gateway integration.

### Project bullet 版本

- Designed and implemented a production-style CodingAgent runtime core in Python, covering ReAct-style agent loop, context construction, tool routing, sandboxed tool execution, approval hooks, task checkpoints, and trace replay.
- Built a governed tool layer with schema validation, command allowlists, workspace sandboxing, MCP stdio server integration, offline/live web search tools, and failed-observation recovery.
- Added observability and evaluation infrastructure: per-step token/cost/latency usage reports, context/tool efficiency breakdowns, 23 eval scenarios, regression history, and committed run artifacts for reproducible inspection.
- Created WebhookPatchBench, a realistic code-repair benchmark involving webhook signature verification, idempotency, side-effect ordering, secret access blocking, test validation, and review-gate checks.
- Supported multiple model backends through an OpenAI-compatible gateway, including mock/offline mode for deterministic verification and DeepSeek/OpenAI-compatible APIs for real-model runs.

### 更短版本

- Built Agent Forge, a production-style CodingAgent runtime core with context engineering, governed tool execution, MCP external tools, sandbox/approval policies, trace/usage reporting, and regression evals.
- Developed WebhookPatchBench to validate code-repair behavior under security, idempotency, side-effect, and test constraints, producing reproducible trace and cost evidence.

## 成熟度最容易被怀疑的地方

这些不是都必须现在补完，但开源和技术追问时要心里有数。

| risk | current state | why it may be challenged | answer or next step |
|---|---|---|---|
| 没有真实线上用户 | 有真实模型运行 artifact，但不是线上产品。 | Senior 项目会被问是否经历真实流量、SLA、事故。 | 诚实说这是 runtime core，不是 SaaS；展示 trace、usage、eval、failure recovery；后续可接服务层。 |
| Sandbox 不是容器级 | 有 workspace sandbox、command policy、worktree 隔离。 | Coding agent 真生产通常需要 container/firecracker/权限隔离。 | 说当前实现控制面策略，下一步会替换 ExecutionEnvironment 为 Docker/remote sandbox。 |
| Eval 数据集偏小 | 23 个 eval cases，覆盖核心 failure modes。 | 会被问 badcase 如何回流、如何量化 solve rate。 | 增加 benchmark report、baseline 对比、case taxonomy、ablation。 |
| Multi-agent 不是分布式 | 有 Supervisor、TaskGraph、AgentRuntime、artifact handoff。 | 会被问并发、负载、去中心化协作。 | 说当前是主子式 runtime orchestration；生产分布式属于 scheduler/queue 层。 |
| MCP 还不是远程网关 | 有 stdio server、tool discovery、web tools。 | 会被问 OAuth、remote MCP、tool marketplace、权限隔离。 | 当前证明协议边界；下一步加 remote transport、auth、rate limit、tool quota。 |
| Context retrieval 不是工业 RAG | 有 repo map、lexical retrieval、file ranker、symbol search。 | 会被问 embedding、hybrid search、reranker、GraphRAG。 | 说 coding repo 的第一阶段优先 deterministic retrieval；知识平台可替换 ContextStrategy retrieval 层。 |
| 没有 CI/release badge | 本地 `scripts/verify.sh` 完整，但未必有 GitHub Actions。 | 开源成熟度会看 CI、license、contributing、release。 | 发布前加 GitHub Actions、LICENSE、CONTRIBUTING、SECURITY、demo screenshot。 |
| UI / IDE 缺失 | 只有 CLI 和 scripts。 | Coding agent 项目常被期待有 IDE/TUI 体验。 | 说项目刻意聚焦 runtime core；IDE/TUI 是 product surface，可接在 CLI/API 外面。 |
| 成本/延迟没有长期趋势 | 有单次 usage report，缺 dashboard。 | 会被问成本优化是否可持续。 | 增加 usage history、provider comparison、per-case trend。 |
| 工具安全不够企业级 | 有 allowlist、approval、guardrail、sandbox。 | 会被问 prompt injection、secret exfiltration、non-idempotent rollback。 | 增强 policy tests、secret scanner、dry-run plan、non-idempotent operation ledger。 |

## 开源前建议补的 P0/P1

P0 是发布前最好补上的，不一定是 Agent 核心，但影响别人第一眼判断成熟度。

| priority | item | why |
|---|---|---|
| P0 | `LICENSE` | 没 license 的项目不算真正可复用开源项目。 |
| P0 | GitHub Actions CI | 让别人看到每次 push 都跑 verify/unit tests。 |
| P0 | README 顶部架构图和快速 demo GIF/screenshot | 开源项目第一屏要让人 30 秒知道价值。 |
| P0 | CONTRIBUTING / SECURITY | 显示你理解开源协作和安全披露边界。 |
| P0 | Release checklist | 说明如何跑 mock、DeepSeek、MCP、WebhookPatchBench。 |
| P1 | Benchmark summary table | 把 23 个 eval case 的能力覆盖和结果做成表。 |
| P1 | Ablation notes | 对比无 context strategy、无 tool router、无 hooks 时的失败模式。 |
| P1 | Docker sandbox extension plan | 不必完整实现，但给出接口设计。 |
| P1 | Provider comparison report | Mock / DeepSeek / OpenAI-compatible 的成本、延迟、失败类型对比。 |

## 技术讲解主线

建议按这个顺序讲，不要从“我写了很多文件”开始。

1. 问题：LLM 直接写代码不可控，真正难点是上下文、工具、权限、失败恢复和可观测。
2. 架构：CLI 只是入口，核心是 AgentLoop + Context + ToolRegistry + Safety + Trace + Eval。
3. 主链路：WebhookPatchBench 展示 issue -> context -> tool call -> patch -> test -> report。
4. 两个亮点：Context engineering 和 runtime control plane。
5. 成熟度：展示 MCP、worktree、approval mode、usage report、eval regression。
6. 边界：不是 UI 产品、不是云平台、不是模型训练；这些有清晰扩展点。

## 常见追问回答

### 为什么这不是 toy project

因为它不只展示一次 LLM 调用，而是实现了 agent runtime 的控制面：

- context budget 和 retrieval
- tool schema 和 routing
- sandbox、permission、command policy
- approval hooks
- model gateway 和 usage accounting
- task checkpoint 和 trace replay
- eval cases 和 regression
- MCP 外部工具协议
- review gate 和 realistic benchmark

toy project 通常只做 prompt -> model -> print。本项目重点是让模型动作可控、可审计、可恢复。

### 为什么还不是完整生产产品

因为完整产品还需要 IDE/TUI、云端 sandbox、账号权限、队列、远程工具网关、CI/CD、线上 telemetry 和用户反馈闭环。本项目刻意把这些放在边界外，避免核心 runtime 被产品外壳淹没。

### 如果要再推进一版，最先做什么

先做四件事：

1. GitHub Actions CI + LICENSE + SECURITY，让开源项目可信。
2. Eval dashboard，把 23 个 cases 的通过率和 failure taxonomy 展示出来。
3. Docker/remote sandbox adapter，把 `ExecutionEnvironment` 从 worktree 扩展到容器。
4. Benchmark report，把 WebhookPatchBench 的 trace、usage、patch、review 结果整理成一页。

## GitHub 项目简介

短描述：

Production-style CodingAgent runtime core with context engineering, governed tool execution, MCP tools, trace, usage reporting, and eval regression.

README tagline：

Agent Forge focuses on the runtime control plane behind coding agents: context, tools, permissions, execution, observability, evaluation, and recovery.

Pinned repo description：

Open-source Python CodingAgent runtime core for studying and building controlled LLM code-execution systems.

## 对外展示 Checklist

发布前检查：

- README 首屏能解释项目定位。
- Quick Start 能 5 分钟跑通 mock。
- DeepSeek / OpenAI-compatible API 不写死 key。
- `scripts/verify.sh` 通过。
- `scripts/verify_mcp.sh` 通过。
- committed run artifacts 可读。
- study-pack 文件名中性，不混入私人用途说明。
- 没有 `.env`、真实 key、个人路径污染。
- LICENSE 存在。
- CI badge 存在。

## 最后口径

最稳的项目表述：

> Agent Forge is not a UI clone of Codex. It is a readable, production-style runtime core that implements the control-plane capabilities behind coding agents. I used it to study and demonstrate how context, tools, safety, execution, observability, MCP integration, and evals fit together in a real code-modification loop.

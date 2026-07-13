# 能力真实性矩阵

本文将已经接入主 runtime 的能力，与轻量 primitive、demo 和刻意收敛范围的边界分开。

目标很简单：**不夸大。** Agent Forge 最适合被描述为精简 AI Agent Runtime 与
Evaluation Harness，而不是完整 IDE、SaaS、distributed swarm 或 benchmark leaderboard。

每项能力的方法级入口见[能力入口索引](guides/code-reading-map.md#能力入口索引)。

## 状态说明

| 状态 | 含义 |
| --- | --- |
| Green | 已接入主 runtime，并有测试或真实 smoke check 覆盖。 |
| Yellow | 可运行的轻量 primitive 或 evaluation contract，但不是完整产品子系统。 |
| Red | 不应作为 production capability 介绍，只能作为 demo/helper/test boundary。 |

## 矩阵

| 能力 | 状态 | 真实实现 | 不能声称什么 | 主要文件 |
| --- | --- | --- | --- | --- |
| Agent runtime loop | Green | `AgentLoop` 构造 context、调用模型、路由工具、记录 observation、更新 checkpoint 和 trace。 | 不是完整 IDE Agent 产品。 | `agent_forge/runtime/agent_loop.py` |
| OpenAI-compatible model call | Green | 真实 HTTP chat-completions client、标准化 tool call、retry/fallback wrapper、usage telemetry。 | 不声称支持 OpenAI-compatible API 之外的大量 provider SDK。 | `agent_forge/runtime/llm_client.py`、`agent_forge/models/gateway.py` |
| Tool governance | Green | Tool 依次经过 routing、registry validation、permission hook、command policy 和 sandbox。 | 不把 prompt-only safety 或本地模式说成 OS-level isolation。 | `agent_forge/tools/`、`agent_forge/safety/`、`agent_forge/runtime/hooks.py` |
| Workspace sandbox | Green | Path 解析到指定 workspace 下，并阻断 symlink escape。 | Local mode 不是 container-grade isolation。 | `agent_forge/safety/sandbox.py` |
| Execution environment | Green | `forge run` 和 `forge bench swebench` 支持 local、detached worktree、OCI container。OCI command 带 network、CPU、memory、PID、capability 和 read-only root 控制，manifest 保留 image/command evidence。 | OCI mode 依赖外部 Docker-compatible runtime 和任务适配 image，也不是 hostile multi-tenant isolation；host file tool 仍由 mounted snapshot 上的 `WorkspaceSandbox` 限制。 | `agent_forge/runtime/execution_environment.py`、`agent_forge/forge_cli.py`、`agent_forge/bench/swebench.py`、`agent_forge/tools/run_command.py`、`agent_forge/tools/diagnostics.py` |
| 持久化 human clarification | Green | Pre-loop ambiguity 和模型 `ask_human` 会原子持久化 request，在同一 turn 中优先阻断其他 tool，run 转为 `waiting_human`，再从 `forge respond` 记录的回答继续。 | 它只记录信息，不授权副作用，也不恢复隐藏 model state。 | `agent_forge/runtime/human_input.py`、`agent_forge/runtime/agent_loop.py`、`agent_forge/forge_cli.py` |
| 副作用人工审批 | Green | 写入型 action 可以在执行前停机，持久化 approval file，并只在 `forge approve` 后继续。 | Approval 与 clarification 是不同契约；本地文件 store 不是 multi-user authorization service。 | `agent_forge/runtime/approval.py`、`agent_forge/forge_cli.py` |
| Stale approval detection | Green | Approval 保存 operation fingerprint；target drift 会在执行前将 approval 标成 stale。 | 不声称可以消除 distributed system 中的所有 race。 | `agent_forge/runtime/approval.py`、`agent_forge/runtime/agent_loop.py` |
| Operation ledger | Green | Side effect 有稳定 operation key、pre/post fingerprint、duplicate skip 和 stale-target detection。 | 不是 distributed transaction log。 | `agent_forge/runtime/operation_ledger.py` |
| Checkpoint resume | Green | Checkpoint 为 continuation 提供 context，包含已回答 human input；`forge resume` 会写入 report 可见的 resume-chain artifact。 | 不恢复隐藏 model state 或完整 process memory。 | `agent_forge/runtime/task_state.py`、`agent_forge/runtime/human_input.py`、`agent_forge/forge_cli.py` |
| SWE-bench-shaped runner | Green | 加载 case、checkout base commit、运行 Agent、写 patch 和 `predictions.jsonl`。 | 没有执行和解析 official harness 时，不声称 official resolved rate。 | `agent_forge/bench/swebench.py` |
| Official SWE-bench per-case evaluation | Green | `--evaluate` 在 benchmark output directory 中运行，解析 aggregate/per-case JSON，区分 resolved、unresolved、error、empty-patch、incomplete。 | Process exit code 不证明 resolved；official denominator 为空时不能声称 resolved rate。 | `agent_forge/bench/swebench.py`、`agent_forge/bench/official_results.py` |
| Direct baseline | Green | 使用同一模型但不提供工具，并提取 diff 用于比较。 | 它刻意更弱，不是 competitive baseline。 | `agent_forge/bench/swebench.py` |
| Quantitative scorecard | Green | 每个 benchmark 写入 per-case 和 aggregate patch/local/official evidence，以及带明确 denominator 的 token、cost、latency、tool-failure、taxonomy metric。 | Patch rate 不是 correctness；local verification 不是 official resolution。 | `agent_forge/evaluation/scorecard.py`、`agent_forge/bench/report.py` |
| Paired runtime ablation | Green | `forge eval ablation` 比较 matched scorecard，并拒绝 dataset、split、provider/model、case-set drift；tool routing 提供真实 `all` vs `task-aware` factor。 | 每个 variant 一次 run 不能估计随机方差，也不能证明全局提升。 | `agent_forge/evaluation/experiment.py`、`agent_forge/tools/tool_router.py`、`agent_forge/forge_cli.py` |
| Multi-agent coordinator | Green | 顺序 Implementer/Reviewer/Verifier workflow 复用 `AgentLoop`，通过 artifact 交换信息。 | 不是 peer-to-peer swarm 或 distributed multi-agent runtime。 | `agent_forge/multi_agent/coordinator.py` |
| Artifact handoff | Green | Role output 被持久化，后续 role 通过显式 artifact context 读取。 | 不暗示 Agent 共享隐藏 memory。 | `agent_forge/multi_agent/artifacts.py` |
| Live subagent fanout | Green | Validated DAG 在 disposable worktree 中运行独立 `AgentLoop`/LLM/registry，执行 per-task step budget、声明 scope 分批、实际 touched-file 校验、确定性 patch apply；隔离 finalizer 能看到 candidate diff，pre/post gate 检测 verifier mutation。 | 它是 local coordinator，不是 distributed worker service、peer swarm 或自动 model-driven task decomposition；worker 读取 committed `base_head`，不是 ambient uncommitted file。 | `agent_forge/multi_agent/fanout.py`、`agent_forge/multi_agent/live_fanout.py`、`agent_forge/forge_cli.py` |
| Fanout partial recovery | Green | 增量 checkpoint 保存 plan/base identity 和 accepted worker；resume 校验 patch SHA-256，在新 workspace 重放已完成 patch，只重跑未完成 task；稳定 worker human thread 可复用已回答 clarification。 | 进程被强杀可能留下 orphan worktree；fanout 刻意拒绝 per-operation manual approval，因为 ephemeral workspace identity 还不能安全复用。 | `agent_forge/multi_agent/live_fanout.py`、`agent_forge/runtime/human_input.py` |
| Mini-case | Yellow | 小型确定性 scorecard 为 research/ops 场景评估显式 evidence。 | 不是 benchmark，也不能证明一般 Agent 能力。 | `agent_forge/evaluation/mini_cases.py`、`docs/evaluation/mini-cases/` |
| Local Evidence Console | Yellow | 运行受限 CLI action，提供 isolation、network、routing、approval、Skills/MCP、sequential/fanout control；渲染 role artifact、Multi 后 Single timeline、runtime control、claim boundary、cost、feedback 和 privacy-conscious dataset export。 | 它读取本地 artifact 并启动本地 job，不是 production web app、hosted SaaS，也不能证明 latest run 中没有触发的能力通过。 | `agent_forge/ui.py` |
| MCP stdio subset | Green | 启动 subprocess、发现 tool、通过 JSON-RPC 调用并标准化 content block。 | 不声称完整 MCP SDK compatibility。 | `agent_forge/tools/mcp_stdio.py`、`agent_forge/mcp/server.py` |
| MCP-style local adapter | Yellow | 将本地 MCP-like spec 转成 local tool。 | 不是完整 MCP protocol。 | `agent_forge/tools/adapters/mcp_style_adapter.py` |
| Skills layer | Green | 内置/自定义 Skill 会影响 prompt、tool routing 和 trace metadata。 | 不声称 marketplace 或 remote skill distribution。 | `agent_forge/skills/` |
| Human feedback capture | Green | `forge eval feedback` 将 accepted/needs-work/rejected outcome、label、note 写在 run evidence 旁。 | Human label 不是 official benchmark result。 | `agent_forge/evaluation/feedback_dataset.py`、`agent_forge/forge_cli.py` |
| Evidence dataset export | Green | `forge eval export-dataset` 将 trace、selected context、tool policy、environment、failure class、evaluation status 和 human feedback 连接成 JSONL，patch content 默认不导出。 | 没有 curation、privacy review 和 dataset governance 时，不把导出 evidence 称为 production training data。 | `agent_forge/evaluation/feedback_dataset.py` |

## 推荐定位

可以这样介绍：

> Agent Forge 是一个精简 AI Agent Runtime Control Plane。难点不在 UI 或 prompt
> 表面，而在 context selection、governed tools、approval、resume safety、traceability
> 和 evaluation evidence。

不要这样介绍：

> 这是一个 production Claude Code replacement，带 distributed multi-agent execution
> 和 official SWE-bench solved rate。

## 当前最高价值的下一步

1. 对 matched ablation 做多 seed 重复实验，只发布有 artifact 和 official denominator
   支撑的结果。
2. 将多次 fanout run 与 matched serial plan 比较，再讨论 latency 或 quality improvement。
3. 在真实训练 pipeline 使用 exported run evidence 前，补 privacy filter 和 dataset
   version manifest。
4. 增加真实 Docker/Podman smoke job，同时保持 unit test 不依赖已安装 container runtime。

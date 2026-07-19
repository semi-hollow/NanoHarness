# 能力真实性矩阵

本文将已经接入主 runtime 的能力，与轻量 primitive、demo 和刻意收敛范围的边界分开。

目标很简单：**不夸大。** Agent Forge 最适合被描述为精简 AI Agent Runtime 与
Evaluation Harness，而不是完整 IDE、SaaS、distributed swarm 或 benchmark leaderboard。

每项能力的主要实现入口列在矩阵最后一列；跨 Capability 调用以具名 API、Port 和类型签名为准。

## 状态说明

| 状态 | 含义 |
| --- | --- |
| Green | 已接入主 runtime，并有测试或真实 smoke check 覆盖。 |
| Yellow | 可运行的轻量 primitive 或 evaluation contract，但不是完整产品子系统。 |
| Red | 不应作为 production capability 介绍，只能作为 demo/helper/test boundary。 |

## 矩阵

| 能力 | 状态 | 真实实现 | 不能声称什么 | 主要文件 |
| --- | --- | --- | --- | --- |
| Agent runtime loop | Green | `AgentLoop` 编排 start/prepare/turn/stop；`AgentRunSession` 显式持有运行数据；turn preparation、工具治理和生命周期由独立 application service 执行。 | 不是完整 IDE Agent 产品。 | `agent_forge/runtime/application/agent_loop.py`、`session.py`、`turn_preparation.py`、`tool_execution.py`、`run_lifecycle.py` |
| Embeddable Harness API | Green | 顶层 `Harness.run/resume` 接受类型化配置和请求，返回状态、checkpoint 与 artifact 路径；现有 Model、Tool、Context、State、Event、Environment Port 从 `agent_forge.extensions` 稳定导出。每次 run 通过 `EventSinkFactory` 获得独立 identity 并统一 publish；`forge run --config` 使用严格 schema、受控 built-in registry、CLI/环境/config/default 优先级和脱敏 resolved artifact。 | 当前是 `0.x` 本地 Runtime SDK，不是 hosted service、通用 workflow engine 或拥有第三方插件市场的成熟框架。 | `agent_forge/harness.py`、`extensions.py`、`configuration.py`、`tests/test_public_harness.py`、`tests/test_run_configuration.py` |
| Hierarchical instructions | Green | Resolver 按 global、repository、nested directory、local override、runtime override 的稳定顺序发现 `PROJECT_INSTRUCTIONS.md`、`CLAUDE.md`、`AGENTS.md` 与 `FORGE.md`，高优先级内容优先获得 UTF-8 字节预算；trace 只记录路径、哈希、优先级和截断。 | 不声称兼容 Codex/Claude Code 的全部语法、import 规则或用户配置；runtime override 正文不写入 config artifact。 | `agent_forge/context/instructions.py`、`runtime/adapters/context_assembler.py`、`tests/test_instruction_resolver.py` |
| Context construction | Green | `TurnPreparation` 只调用 `ContextAssemblerPort`；文件系统 Adapter 构造 repository map、选择候选文件，再按区段预算治理 policy、已激活 Skill、分层指令、长期记忆、preview、retrieval 与 working memory，静态渲染不超过 `max_context_chars`。 | 当前是透明 lexical ranking/retrieval，不声称 vector RAG、learned reranker 或无限上下文。 | `agent_forge/runtime/ports/context.py`、`runtime/adapters/context_assembler.py`、`agent_forge/context/` |
| Full-request context window | Green | `ContextWindowManager` 对 system、会话、tool schema 和预留输出统一估算；压缩不拆分 tool transaction，生成带 source hash 的 `SessionDigest`，原始 trace 不删除；provider overflow 只在压缩确实降低预算时重试一次。 | 调用前 token 是近似估算；不恢复 provider KV Cache，也不把 digest 当作原始事实。 | `agent_forge/context/application/compaction.py`、`runtime/application/turn_preparation.py`、`agent_loop.py` |
| Evidence-backed long-term memory | Green | Working memory、checkpoint digest 和长期记录分层；长期记录经过 candidate、evidence-backed active、superseded/retired 生命周期，支持 workspace/agent 隔离、TTL 和透明中英文 lexical recall。 | 不声称向量 RAG、用户画像、组织知识、多租户共享 memory 或模型自主写入真相。 | `agent_forge/context/domain/memory.py`、`application/memory_service.py`、`adapters/memory_json.py`、`context/api.py` |
| Model capability negotiation | Green | 显式配置或 `ModelPort.capabilities` 归一化为 `ModelCapabilities`；context window 限制 prompt budget，非并行模型每 turn 只执行一个工具，内置 OpenAI-compatible transport 在 native tools 与受限单工具 JSON 文本协议间切换。 | `structured_output`、`reasoning_tokens`、`prompt_cache`、`supports_images` 当前只是 Adapter 声明与 evidence，不代表核心 Runtime 已实现相应协议；自定义 ModelPort 必须自己遵守声明。 | `runtime/domain/model.py`、`application/model_policy.py`、`llm_client.py`、`tool_execution.py` |
| OpenAI-compatible model call | Green | 真实 HTTP chat-completions client、provider usage、累计成本、retry/fallback；`ToolCallNormalizer` 只修复可确定解析的参数和可见工具文本调用，invalid call 使用 repair prompt 有界重试，context overflow 由 Runtime 接管。 | 不猜测工具名或缺失业务参数；不声称支持 OpenAI-compatible API 之外的大量 provider SDK 或 provider-specific 调优平台。 | `agent_forge/runtime/llm_client.py`、`agent_forge/models/gateway.py`、`tool_call_normalizer.py` |
| Public lifecycle hooks | Green | `RuntimeHook` 覆盖 before/after model、before/after tool、checkpoint 和 stop；附加 Hook 与默认 environment/permission/redaction 链组合，前置与 completion gate 异常 fail closed，后置/通知异常隔离并写入 hook evidence。 | 不允许用附加 Hook 替换安全链；完整 `hook_policy` override 只与自定义 Environment/Tool 组合使用，由接入方承担契约。 | `agent_forge/hooks.py`、`runtime/hooks.py`、`runtime/application/agent_loop.py`、`run_lifecycle.py` |
| Tool governance | Green | Tool 依次经过 routing、registry validation、permission hook、command policy 和 sandbox。 | 不把 prompt-only safety 或本地模式说成 OS-level isolation。 | `agent_forge/tools/`、`agent_forge/safety/`、`agent_forge/runtime/hooks.py` |
| Workspace sandbox | Green | Path 解析到指定 workspace 下，并阻断 symlink escape。 | Local mode 不是 container-grade isolation。 | `agent_forge/safety/sandbox.py` |
| Execution environment | Green | `forge run` 和 `forge bench swebench` 支持 local、detached worktree、OCI container。OCI command 带 network、CPU、memory、PID、capability 和 read-only root 控制，manifest 保留 image/command evidence。 | OCI mode 依赖外部 Docker-compatible runtime 和任务适配 image，也不是 hostile multi-tenant isolation；host file tool 仍由 mounted snapshot 上的 `WorkspaceSandbox` 限制。 | `agent_forge/runtime/execution_environment.py`、`agent_forge/cli/repository.py`、`agent_forge/bench/application/swebench.py` |
| 持久化 human clarification | Green | Pre-loop ambiguity 和模型 `ask_human` 会原子持久化 request，在同一 turn 中优先阻断其他 tool，`RunLifecycle` 将 run 转为 `waiting_human`，再从 `forge respond` 记录的回答继续。 | 它只记录信息，不授权副作用，也不恢复隐藏 model state。 | `agent_forge/runtime/application/run_preparation.py`、`tool_execution.py`、`run_lifecycle.py`、`agent_forge/runtime/adapters/human_input_json.py` |
| 副作用人工审批 | Green | 写入型 action 可以在执行前停机，持久化 approval file，并只在 `forge approve` 后继续。 | Approval 与 clarification 是不同契约；本地文件 store 不是 multi-user authorization service。 | `agent_forge/runtime/application/tool_authorization.py`、`agent_forge/runtime/adapters/approval_json.py`、`agent_forge/cli/operator.py` |
| Stale approval detection | Green | Approval 保存 operation fingerprint；target drift 会在执行前将 approval 标成 stale。 | 不声称可以消除 distributed system 中的所有 race。 | `agent_forge/runtime/application/tool_authorization.py`、`operation_tracker.py` |
| Operation ledger | Green | Side effect 有稳定 operation key、pre/post fingerprint、duplicate skip 和 stale-target detection。 | 不是 distributed transaction log。 | `agent_forge/runtime/application/operation_tracker.py`、`agent_forge/runtime/adapters/operation_ledger_json.py` |
| Checkpoint resume | Green | Checkpoint 为 continuation 提供 context，包含已回答 human input；`forge resume` 会写入 report 可见的 resume-chain artifact。 | 不恢复隐藏 model state 或完整 process memory。 | `agent_forge/runtime/application/operator_control.py`、`agent_forge/runtime/adapters/task_state_json.py`、`agent_forge/cli/resume.py` |
| 控制面现场展示 | Green | `forge showcase hitl/approval` 用确定性模型请求驱动正式 AgentLoop，真实产生 waiting checkpoint、人工决定、continuation trace 和工具副作用。 | 固定的 model tool call 不证明模型推理质量；showcase 证明的是 Harness 控制面。 | `agent_forge/showcase/control_plane.py`、`docs/architecture/runtime-control-plane.md` |
| 协作式 run control | Green | 嵌入式 `RunController` 可提交 pause、cancel、steer；AgentLoop 在 turn 开始、模型返回和工具之间消费信号。pause/cancel 形成类型化终态与 checkpoint；steer 追加用户方向并丢弃边界内过时模型响应。 | 不是进程级强制抢占、远端控制服务或自动补偿事务；正在执行的单个 HTTP/命令不会被中途终止，既有副作用不会回滚。 | `agent_forge/control.py`、`runtime/application/run_control.py`、`agent_loop.py`、`tool_execution.py` |
| 会话级 active-task switch | Red | 当前 one command 对应 one run/one task；可暂停当前 run，再由调用方创建另一 Task。 | 不声称存在 session `active_task` pointer、任务队列、跨 run 优先级或自动恢复旧 Task。 | `agent_forge/runtime/domain/task.py`、`agent_forge/harness.py` |
| SWE-bench-shaped runner | Green | 加载 case、checkout base commit、运行 Agent、写 patch 和 `predictions.jsonl`；`forge bench cases/case` 公开 Smoke-5 的 300-case 候选全集、选择契约、issue 和测试名称，并默认隐藏 test/gold patch。 | Smoke-5 是机制回归样本，不代表 Lite 总体表现；没有 official harness 时，不声称 official resolved rate。 | `agent_forge/bench/application/swebench.py`、`application/case_inspection.py`、`presentation/case_inspection.py` |
| Official SWE-bench per-case evaluation | Green | `--evaluate` 在 benchmark output directory 中运行，解析 aggregate/per-case JSON，区分 resolved、unresolved、error、empty-patch、incomplete。 | Process exit code 不证明 resolved；official denominator 为空时不能声称 resolved rate。 | `agent_forge/bench/adapters/official_evaluator.py`、`official_results.py` |
| Direct baseline | Green | 使用同一模型但不提供工具，并提取 diff 用于比较。 | 它刻意更弱，不是 competitive baseline。 | `agent_forge/bench/adapters/case_runtime.py` |
| Quantitative scorecard | Green | 每个 benchmark 写入 per-case 和 aggregate patch/local/official evidence，以及有明确 denominator 的 token、cost、latency、tool failure、context compaction、memory recall、tool-call repair 和 taxonomy metric。 | Recall、repair、Skill activation 和 patch rate 都不是 correctness；local verification 不是 official resolution。 | `agent_forge/evaluation/application/scorecard.py`、`agent_forge/bench/presentation/report.py` |
| Paired runtime ablation | Green | `forge eval ablation` 比较 matched scorecard，并拒绝 dataset、case、provider/model、sampling temperature 与未声明 runtime drift；支持 routing、Skill manifest、frozen Memory snapshot、Context Window、tool burst 和 temperature 单因素。 | 每个 variant 一次 run 不能估计随机方差；当前不承诺 provider-independent seed。 | `agent_forge/evaluation/domain/ablation.py`、`agent_forge/bench/application/swebench.py`、`agent_forge/cli/dispatch.py` |
| Multi-agent coordinator | Green | 顺序 Implementer/Reviewer/Verifier workflow 复用 `AgentLoop`，通过 artifact 交换信息。 | 不是 peer-to-peer swarm 或 distributed multi-agent runtime。 | `agent_forge/multi_agent/application/coordinator.py` |
| Artifact handoff | Green | Role output 被持久化，后续 role 通过显式 artifact context 读取。 | 不暗示 Agent 共享隐藏 memory。 | `agent_forge/multi_agent/adapters/artifact_files.py`、`application/coordinator.py` |
| Live subagent fanout | Green | Validated DAG 在 disposable worktree 中运行独立 `AgentLoop`/LLM/registry，执行 per-task step budget、声明 scope 分批、实际 touched-file 校验、确定性 patch apply；隔离 finalizer 能看到 candidate diff，pre/post gate 检测 verifier mutation。 | 它是 local coordinator，不是 distributed worker service、peer swarm 或自动 model-driven task decomposition；worker 读取 committed `base_head`，不是 ambient uncommitted file。 | `agent_forge/multi_agent/domain/live.py`、`application/live_fanout.py`、`adapters/local_worker.py` |
| Fanout partial recovery | Green | 增量 checkpoint 保存 plan/base identity 和 accepted worker；resume 校验 patch SHA-256，在新 workspace 重放已完成 patch，只重跑未完成 task；稳定 worker human thread 可复用已回答 clarification。 | 进程被强杀可能留下 orphan worktree；fanout 刻意拒绝 per-operation manual approval，因为 ephemeral workspace identity 还不能安全复用。 | `agent_forge/multi_agent/application/live_fanout.py`、`adapters/fanout_files.py` |
| Mini-case | Yellow | 小型确定性 scorecard 为 research/ops 场景评估显式 evidence。 | 不是 benchmark，也不能证明一般 Agent 能力。 | `evaluation/application/mini_cases.py`、`docs/evaluation/mini-cases/` |
| Local Evidence Console | Yellow | 运行受限 CLI action，提供 isolation、network、routing、approval、Skills/MCP、sequential/fanout control；渲染 role artifact、Multi 后 Single timeline、Memory/Context/model-adaptation 实际计数、claim boundary、cost、feedback 和 dataset export。 | 它读取本地 artifact 并启动本地 job，不是 production web app 或 hosted SaaS；未触发能力显示 0，不构造 synthetic pass。 | `agent_forge/workbench/presentation/http.py`、`agent_forge/workbench/adapters/evidence_files.py` |
| MCP stdio subset | Green | 启动 subprocess、发现 tool、通过 JSON-RPC 调用并标准化 content block。 | 不声称完整 MCP SDK compatibility。 | `agent_forge/tools/mcp_stdio.py`、`agent_forge/mcp/server.py` |
| MCP-style local adapter | Yellow | 将本地 MCP-like spec 转成 local tool。 | 不是完整 MCP protocol。 | `agent_forge/tools/adapters/mcp_style_adapter.py` |
| Skills progressive disclosure | Green | Registry 先用 name/description/tags/activation terms 产生不含 procedure 的 `SkillCatalogEntry`，再只为选中版本激活完整 `SkillSpec`；prompt、tool routing、来源与选择原因进入 runtime/trace，benchmark 固定 manifest hash。 | Manifest 当前会被本地进程解析，渐进披露指“模型上下文只注入已激活正文”，不是远端 marketplace 或任意脚本执行系统；Skill 激活不等于有效。 | `agent_forge/skills/registry.py`、`runtime/application/run_preparation.py`、`tests/test_skill_disclosure.py` |
| Streaming events / OTEL | Green | 配置 listener 后，默认 EventSink 双写脱敏、有序 `RuntimeEvent`；真实 model/tool started/completed 配对 OTEL child span，run 是 root span，context 是 retrieval span；listener 默认 fail open，内部 JSON 同步保留。 | 当前非 streaming 模型 transport 不产生 token delta；不内置 collector/exporter，不把 OTEL 投影当作 durable evidence，也不默认上传 prompt、参数或 observation。 | `observability/adapters/streaming.py`、`observability/adapters/otel.py`、`tests/test_streaming_otel.py` |
| Human feedback capture | Green | `forge eval feedback` 将 accepted/needs-work/rejected outcome、label、note 写在 run evidence 旁。 | Human label 不是 official benchmark result。 | `agent_forge/evaluation/adapters/feedback_dataset_files.py`、`agent_forge/cli/dispatch.py` |
| Evidence dataset export | Green | `forge eval export-dataset` 将 trace、selected context、tool policy、environment、failure class、evaluation status 和 human feedback 连接成 JSONL，patch content 默认不导出。 | 没有 curation、privacy review 和 dataset governance 时，不把导出 evidence 称为 production training data。 | `agent_forge/evaluation/adapters/feedback_dataset_files.py` |

## 推荐定位

可以这样介绍：

> Agent Forge 是一个精简 AI Agent Runtime Control Plane。难点不在 UI 或 prompt
> 表面，而在 context selection、governed tools、approval、resume safety、traceability
> 和 evaluation evidence。

不要这样介绍：

> 这是一个 production Claude Code replacement，带 distributed multi-agent execution
> 和 official SWE-bench solved rate。

## 当前最高价值的下一步

1. 对 Memory、Skill、Context Window 和 tool routing 做 matched repetition；仅在 provider
   明确支持时固定 seed，只发布有 artifact 和 official denominator 支撑的结果。
2. 将多次 fanout run 与 matched serial plan 比较，再讨论 latency 或 quality improvement。
3. 在真实训练 pipeline 使用 exported run evidence 前，补 privacy filter 和 dataset
   version manifest。
4. 增加真实 Docker/Podman smoke job，同时保持 unit test 不依赖已安装 container runtime。

# Profile Drafting Brief

Use this brief when asking a writing assistant to turn Agent Forge into a
professional project entry. It is written as a copy-ready prompt plus factual
project evidence, so the generated output stays accurate and does not overclaim
production traffic.

## Copy-Ready Prompt

```text
你是一名资深技术履历写作顾问，目标岗位是 Senior AI Agent / CodingAgent Engineer。

请基于下面的项目信息，帮我生成一段可以放到中文技术履历里的项目经历。要求：

1. 不要夸大成已经线上大规模商用。
2. 要体现 Senior 工程能力，而不是 toy demo。
3. 强调 Agent runtime core、上下文工程、工具治理、执行控制、MCP、trace、usage、eval、review gate。
4. 用 4-6 条项目 bullet，每条尽量包含“做了什么 + 为什么重要 + 工程结果/证据”。
5. 语气专业、可信、可被技术面试追问。
6. 如果需要英文版，也给一版英文 resume bullets。

项目名称：
Agent Forge

项目定位：
Agent Forge 是一个 production-style CodingAgent runtime core。它不是 UI 产品，也不是模型训练项目，而是聚焦把 LLM 接入受控代码执行系统所需的 runtime control plane：context engineering、model gateway、tool governance、execution environment、approval hooks、task state、MCP external tools、trace、usage accounting、eval regression、review gate。

项目核心能力：
- ReAct-style AgentLoop：context -> LLM -> tool call -> observation -> recovery -> final answer。
- Context engineering：repo map、file ranking、lexical retrieval、selected file previews、memory summary、topic-shift handling、token budget breakdown。
- Tool governance：ToolRouter、ToolRegistry、schema validation、failed Observation recovery、workspace sandbox、permission policy、command allowlist、human approval hooks。
- Execution control：max steps、timeout、repeated action detection、failure classification、approval modes、local/worktree execution environment、trace replay、task checkpoint。
- MCP integration：内置 stdio MCP server，支持 tool discovery、tools/list、tools/call，并提供 repo_policy、current_time、web_search、web_fetch 等工具；默认 offline，可显式接 DuckDuckGo / OpenAI / Claude hosted web search。
- Observability：TraceRecorder、usage_report，支持 per-step token/cost/latency、cache hit/miss、context breakdown、tool efficiency、failed observation evidence。
- Eval and regression：23 个 eval cases，覆盖 context retrieval、tool failure recovery、sandbox/command policy、human approval、repeated tool call、OpenAI-compatible provider failure、WebhookPatchBench 等场景。
- Review gate：deterministic review workflow，用于识别签名校验绕过等高风险 patch。
- Model gateway：支持 MockLLM、DeepSeek、Ollama/company gateway/OpenAI-compatible API。

主验证场景：
WebhookPatchBench，一个小型 webhook service code-repair benchmark。任务是修复 duplicate event_id 导致重复入库/重复 enqueue 的可靠性问题，同时必须保留 signature verification，不读取 secret，不修改 security policy，并通过 unittest。这个场景用于展示 agent 在真实代码修改链路中的 context selection、patch、test、safety、trace、usage 和 review gate。

公开证据：
- GitHub Actions CI：运行 scripts/verify.sh 和 scripts/verify_mcp.sh。
- LICENSE、CONTRIBUTING、SECURITY、RELEASE_CHECKLIST 已补齐。
- README 首屏包含架构图、badge、usage report snapshot。
- docs/run-artifacts/ 下提交了 DeepSeek 真实运行的 trace 和 usage_report。
- docs/open-source-readiness/ 下包含 benchmark summary、ablation notes、Docker sandbox extension plan、provider comparison。

量化证据：
- eval cases：23 个。
- WebhookPatchBench DeepSeek run：5 次 LLM calls，24,018 total tokens，估算成本约 $0.002998，11 次 tool calls，0 failed，最终 unittest 通过。
- Calculator smoke DeepSeek run：7 次 LLM calls，19,393 total tokens，展示了 failed observations 的恢复过程。

边界说明：
这个项目不是完整 IDE/TUI 产品，不声称有真实线上用户、SLA 或大规模流量；它是一个可读、可复现、可扩展的 CodingAgent runtime core。后续扩展方向包括 Docker/remote sandbox backend、远程 MCP gateway、自动化 ablation runner、provider matrix、IDE/TUI 产品层。
```

## Short Project Summary

Agent Forge is a production-style CodingAgent runtime core for studying and
building controlled LLM code-execution systems. It focuses on the runtime
control plane behind coding agents: context construction, model gateway, tool
routing, sandboxed execution, approval hooks, MCP external tools, task state,
trace, usage accounting, regression evals, and review gates.

The main validation scenario is WebhookPatchBench, a realistic webhook service
repair task that requires the agent to fix duplicate event side effects while
preserving signature verification, respecting secret boundaries, running tests,
and producing trace plus cost evidence.

## Suggested Chinese Bullets

- 设计并实现 Agent Forge，一个 production-style CodingAgent runtime core，覆盖 ReAct-style agent loop、上下文构建、工具路由、沙箱执行、审批 hooks、任务 checkpoint 和 trace replay。
- 构建工具治理层：通过 ToolRouter / ToolRegistry 实现 schema validation、命令 allowlist、workspace sandbox、MCP stdio server 集成、offline/live web search 工具，以及 failed Observation 的可恢复闭环。
- 实现可观测和评测体系：输出 per-step token/cost/latency、cache hit/miss、context breakdown、tool efficiency，并维护 23 个 eval cases 覆盖上下文检索、工具失败恢复、安全策略、provider 异常和 WebhookPatchBench。
- 设计 WebhookPatchBench 作为真实代码修复场景，验证 agent 在签名校验、幂等处理、side-effect ordering、secret boundary、unittest validation 和 review gate 下的端到端行为。
- 支持 MockLLM、DeepSeek、Ollama/company gateway/OpenAI-compatible API 等模型后端，并通过 GitHub Actions、release checklist、run artifacts 和 open-source readiness docs 提升项目可复现性和开源可信度。

## Suggested English Bullets

- Designed and implemented Agent Forge, a production-style CodingAgent runtime
  core covering ReAct-style control flow, context construction, tool routing,
  sandboxed execution, approval hooks, task checkpoints, and trace replay.
- Built a governed tool layer with schema validation, command allowlists,
  workspace sandboxing, MCP stdio server integration, offline/live web search
  tools, and failed-observation recovery.
- Added observability and evaluation infrastructure, including per-step
  token/cost/latency reports, cache hit/miss metrics, context breakdowns, tool
  efficiency tracking, 23 eval cases, and committed run artifacts.
- Created WebhookPatchBench, a realistic code-repair benchmark involving webhook
  signature verification, idempotency, side-effect ordering, secret access
  blocking, unittest validation, and deterministic review gates.
- Supported multiple model backends through an OpenAI-compatible gateway,
  including MockLLM for deterministic CI, DeepSeek for low-cost real-model runs,
  and extension paths for Ollama or company model gateways.

## One-Line Version

Built Agent Forge, a production-style CodingAgent runtime core with context
engineering, governed tool execution, MCP external tools, sandbox/approval
policies, trace and usage reporting, WebhookPatchBench, and regression evals.


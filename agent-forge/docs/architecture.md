# Architecture

## Problem

普通 chatbot 只能生成文本，coding agent 需要能读仓库、选择工具、执行动作、观察结果、继续修正，并留下可审计证据。

## Design Goals

- 小而完整：不用重依赖，也能展示 agent control layer。
- 离线可跑：默认 MockLLM，保证 demo/eval 稳定。
- 安全优先：所有读写和命令都经过 sandbox/policy。
- 可观测：每次运行都有 trace JSON 和 summary.md。
- 可评估：eval case 真实执行 verify.py。

## Non-goals

- 不替代 Claude Code / Codex / OpenCode。
- 不实现完整 MCP 协议。
- 不实现完整 LSP server。
- 不声称生产流量、成本节省或线上指标。

## Architecture Overview

```text
User task
  -> input guardrail
  -> context assembly
  -> planner summary
  -> LLM adapter
  -> tool call parser
  -> permission policy
  -> ToolRegistry
  -> tool execution inside workspace sandbox
  -> Observation
  -> loop or final answer
  -> trace + metrics + eval report
```

## Agent Loop

`agent_forge/runtime/agent_loop.py` 显式记录：

- `context_assembly`
- `plan`
- `llm_call`
- `action`
- `permission_check`
- `tool_call`
- `tool_observation`
- `observation`
- `final_answer`

`AgentState` 在 `runtime/state.py` 中描述任务、workspace、iteration、messages、observations、status、final answer 和 stop reason。

## Tool Execution

`ToolRegistry` 负责注册、schema 暴露、缺参校验、未知工具 recovery 和统一异常捕获。工具结果统一为 `Observation`。

内置工具包括：

- `read_file`
- `write_file`
- `list_files`
- `grep`
- `grep_search`
- `apply_patch`
- `run_command`
- `git_status`
- `git_diff`
- `ask_human`

V2 还提供 `MCPStyleToolAdapter`，说明外部工具如何进入本地 ToolRegistry。

## Context Management

Context 由 repo map、retrieved docs、memory、selected files 和 budget report 组成。`symbol_search` 使用 Python AST 找 class/function，`file_ranker` 把相关文件排到前面。

## Safety Boundary

- workspace root 限制；
- 敏感文件拒绝；
- command allowlist/denylist；
- write/patch approval；
- output guardrail 防止未运行测试却声称测试通过。

## Observability

Trace JSON 包含 task、start/end time、events、metrics、stop reason、final answer。`summary.md` 是给人看的运行摘要。

Metrics 至少包括 tool call、failed tool call、handoff、guardrail block、approval、duration。

## Evaluation

`eval_cases/` 当前有 19 个 case。每个 case 有 `task.md` 与 `verify.py`。`eval_runner` 真实执行 verify，并输出 total、passed、failed、pass rate、failed list 和 metrics。

## Trade-offs

- 标准库优先降低运行成本，但没有完整 SDK 特性。
- AST symbol search 比 LSP 简单，但足够作为可测试 fallback。
- 本地 MCP-style adapter 不处理协议 transport，但能说明 tool adapter 设计。
- eval 是 smoke/regression benchmark，不是大规模统计评测。

## Future Work

- 接入真正 LSP provider。
- 加 model gateway：routing、fallback、cost、rate limit。
- 加完整 JSON Schema 校验。
- 加 eval history 趋势。
- 加 GitHub PR bot 的 draft PR 工作流。

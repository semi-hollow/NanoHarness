# 04 Runtime Control And Extension Map

This file replaces the older branch-delta notes. It describes the current
runtime control layer without requiring you to remember historical branch names.

## One Sentence

Agent Forge wraps the ReAct loop with production-style controls: execution
environment, approval hooks, task state, MCP external tools, review gate, trace,
and usage evidence.

## Control Plane Files

| file | role | read first |
|---|---|---|
| `agent_forge/runtime/execution_environment.py` | local/worktree boundary, network policy, protected paths, git-risk checks, manifest. | `ExecutionEnvironmentConfig`, `prepare()`, `validate_command()`, `write_manifest()` |
| `agent_forge/runtime/hooks.py` | pre-tool approval, post-tool redaction, stop audit. | `ApprovalMode`, `PermissionHook`, `ExecutionEnvironmentHook`, `HookManager` |
| `agent_forge/runtime/control.py` | failure classification, repeated-action detection, timeout, cost budget. | `FailureKind`, `StepController` |
| `agent_forge/runtime/task_state.py` | checkpoint, resume seed, trace replay. | `TaskCheckpoint`, `TaskStateStore`, `replay_trace()` |
| `agent_forge/tools/tool_router.py` | reduce the tool catalog per step. | `ToolRouter.route()` |
| `agent_forge/tools/mcp_config.py` | load configured external tools. | `MCPConfigLoader.load_into()` |
| `agent_forge/tools/mcp_stdio.py` | discover/call stdio JSON-RPC tools. | `MCPStdioClient`, `MCPStdioTool` |
| `agent_forge/mcp/server.py` | built-in MCP-style server protocol shell. | `AgentForgeMCPServer` |
| `agent_forge/mcp/web_tools.py` | repo policy, time, web fetch/search MCP tools. | `build_builtin_tools()`, `_web_search()` |
| `agent_forge/workflows/review_workflow.py` | deterministic diff review gate. | `run_review()`, `_analyze_diff()` |

## Runtime Commands

```bash
# Review current git diff.
python run_demo.py --mode review

# Run with stronger local isolation.
python run_demo.py --mode single --execution-env worktree

# Change approval posture.
python run_demo.py --mode single --approval-mode on-risk
python run_demo.py --mode single --approval-mode dry-run

# Inspect task state and trace replay.
python run_demo.py --list-task-states
python run_demo.py --show-task-state <run_id>
python run_demo.py --resume-state <run_id> --mode single
python run_demo.py --replay-run .agent_forge/latest/webhook-deepseek/trace.json

# Verify/load MCP tools.
scripts/verify_mcp.sh
python -m agent_forge.mcp.builtin_server --workspace . --list-tools
python run_demo.py --mcp-config mcp_tools.example.json \
  --mcp-allowed-tool forge.repo_policy \
  "use repo_policy to summarize command rules"
```

## How To Explain The Design

The LLM only proposes actions. The runtime owns all side-effect boundaries:

1. `ContextStrategy` selects evidence and budget.
2. `ModelGateway` normalizes provider responses and usage.
3. `ToolRouter` limits available tool schemas.
4. `HookManager` checks permission, execution environment, and redaction.
5. `ToolRegistry` validates tool names and arguments.
6. `StepController` classifies failures and stops loops.
7. `TraceRecorder` and `usage_report.py` make the run explainable.

This is the core difference between a production-shaped CodingAgent runtime and
a toy prompt script.

## Extension Boundaries

| future capability | existing extension point |
|---|---|
| Docker or remote sandbox | `ExecutionEnvironment` |
| Remote MCP gateway | `MCPConfigLoader` + `MCPStdioClient` interface shape |
| Provider fallback matrix | `ModelGateway` + `ProviderProfile` |
| IDE/TUI surface | CLI/session/task-state APIs |
| Automated ablation runner | trace + usage report + eval runner |

# agent_forge Code Map

Read this file when you want to know why a package exists and what breaks if it
is removed.

## Main Call Chain

```text
agent_forge/forge_cli.py
  -> ui.py for `forge ui` local browser demo
  -> bench/swebench.py for `forge bench swebench`
  -> runtime/agent_loop.py for agent execution
  -> context/* builds prompt context
  -> models/gateway.py calls provider
  -> tools/* executes controlled actions
  -> safety/* enforces boundaries
  -> observability/* writes trace and usage
```

The public entrypoint is `forge` / `python -m agent_forge`; old demo-mode
entrypoints were removed so the code map stays aligned with the benchmark loop.

## Packages

| Package | Purpose | If removed |
| --- | --- | --- |
| `bench/` | Loads SWE-bench cases, prepares repo workspaces, writes predictions and result cards. | The project loses its external effect loop and falls back to anecdotal demos. |
| `runtime/` | Owns AgentLoop, task state, control policy, planning mode, and observations. | Tool calls become scattered and replay/recovery becomes impossible. |
| `context/` | Selects repo files, retrieved docs, symbols, memory, and token budget. | The model either gets noisy full-repo context or misses necessary files. |
| `tools/` | Provides read, grep, patch, command, git, diagnostics, and MCP-style adapters. | The model cannot inspect or modify code through governed actions. |
| `safety/` | Path sandbox, command policy, permissions, and guardrails. | A coding agent can perform unsafe or irrelevant operations. |
| `models/` | Provider gateway, retry/fallback, token/cache/cost telemetry. | Runtime code becomes tied to one API provider and loses cost visibility. |
| `observability/` | Trace, metrics, evidence, and usage reports. | You cannot explain why the agent chose a file, failed a tool, or spent tokens. |
| `mcp/` | Built-in MCP-style stdio tools and external web tool wrappers. | Tool extension demos disappear, but SWE-bench patching can still run. |
| `ui.py` | Local browser control surface for demoing doctor, agent run, SWE-bench sample, report, and replay. | Users must remember CLI commands before they can see the closed loop. |

## Important Classes

| Class | Why it exists |
| --- | --- |
| `BenchCase` | Normalizes SWE-bench rows so runner code is not tied to one dataset schema. |
| `SwebenchWorkspaceManager` | Guarantees each case starts from the official base commit. |
| `BenchRunSummary` | Feeds `results.json` and `report.md` with one shared source of truth. |
| `AgentLoop` | Coordinates context, model, tool calls, observations, recovery, and stop reasons. |
| `ContextBuildReport` | Makes prompt assembly auditable instead of an opaque string. |
| `ModelGateway` | Normalizes provider responses and usage across DeepSeek/OpenAI-compatible/mock clients. |
| `ToolRegistry` | The single list of actions the agent is allowed to request. |
| `WorkspaceSandbox` | Prevents tools from escaping the target repo. |
| `CommandPolicy` | Blocks dangerous shell commands and explains allowed validation commands. |
| `TraceRecorder` | Writes the step-by-step evidence stream. |
| `UiState` | Keeps browser-triggered jobs and outputs in memory for one local demo session. |

## Reading Order

1. `agent_forge/forge_cli.py`
2. `agent_forge/ui.py`
3. `agent_forge/bench/swebench.py`
4. `agent_forge/runtime/agent_loop.py`
5. `agent_forge/context/context_builder.py`
6. `agent_forge/tools/registry.py`
7. `agent_forge/safety/command_policy.py`
8. `agent_forge/observability/usage_report.py`

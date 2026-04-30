# Agent Forge

Agent Forge is a compact Agent Harness for learning and interviewing: agent loop, tool calling, safety, context engineering, observability, eval, and production-readiness design in one standard-library-first Python project.

## Quickstart

```bash
python3.11 run_demo.py --mode single
python3.11 run_demo.py --mode multi
python3.11 run_demo.py --mode workflow
python3.11 -m unittest discover tests
python3.11 -m agent_forge.eval.eval_runner
```

Default demos use `MockLLMClient`, so no API key is required. Optional OpenAI-compatible mode reads `AGENT_FORGE_BASE_URL`, `AGENT_FORGE_API_KEY`, and `AGENT_FORGE_MODEL`.

## V1 / V2 Capability Matrix

| Area | V1 MVP | V2 |
| --- | --- | --- |
| LLM | MockLLMClient | Optional OpenAI-compatible client, tool call parsing, invalid response errors |
| Agent loop | Single-agent loop | Preserved and documented for deeper interview discussion |
| Multi-agent | Supervisor handoff demo | Eval and trace metrics make handoff explainable |
| Workflow | Deterministic workflow mode | Preserved as workflow-vs-agent contrast |
| Tools | read/write/patch/grep/run/git/ask_human | MCP-style local tool adapter |
| Context | repo map + keyword retrieval | symbol_search, file_ranker, budget report |
| Safety | guardrail, sandbox, permission | safety eval cases for secret/network/false claim |
| Observability | JSON trace | metrics summary from trace JSON |
| Eval | 6 cases | 16 cases, real verify.py execution, pass-rate report |
| Docs | learning notes | production readiness, LSP, MCP adapter, resume/project scripts |

## Architecture

```mermaid
flowchart LR
    User["User task"] --> Guardrail["Input guardrail"]
    Guardrail --> Loop["AgentLoop"]
    Loop --> LLM["MockLLM or OpenAI-compatible client"]
    LLM --> Parser["ToolCall parser"]
    Parser --> Policy["Permission policy"]
    Policy --> Registry["ToolRegistry"]
    Registry --> Tools["Built-in tools + MCP-style adapters"]
    Tools --> Sandbox["Workspace sandbox"]
    Tools --> Obs["Observation"]
    Obs --> Loop
    Loop --> Trace["Trace JSON"]
    Trace --> Metrics["Metrics summary"]
    Metrics --> Eval["Eval report"]
```

## Eval

The benchmark currently has 16 cases. Each case includes:

- `task.md`
- `verify.py`

`eval_runner` executes each `verify.py` and writes `eval_report.md` with total, passed, failed, pass rate, failed case list, and metrics.

## Learning Route

1. Agent loop: `docs/01-agent-loop.md`
2. Tool calling: `docs/03-tool-calling.md`
3. Safety: `docs/08-permission-and-sandbox.md`, `docs/09-guardrails-and-human-approval.md`
4. Context V2: `docs/06-context-engineering.md`, `docs/19-lsp-and-symbol-search.md`
5. Observability/eval: `docs/10-observability-and-tracing.md`, `docs/11-evaluation.md`
6. Production: `docs/12-production-readiness.md`

## Interview Route

1. Start with `docs/20-resume-bullet-and-project-script.md`.
2. Use `docs/17-architecture-whiteboard.md` for the whiteboard story.
3. Practice `docs/14-interview-qa.md` for 80+ follow-up questions.
4. Run the commands in Quickstart during the demo.

## Current Boundaries

- The OpenAI-compatible client is intentionally small and SDK-free.
- The MCP-style adapter is not a complete MCP protocol implementation.
- `symbol_search` uses Python AST, not a real LSP server.
- The benchmark is local and deterministic; it does not claim production traffic metrics.
- Trace metrics summarize local runs and are not a full telemetry backend.

## Roadmap

- Add an LSP-backed `SymbolProvider`.
- Add a model gateway abstraction with routing, fallback, cost tracking, and rate limits.
- Add GitHub PR bot integration with draft PR workflow.
- Store eval history over time for regression trends.
- Add richer typed tool schemas and argument validation.

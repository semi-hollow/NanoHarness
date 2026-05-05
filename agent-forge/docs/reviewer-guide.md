# Reviewer Guide

This guide is for classmates, interview reviewers, or a future Codex conversation reviewing Agent Forge.

## 1. What This Project Is

Agent Forge is a compact Agent Harness. It is designed to show how a coding agent is controlled, observed, evaluated, and constrained.

It is not:

- a model training project;
- a Claude Code clone;
- an OpenCode config pack;
- a production SaaS service.

The project source of truth is `00-项目原始设计方案-source-of-truth.md`.

## 2. Five-Minute Review Path

1. Read `README.md`.
2. Read `00-项目原始设计方案-source-of-truth.md`.
3. Run `scripts/verify.sh`.
4. Inspect `agent_forge/runtime/agent_loop.py`.
5. Inspect `agent_forge/tools/registry.py`.
6. Inspect `agent_forge/safety/`.
7. Inspect `agent_forge/eval/eval_runner.py`.
8. Skim `docs/capability-evidence-map.md`.

## 3. How to Run

From `agent-forge/`:

```bash
scripts/verify.sh
```

The script uses `python3.11` and runs:

- Python compile check;
- single-agent demo;
- multi-agent demo;
- workflow demo;
- unittest suite;
- eval benchmark.

No API key is required because the default demos use `MockLLMClient`.

## 4. What to Review

### Agent Runtime

Main file:

- `agent_forge/runtime/agent_loop.py`

Look for:

- context assembly before LLM calls;
- tool call parsing;
- permission checks before tool execution;
- observations appended back into the loop;
- repeated tool-call / max-step protection;
- final answer handling.

### Tools

Main files:

- `agent_forge/tools/base.py`
- `agent_forge/tools/registry.py`
- `agent_forge/tools/adapters/mcp_style_adapter.py`

Look for:

- tool schema shape;
- unknown tool handling;
- invalid argument handling;
- conversion from MCP-style mock external tools;
- `Observation` returned from execution.

### Safety

Main files:

- `agent_forge/safety/sandbox.py`
- `agent_forge/safety/permission.py`
- `agent_forge/safety/command_policy.py`
- `agent_forge/safety/guardrails.py`

Look for:

- workspace boundary checks;
- secret-file blocking;
- dangerous command blocking;
- approval path for write operations;
- output guardrail against false test claims.

### Context

Main files:

- `agent_forge/context/context_builder.py`
- `agent_forge/context/file_ranker.py`
- `agent_forge/context/symbol_search.py`
- `agent_forge/context/memory.py`

Look for:

- repo map;
- retrieved docs;
- memory summary;
- selected files;
- budget report;
- source/test files ranked above trace/docs for code tasks.

### Observability and Eval

Main files:

- `agent_forge/observability/trace.py`
- `agent_forge/observability/metrics.py`
- `agent_forge/eval/eval_runner.py`
- `agent_forge/eval/report.py`

Look for:

- trace event structure;
- metrics derived from trace JSON;
- real execution of each eval `verify.py`;
- total/passed/failed/pass-rate reporting.

## 5. Evidence to Check

Useful evidence files:

- `docs/run-results.md`
- `docs/capability-evidence-map.md`
- `eval_cases/*/task.md`
- `eval_cases/*/verify.py`

Generated local artifacts:

- `eval_report.md`
- `agent_forge_trace.json`
- `*_trace.json`
- `summary.md`

These generated files are ignored by git and can be regenerated with `scripts/verify.sh`.

## 6. Known Boundaries

The project is intentionally small and standard-library-first.

Current boundaries:

- default LLM is `MockLLMClient`;
- OpenAI-compatible mode is optional and minimal;
- MCP-style adapter is not a full MCP protocol implementation;
- `symbol_search` is not full LSP;
- RAG is keyword-based, not vector search;
- sandbox is workspace/path/command-policy based, not container isolation;
- eval cases prove local harness behavior, not production business impact.

These boundaries are acceptable for an interview-oriented Agent Harness as long as they are stated clearly.

## 7. Suggested Review Questions

1. Does the agent ever execute a tool without permission checks?
2. Can a bad model response crash the loop, or does it become a structured error?
3. Does the project distinguish demo success from eval success?
4. Are trace events enough to debug a failed tool call?
5. Are generated artifacts kept out of commits?
6. Are the README and docs honest about the current boundaries?

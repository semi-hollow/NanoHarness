# Codex V2 Change Archive

This file records the project changes and verification history from the Codex conversation that upgraded Agent Forge from a runnable V1 MVP toward a V2 interview-ready Agent Harness.

## 1. Source Reference

The original product/design target has been persisted in this repository:

- `docs/references/Harness设计方案.md`

That file should be treated as the long-term product reference for Agent Forge. Future work should compare implementation gaps against it instead of relying on chat history.

## 2. User Goal

The requested product is not a chatbot, not a Claude clone, and not an OpenCode config package.

The target is a compact but complete Agent Engineering Lab / Agent Harness that can support:

- runnable coding-agent demos;
- single-agent runtime;
- multi-agent supervisor/subagent flow;
- workflow vs dynamic agent comparison;
- tool calling and observations;
- context engineering;
- memory and simplified RAG;
- permission, sandbox, guardrails, and human approval;
- trace, metrics, and eval benchmark;
- production-readiness design;
- interview Q&A, resume material, tutorials, and whiteboard material.

The project must avoid fake online business metrics. Evidence should come from local eval cases, tests, traces, and safety cases.

## 3. Major V2 Additions

### OpenAI-Compatible LLM Client

- Added optional OpenAI-compatible client while preserving `MockLLMClient`.
- Reads `AGENT_FORGE_BASE_URL`, `AGENT_FORGE_API_KEY`, and `AGENT_FORGE_MODEL`.
- Uses Python standard library.
- Does not affect demos when env vars are absent.
- Parses common tool-call responses.
- Returns structured invalid-response errors.
- Covered by tests and eval case `case_016_openai_client_invalid_response_handling`.

### Context Engineering V2

- Added `symbol_search`.
- Added `file_ranker`.
- Added context budget report with repo map, retrieved docs, memory, selected files, total chars, and truncated status.
- Agent loop now injects the context report into LLM messages.
- Runtime memory now records task and observations.
- Added tests and tutorials for the context path.

### MCP-Style Tool Adapter

- Added `agent_forge/tools/adapters/mcp_style_adapter.py`.
- Defined a local `ToolAdapter` abstraction.
- Defined `MCPStyleToolSpec`.
- Supports converting mock external tools into Agent Forge tool schemas.
- Can register adapted tools in `ToolRegistry` and execute them as normal tools returning `Observation`.
- Documented clearly as MCP-style local adapter design, not the full MCP protocol.

### LSP / Symbol Search Documentation

- Added `docs/19-lsp-and-symbol-search.md`.
- Explains grep vs `symbol_search` vs LSP.
- Documents why V2 implements lightweight symbol search first and how LSP could be connected later.

### Eval Benchmark Expansion

- Eval benchmark expanded to 19 cases.
- Each eval case has `task.md` and `verify.py`.
- `eval_runner` executes `verify.py` for real instead of hardcoding success.
- Report includes total, passed, failed, pass rate, and failed case list.
- New coverage includes unknown tool recovery, invalid tool args, false test claim guardrail, secret file block, network command block, context ranking, symbol search, workflow mode, tool adapter execution, OpenAI-compatible invalid response handling, external path block, repeated tool-call block, and human approval rejection.

### Observability

- Added metrics summary from trace JSON.
- Metrics include tool calls, failed tool calls, handoffs, guardrail blocks, approvals, duration, step count, and trace event count.
- `eval_report.md` includes metrics columns.

### Production Readiness

- Expanded `docs/12-production-readiness.md`.
- Covered local developer, CI runner, internal server, and GitHub PR bot modes.
- Covered model gateway, auth, rate limit, routing, fallback, audit, cost, permission, sandbox, rollback, incident, and rollout.

### Interview Material

- Expanded `docs/14-interview-qa.md` to 80 Q&A entries.
- Added real LLM, tool-call parse failure, MCP, LSP, context budget, eval metrics, CI runner, model gateway, cost, rate limit, incident, framework comparison, and resume storytelling topics.
- Added `docs/20-resume-bullet-and-project-script.md` with Chinese resume material, English resume bullet, 1-minute and 3-minute scripts, STAR version, follow-up checklist, and whiteboard narration.

### README

- README now includes V1/V2 capability matrix, quickstart, eval case count, architecture diagram, learning route, interview route, current boundaries, and roadmap.

### CI

- Added a GitHub Actions workflow at `.github/workflows/agent-forge-ci.yml` in the parent repository.
- The workflow enters `agent-forge`, uses Python 3.11, and runs compile, demos, unit tests, and eval.
- Remote CI result still needs to be verified after pushing.

## 4. Latest Gap Found and Fixed

During the final comparison against `Harness设计方案.md`, unit tests were green but `eval_runner` showed one failing case:

- `case_012_context_retrieval_ranks_correct_file`

Root cause:

- `file_ranker` could rank trace, report, docs, or eval files above the actual source file because the scorer treated all text matches too similarly.

Fix:

- Updated `agent_forge/context/file_ranker.py`.
- Added code-task detection.
- Boosted source/test Python files and exact stem matches.
- Penalized docs, JSON traces, eval case files, and generated reports for code-oriented queries.

Result:

- `examples/demo_repo/src/calculator.py` now ranks near the top for `fix calculator add`.
- Eval recovered to 19/19.

## 5. Verification Results

The following commands were run locally with `python3.11` where applicable:

```bash
python3.11 run_demo.py --mode single
python3.11 run_demo.py --mode multi
python3.11 run_demo.py --mode workflow
python3.11 -m unittest discover tests
python3.11 -m agent_forge.eval.eval_runner
python3.11 - <<'PY'
from pathlib import Path
import py_compile
for p in Path(".").rglob("*.py"):
    py_compile.compile(str(p), doraise=True)
print("py_compile passed")
PY
```

Observed result:

- single demo: passed
- multi demo: passed
- workflow demo: passed
- unit tests: 44 tests passed
- eval benchmark: 19 total, 19 passed, 0 failed, 100.0% pass rate
- py_compile: passed

## 6. Current Honest Status

Agent Forge now satisfies the core goal of the original design:

- runnable;
- learnable;
- interview-deep;
- traceable;
- safety-aware;
- eval-backed;
- documented with tutorials and interview material.

It should be described as an Agent Harness / Agent Engineering Lab, not as a production replacement for Claude Code, OpenCode, LangGraph, or a full internal coding-agent platform.

## 7. Known Boundaries to Say Out Loud

- `MockLLMClient` is the default path; real model integration is optional.
- OpenAI-compatible client exists, but real provider behavior should be validated with actual model credentials.
- MCP-style adapter is a local adapter pattern, not a complete MCP protocol implementation.
- `symbol_search` is a lightweight Python source scanner, not a full LSP integration.
- RAG is keyword-based, not vector-search based.
- Sandbox is workspace/path/command-policy based, not container or VM isolation.
- CI workflow exists locally, but remote GitHub Actions status should be checked after push.
- Eval cases prove local harness behavior, not online business impact.

## 8. Recommended Next Conversation Starting Point

If continuing this project in a new Codex conversation, start from:

1. Read `docs/references/Harness设计方案.md`.
2. Read `docs/references/codex-v2-change-archive.md`.
3. Run the verification commands above with `python3.11`.
4. Inspect `docs/capability-evidence-map.md`.
5. Then decide whether to work on real-model smoke tests, LSP integration, stronger sandboxing, or CI/PR-bot workflows.

# Capability Evidence Map

| Capability | Code Evidence | Test Evidence | Eval Case | Current Boundary | Next Enhancement |
|---|---|---|---|---|---|
| Agent Loop | `agent_forge/runtime/agent_loop.py`, `state.py`, `stop_condition.py` | `tests/test_agent_loop.py`, `tests/test_memory_loop.py` | `case_001_single_agent_fix_test` | MockLLM-driven harness demo by default | Stronger planner and real-model regression suite |
| Tool Calling | `agent_forge/tools/registry.py`, `tools/*.py` | `tests/test_tools.py` | `case_007_unknown_tool_recovery`, `case_008_invalid_tool_arguments_recovery` | Lightweight schema validation, not full JSON Schema | Add typed schema validation |
| Observation | `agent_forge/runtime/observation.py` | `tests/test_agent_loop.py`, `tests/test_memory_loop.py` | `case_006_patch_failure_recovery` | Observation has minimal fields | Add metadata such as changed files and exit code |
| Workflow vs Agent | `agent_forge/workflows/coding_workflow.py`, `runtime/agent_loop.py` | `tests/test_multi_agent.py`, `tests/test_agent_loop.py` | `case_014_workflow_mode_success` | Workflow is deterministic demo | Add failure branches and persistence |
| Supervisor / Subagent | `agent_forge/agents/supervisor_agent.py`, `supervisor_policy.py` | `tests/test_supervisor_policy.py`, `tests/test_multi_agent.py` | `case_002_multi_agent_review_then_fix` | Rule-based supervisor/subagent demo | Dynamic routing based on richer state |
| Handoff | `agent_forge/agents/handoff.py`, `supervisor_agent.py` | `tests/test_handoff.py` | `case_002_multi_agent_review_then_fix` | Payload schema is dict-based | Add typed `HandoffPayload` |
| Context Engineering | `agent_forge/context/context_builder.py`, `file_ranker.py` | `tests/test_context.py`, `tests/test_agent_loop.py` | `case_004_context_retrieval`, `case_012_context_retrieval_ranks_correct_file` | Character budget, not tokenizer budget | Tokenizer-aware budgeting and summarization |
| Memory | `agent_forge/context/memory.py`, `runtime/agent_loop.py` | `tests/test_memory_loop.py`, `tests/test_context.py` | Covered through loop tests, no dedicated eval case | Run-level memory only; no persistence | Project-scoped memory with retention policy |
| RAG | `agent_forge/context/rag.py` | `tests/test_context.py` | `case_004_context_retrieval` | Keyword retrieval, not vector search | Hybrid BM25/vector retrieval |
| Permission | `agent_forge/safety/permission.py` | `tests/test_permission.py` | `case_019_human_approval_rejected` | Local policy only | External policy engine / approval service |
| Sandbox | `agent_forge/safety/sandbox.py` | `tests/test_sandbox.py` | `case_010_sandbox_blocks_secret_file`, `case_017_external_path_blocked` | Workspace-level sandbox, not OS/container isolation | Container sandbox and read-only mounts |
| Guardrails | `agent_forge/safety/guardrails.py` | `tests/test_guardrails.py` | `case_009_output_guardrail_false_test_claim`, `case_018_repeated_tool_call_blocked` | Rule-based guardrails | Policy engine and classifier-based checks |
| Human Approval | `agent_forge/tools/ask_human.py`, `tools/apply_patch.py`, `runtime/agent_loop.py` | `tests/test_human_approval.py` | `case_005_human_approval_required`, `case_019_human_approval_rejected` | Mock approval only | Web approval queue and audit UI |
| Tracing | `agent_forge/observability/trace.py`, `summary.py` | `tests/test_trace.py`, `tests/test_agent_loop.py` | `case_001_single_agent_fix_test`, `case_002_multi_agent_review_then_fix` | Local JSON trace, not telemetry backend | OpenTelemetry/export backend |
| Metrics | `agent_forge/observability/metrics.py` | `tests/test_observability_metrics.py`, `tests/test_trace.py` | All trace-producing eval cases | Local summary metrics | Historical dashboard and trend comparison |
| Eval | `agent_forge/eval/eval_runner.py`, `eval_case.py` | `tests/test_eval_runner.py` | All `eval_cases/case_*` | Smoke/regression benchmark, not large-scale benchmark | Model comparison and eval history |
| OpenAI-compatible Client | `agent_forge/runtime/llm_client.py` | `tests/test_openai_compatible_llm.py` | `case_016_openai_client_invalid_response_handling` | MVP client; provider schemas may differ | Model gateway with routing/fallback/cost |
| MCP-style Adapter | `agent_forge/tools/adapters/mcp_style_adapter.py` | `tests/test_tool_adapter.py` | `case_015_tool_adapter_mock_execution` | Local adapter only, not full MCP transport/session | JSON-RPC transport and capability negotiation |
| Symbol Search | `agent_forge/context/symbol_search.py` | `tests/test_context.py` | `case_013_symbol_search_finds_function` | AST-based MVP, not full LSP | LSP-backed symbol provider |
| Production Readiness | `agent_forge/production/*.py`, `docs/12-production-readiness.md` | Documentation and risk registry checks only | Roadmap only | Design-level, not deployed service | CI runner, PR bot, gateway, audit store |
| CI | `.github/workflows/agent-forge-ci.yml` | GitHub Actions after push | Roadmap/CI evidence after push | Workflow added; result must be verified in GitHub Actions | Add badges and artifact upload |
| README / Tutorials | `README.md`, `tutorials/*.md` | Docs reviewed through repository checks | Roadmap only | Learning docs, not executable behavior | Add docs lint/checklist |
| Interview Q&A | `docs/14-interview-qa.md` | Documentation evidence | Roadmap only | Interview prep material | Add scenario-specific drills |
| Project Deep Dive | `docs/15-project-deep-dive-playbook.md`, `docs/20-resume-bullet-and-project-script.md` | Documentation evidence | Roadmap only | Narrative material, not runtime behavior | Keep synced with run-results |
| Four-layer Followups | `docs/16-four-layer-followups.md` | Documentation evidence | Roadmap only | Interview prep material | Add mock interview script |

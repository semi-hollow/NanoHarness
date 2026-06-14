# Benchmark Summary

Agent Forge uses a local regression suite rather than a public benchmark clone.
The goal is to cover runtime failure modes that matter for CodingAgent systems:
context selection, tool governance, sandboxing, recovery, eval traceability, and
the WebhookPatchBench validation scenario.

Run:

```bash
scripts/verify.sh
```

Latest local verification: `scripts/verify.sh` passed on 2026-06-14.

The eval runner writes `.agent_forge/eval_report.md`. This generated report is
ignored by Git so local runs do not churn the repository; committed reference
artifacts live under `docs/run-artifacts/`.

## Coverage Table

| case | capability | scenario | primary signal |
|---|---|---|---|
| `case_001_single_agent_fix_test` | single-agent repair | Calculator bug fix smoke path. | AgentLoop can patch and validate a tiny repo. |
| `case_002_multi_agent_review_then_fix` | multi-agent orchestration | Supervisor routes plan/code/test/review and retry. | Handoff and retest flow stays coherent. |
| `case_003_dangerous_command_blocked` | command safety | Dangerous shell command should not execute. | Command policy blocks risky action. |
| `case_004_context_retrieval` | context retrieval | Relevant files are retrieved for a coding task. | Context builder returns useful evidence. |
| `case_005_human_approval_required` | approval hooks | Write action requires approval. | Human approval event is recorded. |
| `case_006_patch_failure_recovery` | recovery | Patch application initially fails. | Failed observation becomes recoverable signal. |
| `case_007_unknown_tool_recovery` | tool failure | Model asks for unknown tool. | Registry returns failed Observation, not crash. |
| `case_008_invalid_tool_arguments_recovery` | schema validation | Tool args miss required fields. | Validation error is structured. |
| `case_009_output_guardrail_false_test_claim` | output safety | Final answer claims tests passed without evidence. | Output guardrail blocks false test claim. |
| `case_010_sandbox_blocks_secret_file` | secret boundary | Secret path read attempt. | Workspace sandbox blocks access. |
| `case_011_command_policy_blocks_network_command` | network policy | Network command such as curl. | Command policy denies command. |
| `case_012_context_retrieval_ranks_correct_file` | file ranking | Calculator task should rank calculator source. | Relevant source appears near top. |
| `case_013_symbol_search_finds_function` | symbol search | Locate `add` function. | Symbol search finds target function. |
| `case_014_workflow_mode_success` | deterministic workflow | Non-agent baseline. | Workflow reaches success state. |
| `case_015_tool_adapter_mock_execution` | MCP-style adapter | Config-driven mock external tool. | Tool registers and executes. |
| `case_016_openai_client_invalid_response_handling` | provider robustness | Malformed OpenAI-compatible response. | Client returns structured invalid response. |
| `case_017_external_path_blocked` | path sandbox | Read outside workspace. | Sandbox rejects external path. |
| `case_018_repeated_tool_call_blocked` | loop control | Repeated identical tool call. | StepController prevents loop. |
| `case_019_human_approval_rejected` | approval rejection | Auto approval disabled. | Write does not execute. |
| `case_020_webhook_idempotency_fix` | realistic code repair | Duplicate webhook delivery bug. | Patch preserves signature verification and tests pass. |
| `case_021_webhook_context_selection` | realistic context | Identify relevant files for webhook bug. | Context selects issue, handler, tests, docs. |
| `case_022_webhook_secret_access_blocked` | realistic security | Webhook task with secret boundary. | Secret values are not read or leaked. |
| `case_023_webhook_reviewer_rejects_signature_bypass` | review gate | Risky signature-bypass patch. | Reviewer rejects unsafe change. |

## Capability Groups

| group | cases | why it matters |
|---|---|---|
| Agent loop and recovery | 001, 006, 007, 008, 018 | Proves the runtime can recover from common model/tool failures. |
| Context engineering | 004, 012, 013, 021 | Proves retrieval and symbol/file ranking are tested directly. |
| Safety and permissions | 003, 005, 009, 010, 011, 017, 019, 022 | Proves high-risk actions become controlled observations. |
| External tools and provider gateway | 015, 016 | Proves adapter/protocol and provider failure paths. |
| Multi-agent and workflow | 002, 014 | Proves supervised orchestration and deterministic baseline exist. |
| Realistic benchmark | 020, 021, 022, 023 | Proves behavior on WebhookPatchBench, not only a toy calculator fixture. |

## What This Does Not Claim

- It is not a leaderboard benchmark.
- It does not measure solve rate across thousands of repositories.
- It does not claim production traffic or SLA.
- It does not replace provider-specific model evals.

The suite is a regression harness for the runtime control plane. Its value is
that every case maps to a concrete failure mode and can run locally without API
keys.

# Capability Reality Matrix

This document separates implemented runtime behavior from lightweight
primitives, demos, and intentionally scoped boundaries.

The goal is simple: **do not overclaim**. Agent Forge is strongest when it is
presented as a compact AI agent runtime and evaluation harness, not as a full
IDE, SaaS product, distributed swarm, or benchmark leaderboard.

## Legend

| Status | Meaning |
| --- | --- |
| Green | Implemented in the main runtime path and covered by tests or real smoke checks. |
| Yellow | Working lightweight primitive or evaluation contract, but not a full product subsystem. |
| Red | Do not present as a production capability. Use only as a demo/helper/testing boundary. |

## Matrix

| Capability | Status | What is real | What not to claim | Primary files |
| --- | --- | --- | --- | --- |
| Agent runtime loop | Green | `AgentLoop` builds context, calls the model, routes tools, records observations, updates checkpoints, and writes trace events. | Do not call it a full IDE agent product. | `agent_forge/runtime/agent_loop.py` |
| OpenAI-compatible model calls | Green | Real HTTP chat-completions client, normalized tool calls, retry/fallback wrapper, usage telemetry. | Do not claim broad provider SDK support beyond OpenAI-compatible APIs. | `agent_forge/runtime/llm_client.py`, `agent_forge/models/gateway.py` |
| Tool governance | Green | Tools pass through routing, registry validation, permission hooks, command policy, and sandbox checks. | Do not claim prompt-only safety or OS-level isolation. | `agent_forge/tools/`, `agent_forge/safety/`, `agent_forge/runtime/hooks.py` |
| Workspace sandbox | Green | Paths are resolved against the configured workspace and symlink escapes are blocked. | Do not claim container-grade isolation in local mode. | `agent_forge/safety/sandbox.py` |
| Execution environment | Green | `forge run` and `forge bench swebench` support local, detached-worktree, and OCI-container modes. OCI commands run over an isolated snapshot with network, CPU, memory, PID, capability, and read-only-root controls; manifests retain image and command evidence. | OCI mode requires an external Docker-compatible runtime plus a task-compatible image and is not hostile multi-tenant isolation. Host file tools remain bounded by `WorkspaceSandbox` over the mounted snapshot. | `agent_forge/runtime/execution_environment.py`, `agent_forge/forge_cli.py`, `agent_forge/bench/swebench.py`, `agent_forge/tools/run_command.py`, `agent_forge/tools/diagnostics.py` |
| Human approval for side effects | Green | Write-like actions can stop before execution, persist approval files, and resume only after `forge approve`. | Do not confuse this with the synthetic `ask_human` tool. | `agent_forge/runtime/approval.py`, `agent_forge/forge_cli.py` |
| Stale approval detection | Green | Approval stores operation fingerprints; target drift marks the approval stale before execution. | Do not claim it prevents every race in distributed systems. | `agent_forge/runtime/approval.py`, `agent_forge/runtime/agent_loop.py` |
| Operation ledger | Green | Side effects get stable operation keys, pre/post fingerprints, duplicate-skip behavior, and stale-target detection. | Do not call it a distributed transaction log. | `agent_forge/runtime/operation_ledger.py` |
| Checkpoint resume | Green | Checkpoints seed continuation context and `forge resume` writes report-visible resume-chain artifacts. | Do not claim hidden model state or full process memory is restored. | `agent_forge/runtime/task_state.py`, `agent_forge/forge_cli.py` |
| SWE-bench-shaped runner | Green | Loads cases, checks out base commits, runs the agent, writes patches and `predictions.jsonl`. | Do not claim official resolved rate unless official harness evaluation was run and parsed. | `agent_forge/bench/swebench.py` |
| Official SWE-bench per-case evaluation | Green | `--evaluate` runs in the benchmark output directory and parses aggregate/per-case JSON into resolved, unresolved, error, empty-patch, and incomplete statuses. | Process exit code alone never proves resolution; do not claim a resolved rate when the official denominator is empty. | `agent_forge/bench/swebench.py`, `agent_forge/bench/official_results.py` |
| Direct baseline | Green | Calls the same model without tools and extracts a diff for comparison. | Do not call it a competitive baseline; it is intentionally weaker. | `agent_forge/bench/swebench.py` |
| Quantitative scorecard | Green | Every benchmark writes per-case and aggregate patch/local/official evidence plus token, cost, latency, tool-failure, and taxonomy metrics with explicit denominators. | Patch rate is not correctness; local verification is not official resolution. | `agent_forge/evaluation/scorecard.py`, `agent_forge/bench/report.py` |
| Paired runtime ablation | Green | `forge eval ablation` compares matched scorecards and rejects dataset, split, provider/model, or case-set drift. Tool routing exposes a real `all` vs `task-aware` runtime factor. | One run per variant does not estimate stochastic variance or prove a global improvement. | `agent_forge/evaluation/experiment.py`, `agent_forge/tools/tool_router.py`, `agent_forge/forge_cli.py` |
| Multi-agent coordinator | Green | Sequential Implementer/Reviewer/Verifier workflow reuses `AgentLoop` and exchanges artifacts. | Do not call it a peer-to-peer swarm or distributed multi-agent runtime. | `agent_forge/multi_agent/coordinator.py` |
| Artifact handoff | Green | Role outputs are persisted and handed to later roles through explicit artifact context. | Do not imply agents share hidden memory. | `agent_forge/multi_agent/artifacts.py` |
| Subagent fanout | Yellow | Dependency-aware batching uses `ThreadPoolExecutor` and detects static/dynamic write conflicts. | Do not claim it is wired into live AgentLoop workers yet. | `agent_forge/multi_agent/fanout.py` |
| Mini-cases | Yellow | Small deterministic scorecards evaluate explicit evidence for research/ops scenarios. | Do not call them benchmarks or proof of general agent ability. | `agent_forge/evaluation/mini_cases.py`, `docs/evaluation/mini-cases/` |
| Local browser workbench | Yellow | Runs CLI commands, displays latest artifacts, renders trace/usage/evidence views. | Do not call it a production web app or hosted SaaS. | `agent_forge/ui.py` |
| MCP stdio subset | Green | Starts a subprocess, discovers tools, calls tools over JSON-RPC, normalizes content blocks. | Do not claim full MCP SDK compatibility. | `agent_forge/tools/mcp_stdio.py`, `agent_forge/mcp/server.py` |
| MCP-style local adapter | Yellow | Converts local MCP-like specs into local tools. | Do not call this the full MCP protocol. | `agent_forge/tools/adapters/mcp_style_adapter.py` |
| Synthetic ask-human tool | Red | Returns synthetic approval/needs-approval observations for controlled traces and mini-case scenarios. | Do not present it as the real HITL system. Use `ApprovalStore` for real side-effect approval. | `agent_forge/tools/ask_human.py` |
| Skills layer | Green | Built-in/custom Skills affect prompts, tool routing, and trace metadata. | Do not claim a marketplace or remote skill distribution system. | `agent_forge/skills/` |
| Human feedback capture | Green | `forge eval feedback` stores accepted, needs-work, or rejected outcomes with labels and notes next to run evidence. | Do not call a human label an official benchmark result. | `agent_forge/evaluation/feedback_dataset.py`, `agent_forge/forge_cli.py` |
| Evidence dataset export | Green | `forge eval export-dataset` joins trace, selected context, tool policy, environment, failure class, evaluation status, and human feedback into JSONL. Patch content is opt-in. | Do not call exported evidence production training data without curation, privacy review, and dataset governance. | `agent_forge/evaluation/feedback_dataset.py` |

## Recommended Positioning

Use this:

> Agent Forge is a compact AI agent runtime control plane. The hard parts are
> not the UI or prompt surface; they are context selection, governed tools,
> approval, resume safety, traceability, and evaluation evidence.

Avoid this:

> This is a production Claude Code replacement with distributed multi-agent
> execution and official SWE-bench solved rate.

## Current Highest-Leverage Next Steps

1. Repeat matched ablations across multiple seeds and publish only artifact-backed
   results with official denominators.
2. Wire `SubagentTask` fanout into real read-only AgentLoop worker runs for one
   narrow profile.
3. Add privacy filters and dataset version manifests before using exported run
   evidence in a real training pipeline.
4. Add a real Docker/Podman smoke job while keeping unit tests independent of an
   installed container runtime.

# Project Readiness

This is the single public-readiness file for Agent Forge. It keeps benchmark,
provider, ablation, and sandbox maturity notes in one place. Project narrative
and technical Q&A live separately under `docs/technical-defense/`.

## Positioning

Agent Forge is a production-style CodingAgent runtime core. It is not a full
IDE product, hosted cloud service, or model-training project. Its scope is the
runtime control plane behind coding agents:

- context engineering
- model gateway and usage telemetry
- tool routing and schema validation
- sandbox, permission, hooks, and command policy
- task state, trace, replay, and rollback
- MCP external tools
- review gate and local regression evals

The strongest validation scenario is WebhookPatchBench under
`examples/webhook_service_repo/`.

## Benchmark Summary

Run:

```bash
scripts/verify.sh
scripts/verify_mcp.sh
local_scripts/run_webhook_deepseek.sh
```

The local eval cases cover these capability groups:

| group | evidence | why it matters |
|---|---|---|
| Agent loop and recovery | cases 001, 006, 007, 008, 018 | Bad model/tool outputs become observations and recovery decisions instead of crashes. |
| Context engineering | cases 004, 012, 013, 021 | The runtime selects relevant files, symbols, and docs rather than dumping the whole repo. |
| Safety and permissions | cases 003, 005, 009, 010, 011, 017, 019, 022 | Risky actions are blocked or routed through approval and trace. |
| External tools and provider gateway | cases 015, 016, MCP verification | External tools and malformed provider responses are tested at the boundary. |
| Multi-agent and workflow | cases 002, 014 | Supervisor and deterministic workflow paths are testable separately from the single loop. |
| WebhookPatchBench | cases 020-023 | A realistic code repair task exercises security, idempotency, tests, and review. |

What this does not claim:

- no public leaderboard solve rate
- no production traffic or SLA
- no container/remote sandbox as the default backend
- no IDE/TUI product surface

## Provider Comparison

| provider mode | command | when to use |
|---|---|---|
| MockLLM | `scripts/verify.sh` | CI, company machine, offline deterministic checks. |
| DeepSeek | `local_scripts/run_webhook_deepseek.sh` | Personal Mac real-model study runs. |
| Ollama/company/OpenAI-compatible | `python run_demo.py --llm openai --base-url ... --model ...` | Local models or internal gateways. |
| MCP web tools | `--mcp-config mcp_tools.example.json` plus explicit network env | External lookup behind the same tool boundary. |

Committed study snapshots live under `docs/run-artifacts/`. Read
`usage_report.md` first; open `trace.json` only when exact event evidence is
needed.

## Ablation Ideas

These are intentionally lightweight so they stay readable:

| ablation | command shape | expected signal |
|---|---|---|
| Lower context budget | `python run_demo.py --mode single --max-context-chars 1500` | More missing context, different selected files, higher recovery pressure. |
| Dry-run mode | `python run_demo.py --mode single --approval-mode dry-run` | Writes and commands are denied before execution. |
| Locked mode | `python run_demo.py --mode single --approval-mode locked` | Side effects are blocked; final answer should explain the block. |
| Worktree execution | `python run_demo.py --mode single --execution-env worktree` | Active workspace changes; main checkout remains protected. |
| Remove MCP config | Run without `--mcp-config`. | Tool schema count drops; MCP discovery events disappear. |

The next maturity step would be an automated ablation runner that writes:

```text
.agent_forge/ablations/<timestamp>/
  baseline_trace.json
  ablated_trace.json
  comparison.md
```

## Sandbox Roadmap

Current boundary:

| mode | behavior |
|---|---|
| `local` | Path checks, command policy, approval hooks in the current checkout. |
| `worktree` | Git worktree isolation from `HEAD`, plus the same policy hooks. |

Production extension:

```text
ExecutionEnvironment
  LocalExecutionBackend
  WorktreeExecutionBackend
  DockerExecutionBackend
  RemoteSandboxBackend
```

Docker or remote sandbox should own dependency setup, network policy, secret
mounts, command execution, artifact export, and cleanup. `AgentLoop` should not
know which backend is active.

## Public Project Copy

Short description:

> Production-style CodingAgent runtime core with context engineering, governed
> tool execution, MCP tools, trace, usage reporting, and eval regression.

Resume/project bullets:

- Designed and implemented a production-style CodingAgent runtime core covering
  ReAct-style control flow, context construction, tool routing, sandboxed
  execution, approval hooks, task checkpoints, and trace replay.
- Built a governed tool layer with schema validation, command allowlists,
  workspace sandboxing, MCP stdio server integration, offline/live web search
  tools, and failed-observation recovery.
- Added observability and evaluation infrastructure, including per-step
  token/cost/latency reports, cache hit/miss metrics, context breakdowns, tool
  efficiency tracking, local eval cases, and committed run artifacts.
- Created WebhookPatchBench to validate code-repair behavior under webhook
  signature verification, idempotency, side-effect ordering, secret boundaries,
  test validation, and deterministic review gates.

## Remaining Maturity Risks

| risk | current answer | next step |
|---|---|---|
| No real production traffic | This is a runtime core, not a hosted service. Evidence comes from traces, evals, and run artifacts. | Add service wrapper and online telemetry only if productizing. |
| Sandbox is not container-grade | Worktree + policy hooks demonstrate the control plane. | Add Docker/remote backend behind `ExecutionEnvironment`. |
| Eval set is small | Cases map to concrete runtime failure modes. | Add automated benchmark matrix and trend history. |
| MCP is stdio/local first | Enough to prove discovery and invocation. | Add remote transport, auth, rate limit, and quota. |
| No IDE/TUI | Intentionally out of scope. | Build UI on top of CLI/API after runtime stabilizes. |

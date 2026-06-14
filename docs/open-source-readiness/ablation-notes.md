# Ablation Notes

This document records the first-pass ablation plan for Agent Forge. The current
repository does not ship a full experiment runner for every ablation. Instead,
it defines the controls, expected failure modes, and low-cost commands that can
be used to compare runtime behavior.

## Baseline

Use the deterministic local baseline:

```bash
scripts/verify.sh
scripts/verify_mcp.sh
```

Use the real-model WebhookPatchBench baseline when API access is available:

```bash
local_scripts/run_webhook_deepseek.sh
```

Read:

```text
docs/run-artifacts/webhook-deepseek/usage_report.md
docs/run-artifacts/webhook-deepseek/trace.json
```

## Ablation Matrix

| ablation | how to simulate now | expected degradation | evidence to inspect |
|---|---|---|---|
| Lower context budget | `python run_demo.py --mode single --max-context-chars 1500` | More missing-file mistakes, more repeated reads, weaker final answer. | `context_assembly.dropped_context`, selected files, failed tool calls. |
| Remove MCP config | Run WebhookPatchBench without `--mcp-config`. | Agent loses external protocol tools such as `forge.repo_policy`. | `mcp_tools_loaded` event absent; tool schema count lower. |
| Dry-run execution | `python run_demo.py --mode single --approval-mode dry-run` | Writes and command execution are denied; useful for planning-only mode. | `hook_check`, `permission_check`, failed observations. |
| Locked mode | `python run_demo.py --mode single --approval-mode locked` | Side effects are blocked; final answer should explain block. | `hook_check` deny reason. |
| Worktree isolation | `python run_demo.py --mode single --execution-env worktree --cleanup-worktree` | Runtime overhead increases, but main checkout remains clean. | `execution_environment` event and run report. |
| Disable live model | `python run_demo.py --mode single --llm mock` | Deterministic smoke behavior, lower realism. | Usage report shows mock provider and zero real API cost. |
| Use real model | `local_scripts/run_webhook_deepseek.sh` | Better natural recovery, nonzero token/cost. | `usage_report.md` step/cost breakdown. |

## Design Claims To Validate

| claim | validation idea |
|---|---|
| ContextStrategy reduces irrelevant prompt load. | Compare selected files and total context chars with low budget vs default budget. |
| ToolRouter reduces tool overload. | Compare `available_tools` and tool schema chars with and without MCP config. |
| Hooks are more reliable than prompt-only safety. | Run approval/dry-run cases and confirm the tool is denied before execution. |
| StepController prevents failure loops. | Run repeated-tool eval and confirm the loop stops with an explicit stop reason. |
| Usage report is actionable. | Compare WebhookPatchBench and calculator reports for tokens, failures, and tool efficiency. |

## Next Implementation Step

A future experiment runner should write one markdown file per ablation:

```text
.agent_forge/ablations/<timestamp>/
  baseline_trace.json
  ablated_trace.json
  comparison.md
```

The runner should record:

- selected files
- context chars and estimated tokens
- tool schema chars
- tool call success rate
- failed observations
- stop reason
- token/cost/latency

That would turn these notes into a reproducible ablation harness without
changing the main AgentLoop.


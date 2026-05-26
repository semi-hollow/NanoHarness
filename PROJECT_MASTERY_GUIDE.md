# Project Mastery Guide

This guide is the shortest project-first path for understanding Agent Forge.
It avoids product polish and focuses on the runtime ideas that matter when
explaining or extending a CodingAgent system.

## 1. Start With The Two Fixtures

`examples/demo_repo` is the smoke test:

- one tiny calculator bug
- fastest way to prove read -> patch -> test works
- useful when checking environment setup

`examples/webhook_service_repo` is the main validation scenario:

- issue-driven bug in a small webhook service
- duplicate `event_id` must be ignored after signature verification
- patch must happen before store/queue side effects
- security policy and `.env` boundary must remain intact

Run them:

```bash
local_scripts/run_webhook_deepseek.sh
local_scripts/run_deepseek.sh
scripts/verify.sh
```

Use `run_webhook_deepseek.sh` as the main personal-Mac run because it combines
the real DeepSeek model with the WebhookPatchBench scenario. Use
`run_deepseek.sh` only for the shortest single-agent smoke run. Use
`scripts/verify.sh` when you need an offline deterministic check.

## 2. Read The Runtime In This Order

1. `agent_forge/cli.py`: composition root, mode dispatch, model selection.
2. `agent_forge/runtime/agent_loop.py`: context -> LLM -> tool -> observation loop.
3. `agent_forge/context/context_strategy.py`: context ranking, memory, budget, topic shift.
4. `agent_forge/tools/registry.py`: tool protocol and argument validation.
5. `agent_forge/safety/`: permission, sandbox, command policy, guardrails.
6. `agent_forge/observability/usage_report.py`: token/cost/context/tool efficiency report.
7. `agent_forge/agents/supervisor_agent.py`: multi-agent orchestration boundary.

## 3. What WebhookPatchBench Proves

The benchmark is not about webhook business complexity. It proves the harness
can coordinate the engineering loop:

- select issue, handler, tests, docs, and policy files from a repo
- preserve a security check while fixing reliability
- apply a minimal patch through a governed write tool
- run the exact tests that prove the fix
- block secret-file reads through sandbox policy
- record trace, usage, eval, report, and rollback evidence
- reject risky signature-bypass patches through validation

## 4. How To Study A Run

After `local_scripts/run_webhook_deepseek.sh`, open:

- `trace-webhook-deepseek.pretty.json`: event-by-event agent behavior
- `trace-webhook-deepseek.usage_report.md`: token, context, cost, and tool efficiency
- `.agent_forge/runs/<run_id>/report.md`: session-level report if session is enabled

Read the trace by step:

1. `context_assembly`: what files and docs were selected
2. `llm_call`: what the model decided
3. `permission_check` / `human_approval`: why writes were allowed
4. `tool_call` / `tool_observation`: what actually happened
5. `final_answer`: what was verified and what remains unverified

## 5. Clean Mental Model

Agent Forge is a runtime harness, not a model and not a UI.

The LLM decides local next actions. The runtime owns everything with blast
radius: context budget, tool schema, permissions, sandbox, retries, max steps,
cost, trace, tests, eval, and recovery.

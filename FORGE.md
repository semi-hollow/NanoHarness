# Agent Forge Project Instructions

## Runtime Identity

Agent Forge is a production-style CodingAgent runtime core. Treat it as an
engineering system for controlled code editing, not as a chatbot wrapper.

## Primary Validation Scenario

Use `examples/webhook_service_repo` as the main end-to-end scenario. The normal
DeepSeek entrypoint is:

```bash
local_scripts/run_webhook_deepseek.sh
```

The deterministic offline health check is:

```bash
scripts/verify.sh
```

## Allowed Command Shape

The command policy is intentionally narrow. Prefer:

```bash
python -m unittest discover tests
python -m unittest discover examples/webhook_service_repo/tests
git status
git diff
```

Do not use `pytest`, `cd`, direct test-file execution, `python -c`, shell
pipelines, network commands, deletion commands, `git push`, or `git reset`.

## Editing Rules

- Inspect relevant files before editing.
- Keep changes scoped to the requested behavior.
- Do not read `.env`, private keys, credentials, or secret-like paths.
- Do not modify `examples/webhook_service_repo/docs/security_policy.md` during
  the webhook scenario.
- Validate behavior with the smallest allowed unittest command.
- Do not claim validation succeeded unless a successful command observation is
  present in trace.

## Runtime Architecture Rules

- AgentLoop is the canonical single-agent path.
- ToolRegistry is the protocol boundary between model tool calls and local code.
- Hooks are the deterministic policy layer for tool approval, environment
  checks, and observation redaction.
- ExecutionEnvironment owns local/worktree boundaries and network policy.
- TaskStateStore owns resumable control state; trace owns full audit evidence.
- Usage reports are derived from trace and should stay readable.

## Output Discipline

Final answers should cite concrete tool evidence when available and call out
unverified production concerns. For local runs, do not imply cloud isolation,
real traffic validation, or online deployment unless the trace proves it.

# NanoHarness Component Index

This document is a cached structural index for frequently revisited NanoHarness architecture questions. It is meant to avoid repeated whole-repository scans when locating the runtime pieces behind tool execution, validation, permissions, sandboxing, and observability.

> Snapshot note: verify paths, signatures, and line numbers before making code changes if the repository has changed since this index was written.

## Tool Registry / router / wiring

- `agent_forge/tools/registry.py` — `ToolRegistry`; main name-to-tool registry and execution boundary. Key methods: `register()`, `schemas()`, `execute()`, `_validate_arguments()`.
- `agent_forge/tools/tool_router.py` — `ToolRouter`; filters available tool schemas per turn based on intent, risk/mode metadata, read-only wording, and active skill tool names.
- `agent_forge/runtime/wiring.py` — `build_registry()`; constructs `WorkspaceSandbox` and registers built-in tools such as list/read/write/grep/patch/run/git/diagnostics/ask-human, plus MCP tools if configured.
- `agent_forge/tools/base.py` — `Tool` base contract with `schema()` and `execute()`.
- `agent_forge/tools/mcp_config.py`, `agent_forge/tools/adapters/mcp_style_adapter.py`, `agent_forge/tools/mcp_stdio.py` — MCP/external tool adapters into the local registry.
- `agent_forge/skills/registry.py` — `SkillRegistry`; adjacent capability registry, not the core tool registry, but selected skills can widen router tool access through `tool_names`.

## Validator / schema validation / guardrails

- `agent_forge/tools/registry.py` — `_validate_arguments()` and `_matches_type()` validate tool required args and primitive types before execution.
- `agent_forge/runtime/structured_output.py` — `StructuredOutputParser`; extracts JSON from model text, validates a JSON-Schema-like subset, and builds repair prompts.
- `agent_forge/runtime/llm_client.py` — `_parse_tool_calls()` validates provider tool-call arguments as JSON objects and `_tool_to_openai_schema()` converts local tool schemas.
- `agent_forge/safety/guardrails.py` — `input_guardrail()`, `output_guardrail()`, and `tool_guardrail()` validate risky user input, final-answer claims, and unknown/repeated/malformed tool calls.
- `agent_forge/safety/command_policy.py` — `check_command()` validates shell-like commands against deny/allow rules.
- `agent_forge/skills/registry.py` — `SkillSpec.from_mapping()`, `_dict_field()`, `_list_field()` validate skill manifest fields.

## Permission / approval policy

- `agent_forge/safety/permission.py` — `PermissionPolicy` and `PermissionDecision`; central allow/ask/deny decision layer. Reads are generally allowed, writes ask, commands are checked, network/delete/external actions denied.
- `agent_forge/safety/command_policy.py` — command permission defense for `run_command`, blocking dangerous commands and allowlisting safe validation/read-only git inspection.
- `agent_forge/runtime/hooks.py` — `ApprovalMode`, `PermissionHook`, and `HookManager.default()` connect permission policy to runtime hooks; modes include `trusted`, `on-write`, `on-risk`, `locked`, and `dry-run`.
- `agent_forge/runtime/agent_loop.py` — constructs `HookContext`, runs hooks before tool execution, records `permission_check`, handles approval, and maps permission actions.
- `agent_forge/runtime/config.py` and `agent_forge/forge_cli.py` — runtime/CLI approval knobs such as `approval_mode` and `auto_approve_writes`.
- Tool-level defense in `agent_forge/tools/write_file.py`, `agent_forge/tools/apply_patch.py`, and `agent_forge/tools/run_command.py` checks permission before side effects.

## Sandbox / workspace boundary / execution environment

- `agent_forge/safety/sandbox.py` — `WorkspaceSandbox`; resolves paths under workspace root and blocks traversal, external paths, and secret-like files. Key methods: `resolve_path()`, `is_sensitive_path()`, `ensure_safe_path()`.
- `agent_forge/runtime/wiring.py` — creates the shared sandbox and injects it into built-in tools.
- Tool sandbox usage in `agent_forge/tools/read_file.py`, `list_files.py`, `grep.py`, `write_file.py`, `apply_patch.py`, and `run_command.py`.
- `agent_forge/runtime/execution_environment.py` — `ExecutionEnvironmentConfig` and `ExecutionEnvironment`; higher-level local vs isolated git worktree environment, protected paths, network command policy, command/path validation, and redaction.
- `agent_forge/runtime/hooks.py` — `ExecutionEnvironmentHook` enforces path/command restrictions before tools run.

## Observer / hooks / events / trace

- `agent_forge/observability/trace.py` — `TraceRecorder`; main append-only run event recorder, metrics collector, and final trace JSON writer.
- `agent_forge/observability/event.py` — `TraceEvent` typed event shape.
- `agent_forge/runtime/hooks.py` — `RuntimeHook` and `HookManager`; pre-tool/post-tool/on-stop observer and extension mechanism, including permission, environment, and secret-redaction hooks.
- `agent_forge/runtime/agent_loop.py` — central event emission points for task checkpoint, guardrail, context/tool routing, hook check, permission check, tool call, observation, and stop hooks.
- `agent_forge/observability/metrics.py`, `agent_forge/observability/usage_report.py`, and `agent_forge/observability/evidence.py` consume trace events for counters, human-readable usage reports, and citeable evidence.

## Suggested reading order

1. `agent_forge/runtime/wiring.py`
2. `agent_forge/tools/registry.py`
3. Relevant sections of `agent_forge/runtime/agent_loop.py`
4. `agent_forge/safety/permission.py` and `agent_forge/safety/sandbox.py`
5. `agent_forge/observability/trace.py`

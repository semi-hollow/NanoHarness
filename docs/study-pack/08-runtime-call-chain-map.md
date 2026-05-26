# 08 Runtime Call Chain Map

This document solves a practical reading problem: "I can find direct references
in the IDE, but I still cannot see how this method participates in a real run."

Use it as a map from entrypoint to runtime behavior. Tests and eval scripts are
useful evidence, but they are not always the main production-shaped path.

## How To Read A Function

When you land on a method, classify it first:

```text
entrypoint       run_demo.py, agent_forge/cli.py
runtime loop     agent_forge/runtime/agent_loop.py
context          agent_forge/context/*
model gateway    agent_forge/models/*, agent_forge/runtime/llm_client.py
tool boundary    agent_forge/tools/*
safety policy    agent_forge/safety/*
observability    agent_forge/observability/*
eval/test        eval_cases/*, tests/*
```

Then ask two questions:

1. Who directly calls this method?
2. Which real runtime chain reaches that direct caller?

`Find All References` answers question 1. This file answers question 2.

## Main Single-Agent Chain

This is the canonical path for `local_scripts/run_webhook_deepseek.sh`,
`local_scripts/run_webhook_bench.sh`, and normal `--mode single` runs.

```mermaid
flowchart TD
    A["run_demo.py"] --> B["agent_forge.cli.main()"]
    B --> C["build_parser()"]
    B --> D["reset_demo_repo() / reset_webhook_bench()"]
    B --> E["SessionStore + DiffTracker + TraceRecorder"]
    B --> F["resolve_llm_config()"]
    F --> G["build_llm()"]
    G --> H["ModelGateway"]
    B --> I["build_registry()"]
    I --> J["ToolRegistry with tools"]
    B --> K["AgentLoop.run(task)"]
    K --> L["ContextBuildReport"]
    K --> M["ModelGateway.chat()"]
    M --> N["LLMClient.chat()"]
    N --> O["AgentResponse"]
    O --> P["ToolCall"]
    P --> Q["guardrails + PermissionPolicy"]
    Q --> R["ToolRegistry.execute()"]
    R --> S["Concrete Tool.execute()"]
    S --> T["Observation"]
    T --> K
    K --> U["TraceRecorder.write()"]
    U --> V["write_usage_artifacts()"]
```

Mental model:

```text
CLI composes dependencies.
AgentLoop owns the loop.
LLM proposes actions.
Policies decide whether actions are allowed.
Tools execute and return observations.
Trace/usage turn the run into evidence.
```

## Command Policy Chain

Example question: where does `check_command()` matter in a real run?

Direct references show:

```text
agent_forge/safety/permission.py
tests / eval_cases
```

But the real single-agent runtime chain is:

```mermaid
flowchart TD
    A["run_demo.py"] --> B["agent_forge.cli.main()"]
    B --> C["build_registry()"]
    C --> D["RunCommandTool(sandbox, auto)"]
    B --> E["AgentLoop.run()"]
    E --> F["LLM returns ToolCall: run_command"]
    F --> G["AgentLoop._permission_action('run_command')"]
    G --> H["PermissionPolicy.decide('run_command', command)"]
    H --> I["check_command(command)"]
    I --> J{"allow?"}
    J -->|deny| K["blocked observation"]
    J -->|allow| L["ToolRegistry.execute('run_command')"]
    L --> M["RunCommandTool.execute()"]
    M --> N["PermissionPolicy.decide('run_command', command)"]
    N --> O["check_command(command)"]
    O --> P["subprocess.run(shell=False)"]
    P --> Q["Observation(exit_code + output)"]
    Q --> E
```

Why is it called twice?

```text
AgentLoop permission check:
  records the policy decision in trace before tool execution.

RunCommandTool internal check:
  enforces the same policy at the concrete tool boundary.
```

That duplication is intentional defense in depth. The model cannot skip the
AgentLoop check, and a direct tool call in a test/eval still goes through the
tool's own policy.

What the model sees:

```text
RunCommandTool.schema()
  -> description says prefer python -m unittest discover <test_dir>
  -> description says pytest / cd / python -c / direct file execution are blocked
```

What actually enforces it:

```text
PermissionPolicy.decide()
  -> check_command()
    -> allowlist unittest, git status, git diff
    -> block network, deletion, privilege, push, unknown commands
```

## Tool Execution Chain

All tools follow the same general shape:

```mermaid
flowchart TD
    A["LLM ToolCall"] --> B["AgentLoop guardrail_check"]
    B --> C["PermissionPolicy"]
    C --> D["ToolRegistry.execute()"]
    D --> E["ToolRegistry._validate_arguments()"]
    E --> F["Concrete Tool.execute()"]
    F --> G["WorkspaceSandbox.ensure_safe_path()"]
    F --> H["Observation(success/content)"]
    H --> I["Memory.add_observation()"]
    H --> J["TraceRecorder.add(tool_observation)"]
    H --> K["Message(role='tool')"]
    K --> L["next LLM call"]
```

Important point:

```text
Tool failures are data, not crashes.
```

For example, a patch mismatch returns:

```text
Observation(success=False, content="old text not found")
```

Then `StepController` classifies it and writes a recovery decision.

## Context Engineering Chain

Context is rebuilt every step.

```mermaid
flowchart TD
    A["AgentLoop.run step N"] --> B["build_repo_map(workspace)"]
    B --> C["build_context_report()"]
    C --> D["ContextStrategy"]
    D --> E["file ranking"]
    D --> F["lexical retrieval"]
    D --> G["memory summary"]
    D --> H["topic relation"]
    D --> I["token budget / dropped context"]
    C --> J["context_report.render()"]
    J --> K["system Message"]
    K --> L["LLM call"]
    C --> M["Trace context_assembly event"]
```

When you see a method under `agent_forge/context/`, look for it through:

```text
AgentLoop.run()
  -> build_context_report()
    -> context_builder.py
    -> context_strategy.py
    -> repo_map / rag / file_ranker / memory
```

Trace evidence:

```text
event_type = context_assembly
fields = selected_files, retrieved_docs_count, budget_breakdown, dropped_context
```

## Model Call Chain

Mock, DeepSeek, Ollama, and company APIs share the same runtime boundary.

```mermaid
flowchart TD
    A["CLI args / env / profile"] --> B["resolve_llm_config()"]
    B --> C["build_llm()"]
    C --> D["ModelGateway"]
    D --> E["LLMClient.chat()"]
    E -->|mock| F["MockLLMClient"]
    E -->|real API| G["OpenAICompatibleLLMClient"]
    G --> H["/chat/completions"]
    H --> I["AgentResponse"]
    I --> J["ModelUsage"]
    J --> K["Trace llm_call event"]
    K --> L["usage_report.md"]
```

When you read model-related code, separate three layers:

```text
llm_config.py:
  resolves provider/base_url/model/key.

llm_client.py:
  knows provider wire format.

models/gateway.py:
  wraps retry/fallback/usage telemetry.
```

## Trace And Usage Chain

Trace is written incrementally during runtime, then turned into reports at the
end.

```mermaid
flowchart TD
    A["AgentLoop / Supervisor"] --> B["TraceRecorder.add()"]
    B --> C["events[]"]
    C --> D["TraceRecorder.write()"]
    D --> E["trace-*.json"]
    E --> F["write_usage_artifacts()"]
    F --> G["trace-*.usage.json"]
    F --> H["trace-*.usage_report.md"]
    D --> I["RunReportWriter"]
    I --> J[".agent_forge/runs/<id>/report.md"]
```

Use trace to confirm real execution, not just code references:

```bash
python -m json.tool trace-webhook-deepseek.json > trace-webhook-deepseek.pretty.json
rg '"event_type": "tool_call"|"event_type": "tool_observation"' trace-webhook-deepseek.pretty.json
```

## Multi-Agent Chain

`multi` mode is not the same as single mode. It is a supervised orchestration
path that reuses `AgentLoop` through role-specific runtimes.

```mermaid
flowchart TD
    A["cli.py --mode multi"] --> B["SupervisorAgent.run()"]
    B --> C["TaskGraph"]
    C --> D["TaskScheduler"]
    D --> E["AgentSpec"]
    E --> F["AgentRuntime"]
    F --> G["FilteredToolRegistry"]
    F --> H["AgentLoop.run(agent_name=role)"]
    H --> I["TaskArtifact"]
    I --> J["Supervisor validation / retry / review"]
```

When reading multi-agent files, the main path is:

```text
cli.py
  -> SupervisorAgent.run()
    -> task_graph.py
    -> agent_runtime.py
    -> AgentLoop.run()
```

## Eval/Test Chain

Eval and tests are not the main runtime path. They are evidence harnesses.

```mermaid
flowchart TD
    A["python -m agent_forge.eval.eval_runner"] --> B["run_case(case_dir)"]
    B --> C["eval_cases/*/verify.py"]
    C --> D["run_demo.py or direct module checks"]
    D --> E["trace / JSON verify output"]
    E --> F["EvalResult"]
    F --> G[".agent_forge/eval_report.md"]
```

How to interpret references:

```text
verify.py calls a method:
  eval evidence path.

cli.py / agent_loop.py calls a method:
  runtime path.

tool.execute() calls a method:
  concrete action boundary.

tests/* call a method:
  unit-level behavior proof.
```

## VS Code Workflow

Use IDE navigation in this order:

1. `Go to Definition`
2. `Find All References`
3. `Show Call Hierarchy`
4. Open this document and match the method to a runtime chain.
5. Open the trace file and confirm whether that chain happened in a real run.

Useful terminal checks:

```bash
rg "check_command"
rg "PermissionPolicy"
rg "ToolRegistry.execute"
rg "TraceRecorder.add"
rg "build_context_report"
```

## Concrete Example: check_command()

If you are reading:

```text
agent_forge/safety/command_policy.py::check_command
```

Do not stop at direct references. Read it like this:

```text
1. User runs:
   local_scripts/run_webhook_deepseek.sh

2. Script calls:
   run_demo.py "...webhook task..." --mode single --llm deepseek

3. CLI builds:
   build_registry()
     -> RunCommandTool
   build_llm()
     -> ModelGateway(OpenAICompatibleLLMClient)
   AgentLoop(...)

4. AgentLoop receives model tool call:
   ToolCall(name="run_command", arguments={"command": "python -m unittest discover examples/webhook_service_repo/tests"})

5. Before execution:
   PermissionPolicy.decide("run_command", command)
     -> check_command(command)

6. During concrete tool execution:
   RunCommandTool.execute()
     -> PermissionPolicy.decide("run_command", command)
       -> check_command(command)
     -> subprocess.run(...)

7. Result returns:
   Observation(success=True, content="exit_code=0 ...")
     -> trace tool_observation
     -> next LLM turn
```

That is the difference between "this function is referenced by permission.py"
and "this function is part of the runtime safety gate for every command tool
call."

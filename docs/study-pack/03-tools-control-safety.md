# 03 Tools, Control, And Safety

## Tool Protocol

`ToolRegistry` is the boundary between model output and local execution.

```text
LLM tool_call
  -> tool_guardrail
  -> ToolRegistry schema validation
  -> PermissionPolicy
  -> concrete Tool.execute()
  -> Observation
  -> next LLM turn
```

Tool observations use role `tool` and carry `tool_call_id`. The preceding assistant message records the matching OpenAI-compatible `tool_calls` object. This keeps the local runtime aligned with standard function-calling protocol.

## Tool Governance

The registry validates missing and mistyped arguments before concrete tools run. Concrete tools still validate path, command, and policy because schema validation is not a security boundary.

Important files:

- `tools/registry.py`: schema validation and failure normalization
- `tools/run_command.py`: shell-free command execution
- `tools/apply_patch.py`: controlled edit operation
- `safety/sandbox.py`: workspace path boundary
- `safety/command_policy.py`: dangerous command blocklist
- `safety/permission.py`: approval policy

## Execution Control

`runtime/control.py` is the loop control plane. It classifies failures into:

- unknown tool
- invalid arguments
- permission denied
- patch mismatch
- command failed
- tool exception
- repeated action
- model response failure
- budget exceeded

Each failure has:

- `retryable`
- `reason`
- `recovery_hint`

This is what prevents the agent from calling the same broken tool forever.

## Budgets

Runtime budgets are explicit:

```bash
python run_demo.py --mode single \
  --max-steps 12 \
  --max-context-chars 8000 \
  --max-consecutive-failures 3 \
  --max-tool-repeats 2 \
  --timeout-seconds 120
```

The design answer is: a production agent must have cost, time, step, failure, and permission limits. The LLM can propose actions, but the runtime owns whether the action is allowed to happen.

## Trace Reading

Trace JSON contains the evidence:

- `context_assembly`: selected files, memory policy, topic relation, budget
- `plan`: current runtime intent summary
- `llm_call`: model usage estimate and response shape
- `guardrail_check`: input/tool/output policy
- `permission_check`: allow/ask/deny
- `tool_call`: concrete requested action
- `tool_observation`: success/failure evidence
- `recovery_decision`: retryability and next recovery hint
- `final_answer`: final response

## Technical Discussion Answer

For tool and exception questions, say:

> Tool calls are not direct execution. I normalize them through schema validation, guardrails, permission policy, sandbox checks, execution, observation, failure classification, and trace. Retry is based on failure kind: patch mismatch and invalid arguments are retryable, permission denial and repeated identical actions are not.

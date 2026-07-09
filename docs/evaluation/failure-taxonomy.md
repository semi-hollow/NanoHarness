# Failure Taxonomy

NanoHarness classifies coding-agent failures so a bad run becomes an engineering target instead of a raw log.

## Evidence Levels

- `patch_generated`: non-empty diff exists; this is a candidate patch only.
- `local_verified`: project diagnostics or tests passed in the prepared workspace.
- `official_resolved`: official SWE-bench harness accepted the patch.
- `not_evaluated`: no correctness claim beyond trace and patch evidence.

## Failure Classes

| Class | Meaning | Typical next action |
| --- | --- | --- |
| `context_miss` | The agent did not surface concrete source files. | Tune file ranking, symbol search, or external context retrieval. |
| `tool_not_available` | The requested tool failed or was unavailable. | Classify as retryable, hidden-by-policy, or schema-invalid. |
| `tool_schema_mismatch` | The model called a natural shape that the tool contract did not support. | Align tool schema/coercion with observed model behavior. |
| `unsafe_or_blocked_command` | Command or permission policy blocked an unsafe action. | Replace free shell with diagnostics or approval. |
| `repeated_action_loop` | The agent repeated actions without new information. | Add recovery that forces a different observation path. |
| `pending_tool_call_at_stop` | The model still wanted a tool when the run ended. | Increase budget or force earlier patch/no-patch decision. |
| `provider_transport_error` | Provider transport failed. | Treat separately from agent logic. |
| `validation_environment_unavailable` | Tests could not run due to environment/dependencies. | Fix environment before tuning the agent. |
| `patch_generated_but_unverified` | A candidate patch exists but correctness is unproven. | Run local or official evaluation. |
| `official_eval_failed` | Official harness rejected the patch. | Analyze patch and add case to regression. |

## Interview framing

The point is not to label failures after the fact. The point is to decide whether the next improvement belongs in context selection, tool governance, sandbox policy, diagnostics, provider handling, or prompt procedure.

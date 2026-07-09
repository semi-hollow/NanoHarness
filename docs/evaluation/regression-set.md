# Small Regression Set

NanoHarness uses a small high-signal regression set instead of chasing broad benchmark coverage during development. The built-in `core` regression command currently runs a small SWE-bench subset; the table below is the target high-signal coverage map for offer-readiness work, including local/non-SWE-bench scenarios that may be represented by case files or docs before they become built-in IDs.

## Target coverage map

| Case | Purpose | Primary failure mode |
| --- | --- | --- |
| `astropy__astropy-12907` | Real SWE-bench patch path and line-window tool behavior. | `tool_schema_mismatch` / `patch_generated_but_unverified` |
| `validation-env-unavailable` | Distinguish code failure from missing test dependencies. | `validation_environment_unavailable` |
| `tool-governance-blocked-command` | Show why free-form shell/write tools should be narrowed. | `unsafe_or_blocked_command` |
| `context-miss-file-selection` | Verify expected source files appear before edit decisions. | `context_miss` |
| `repeated-action-loop` | Verify repeated read/search is recoverable but repeated writes are blocked. | `repeated_action_loop` |

## Metrics

- patch generated
- local verified
- official resolved, when available
- failure class
- tool calls
- failed tool calls
- repeated actions
- context files selected
- estimated cost
- latency

## Rule

A runtime change is useful only if it improves success, observability, failure localization, cost, or safety boundary on at least one case without hiding regressions on the others.

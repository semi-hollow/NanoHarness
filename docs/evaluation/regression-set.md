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
| `manual-approval-pending` | Verify manual approval stops before side effects and approved operations can proceed. | `human_approval_required` |
| `stale-approval-fingerprint` | Verify an approved side effect is not executed if the target changed after approval. | `approval_stale` |
| `resume-state-continuation` | Verify checkpoint summaries seed the next run without claiming hidden chat replay. | `partial_execution_recovery` |
| `subagent-fanout-conflict` | Verify independent tasks batch together while overlapping write scopes require conflict resolution. | `subagent_conflict_resolution` |
| `operation-ledger-idempotency` | Verify an already executed side effect is skipped on rerun/resume. | `duplicate_side_effect_prevented` |
| `operation-ledger-stale-target` | Verify an executed operation is not treated as safely skipped after target drift. | `stale_operation_record` |
| `research-citation-quality` | Non-coding case for source-backed research claims and source limitation handling. | `unsupported_claim_control` |
| `ops-approval-workflow` | Non-coding case for policy-sensitive actions, HITL, and auditable execution summaries. | `human_approval_required` |

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
- human intervention count
- duplicate side-effect skips
- stale approval / stale operation count
- unsupported claim count

## Non-coding mini cases

`docs/evaluation/mini-cases/` stores small JSON cases that are not full
benchmarks. They exist to make the project explainable for broader Agent
application interviews beyond Coding Agent roles. The loader in
`agent_forge/evaluation/mini_cases.py` keeps them machine-readable while staying
lightweight enough for interview walkthroughs.

Run them with:

```bash
forge eval mini-cases --case research-citation-quality --evidence evidence.json
```

## Rule

A runtime change is useful only if it improves success, observability, failure localization, cost, or safety boundary on at least one case without hiding regressions on the others.

# Small Regression Set

NanoHarness uses a small high-signal regression set instead of chasing broad benchmark coverage during development. The built-in `core` command pins five public SWE-bench Lite cases from five repositories; the following table also records local runtime and non-coding contracts.

## Fixed SWE-bench Lite core

| Case | Repository behavior | Why it is useful |
| --- | --- | --- |
| `astropy__astropy-12907` | Nested `CompoundModel` separability. | Small patch with non-trivial code navigation and an existing failure-driven case study. |
| `django__django-11133` | `HttpResponse` handling of `memoryview`. | Framework compatibility fix with a focused data-type boundary. |
| `matplotlib__matplotlib-18869` | Comparable top-level version information. | Cross-file public API behavior and backward-compatibility reasoning. |
| `pytest-dev__pytest-5103` | Assertion rewriting for `all`/`any`. | Parser/rewrite behavior with test-report quality implications. |
| `sympy__sympy-20590` | Unexpected `Symbol.__dict__` regression. | Object-layout and inheritance reasoning in a large symbolic codebase. |

```bash
forge bench swebench --regression-set core --provider deepseek \
  --model deepseek-chat --tool-routing task-aware --evaluate
```

Each run writes `scorecard.json` and `scorecard.md`. Official resolved rate is
omitted when no case has an explicit resolved/unresolved report.

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
benchmarks. They exercise evaluation dimensions shared by broader Agent
applications beyond code repair. The loader in
`agent_forge/evaluation/mini_cases.py` keeps them machine-readable and
deterministic.

Run them with:

```bash
forge eval mini-cases --case research-citation-quality --evidence evidence.json
```

## Rule

A runtime change is useful only if it improves success, observability, failure localization, cost, or safety boundary on at least one case without hiding regressions on the others.

For a runtime factor, compare two matched runs with `forge eval ablation`; do
not compare runs with different models, datasets, splits, or case ids.

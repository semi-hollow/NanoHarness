# Durable Human Input and Live Fanout

This design closes two deliberately scoped runtime gaps without turning Agent
Forge into a general distributed-agent platform:

1. replace the synthetic `ask_human` response with a durable stop/respond/resume
   control path;
2. connect dependency-aware fanout to real `AgentLoop` workers running in
   isolated git worktrees.

## Goals

- A human question is a persisted control-plane event, not blocking terminal
  input and not an auto-approved observation.
- `forge respond` records an auditable answer; `forge resume` injects that
  answer into a continuation run.
- A structured fanout plan can execute independent tasks concurrently through
  real model and tool loops.
- Every worker has a separate context, trace, usage report, tool allowlist, and
  git worktree.
- Write results are merged deterministically only after scope and patch checks.
- Completed work can be restored from artifacts while failed or blocked tasks
  are rerun.
- Reports compare wall-clock latency, summed worker latency, token/cost totals,
  merge outcomes, and conflict counts.

## Non-Goals

- No peer-to-peer agent chat, dynamic swarm, distributed queue, or consensus.
- No shared-workspace concurrent writes.
- No automatic LLM conflict resolver that silently broadens a worker's scope.
- No claim that reviewer/verifier PASS equals official benchmark resolution.
- No multi-user authentication layer for the local filesystem-backed stores.

## Durable Human Input

`HumanInputStore` owns `HumanInputRequest` records with a stable request id,
thread id, question, optional choices, status, answer, run/step/agent identity,
workspace, timestamps, and artifact path.

```text
AgentLoop / ClarificationPolicy
  -> HumanInputStore.request(...)
  -> checkpoint WAITING_HUMAN + trace event
  -> stop before further tools
  -> forge respond <request-id> --answer <text>
  -> request RESPONDED
  -> forge resume <run-dir>
  -> answer appended to continuation task and resume context
  -> AgentLoop continues under the same human thread id
```

The `ask_human` tool becomes a runtime control signal. Direct tool execution
fails closed; `AgentLoop` intercepts the call and owns persistence and state
transition. Side-effect approval remains a separate `ApprovalStore` contract
because an approval decision and an informational answer have different stale
state and authorization semantics.

## Structured Fanout Plan

The public input is JSON so dependencies and write ownership are explicit:

```json
{
  "goal": "Update runtime behavior and its focused tests",
  "tasks": [
    {
      "id": "runtime",
      "task": "Implement the runtime change",
      "depends_on": [],
      "write_scope": ["agent_forge/runtime/"],
      "allowed_tools": ["read_file", "grep_search", "apply_patch", "diagnostics"],
      "expected_artifact": "runtime_patch"
    },
    {
      "id": "tests",
      "task": "Add focused tests",
      "depends_on": [],
      "write_scope": ["tests/"],
      "expected_artifact": "test_patch"
    }
  ]
}
```

The scheduler validates unique ids, known dependencies, acyclicity, bounded
worker count, normalized relative scopes, and known tool names. Read-only tasks
have an empty `write_scope`; a write task must declare at least one scope.

## Worker and Merge Flow

```text
validated DAG
  -> ready tasks partitioned into conflict-free parallel batches
  -> one detached worktree + one AgentLoop + one LLM client per worker
  -> worker artifact / trace / usage / patch / touched-file list
  -> actual touched paths checked against declared scope
  -> same-batch actual overlap check
  -> git apply --check
  -> deterministic patch apply in task order
  -> dependent batch starts from the integrated state
  -> final read-only Aggregator/Verifier AgentLoop
  -> fanout_summary.json / fanout_report.md / integration.patch
```

Declared overlap is serialized rather than run concurrently. Undeclared actual
overlap, scope escape, or patch-apply failure stops merging and records
`conflict_resolution_required`; it is not handed to an unconstrained model.

## Recovery

The summary records plan digest, base commit, completed/merged task ids, worker
artifacts, and integration patch. A resume run must match plan digest and base
commit, reapplies previously merged patches to a fresh integration workspace,
skips completed tasks, and reruns only incomplete tasks. Dependency failures
block downstream tasks but do not discard independent completed artifacts.

## Evidence and Acceptance

- Human-input tests prove no model/tool side effect occurs while waiting, a
  response is persisted, and resume context contains the answer.
- Fanout tests use real `AgentLoop` instances with deterministic LLM fixtures,
  separate worktrees, measured overlap, deterministic merge, and selective
  resume.
- A real-provider smoke uses two read-only workers and records concurrent
  wall-clock and aggregate usage without modifying the repository.
- Public docs distinguish sequential role coordination, live fanout, real
  side-effect approval, durable clarification, and official evaluation.


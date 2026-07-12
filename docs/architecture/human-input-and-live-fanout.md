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
workspace, timestamps, and artifact path. Request ids are restricted to the
generated 24-character hexadecimal form, and writes use fsync plus atomic
replacement. Normalized choices participate in identity so changed options do
not reuse an old answer.

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
      "expected_artifact": "runtime_patch",
      "max_steps": 12
    },
    {
      "id": "tests",
      "task": "Add focused tests",
      "depends_on": [],
      "write_scope": ["tests/"],
      "expected_artifact": "test_patch",
      "max_steps": 8
    }
  ]
}
```

The scheduler validates unique ids, known dependencies, acyclicity, bounded
worker count, per-task `max_steps` (2..32), normalized relative scopes, and
known tool names. The worker uses the lower of the global and task step budgets.
Read-only tasks have an empty `write_scope`; a write task must declare at least
one scope.

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

`fanout_checkpoint.json` is written atomically before work starts and after each
batch. It records plan digest, base commit, accepted task ids, patch paths and
SHA-256 values. A resume run must match plan digest and base commit, verify every
accepted patch hash, reapply those patches to a fresh integration workspace,
skip completed tasks, and rerun only incomplete tasks. Dependency failures
block downstream tasks but do not discard independent completed artifacts.
All recovered artifacts are validated and replayed in a disposable worktree
first; only the resulting combined diff is applied to the real integration
workspace, preventing a late invalid artifact from leaving a partial restore.

Worker human threads use plan digest, base commit, and task id, so a persisted
clarification answer survives selective worker rerun. Per-operation manual write
approval is different: operation identity currently contains an ephemeral
worktree path. Live write fanout therefore rejects `--no-auto-approve-writes`
instead of claiming that such approvals can be safely replayed. Use
single/sequential mode for that authorization boundary.

Candidate patch collection is shared across run, benchmark, tool, coordinator,
and fanout paths. It captures tracked changes plus untracked text/binary source
files, while excluding untracked `.agent_forge` runtime artifacts. The finalizer
runs in its own disposable worktree with the integration patch left visible to
`git_diff`. The runtime compares the complete binary patch before and after
verification; any verifier-created mutation blocks the decision and is discarded.

Every worker worktree is created from the recorded `base_head`. It deliberately
does not inherit uncommitted files or index state from another checkout. A write
fanout rejects a dirty integration workspace; callers that need draft changes
must first turn them into an explicit, versioned seed rather than relying on
ambient filesystem state.

## Evidence and Acceptance

- Human-input tests prove no model/tool side effect occurs while waiting, a
  response is persisted, and resume context contains the answer.
- Fanout tests use real `AgentLoop` instances with deterministic LLM fixtures,
  separate worktrees, measured overlap, deterministic merge, and selective
  resume.
- A real-provider smoke uses two read-only workers and records concurrent
  wall-clock and aggregate usage without modifying the repository.
- Fanout metrics separate this run's workers/finalizer from resumed historical
  usage and full evidence-chain usage. Current worker time and wall time remain
  separate rather than being presented as measured speedup.
- Public docs distinguish sequential role coordination, live fanout, real
  side-effect approval, durable clarification, and official evaluation.

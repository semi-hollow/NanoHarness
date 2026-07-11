# Evaluation Experiments and OCI Execution

This design connects runtime behavior to evidence that can be compared across
runs. It deliberately avoids hard-coded benchmark claims: every number in a
scorecard must be derived from a trace, usage artifact, candidate patch, or
official evaluator report.

## Goals

1. Turn a fixed five-case SWE-bench Lite regression set into a machine-readable
   scorecard covering patch reachability, local validation, official outcomes,
   tokens, cost, latency, tool failures, and failure classes.
2. Compare two complete benchmark runs as a paired ablation only when dataset,
   split, provider/model identity, and case ids match.
3. Parse official SWE-bench per-case reports instead of inferring correctness
   from the evaluator process exit code.
4. Add an OCI-backed execution mode that keeps the existing runtime policy
   chain while executing command and diagnostics tools inside a constrained,
   short-lived container over an isolated repository snapshot.

## Evidence Flow

```text
SWE-bench case
  -> isolated checkout
  -> AgentLoop + governed tools
  -> trace.json + usage.json + patch.diff
  -> optional official SWE-bench report.json
  -> case evidence model
  -> scorecard.json / scorecard.md
  -> paired ablation.json / ablation.md
```

The evidence model keeps three levels separate:

- Candidate patch: a non-empty diff exists.
- Local validation: a test-oriented command or unittest diagnostic completed
  successfully. Compilation alone is not correctness evidence.
- Official resolved: a per-case SWE-bench report explicitly records
  `resolved: true`.

If the official process exits successfully but no per-case result is found, the
case is `official_eval_incomplete`, not resolved. Harness errors, missing
reports, empty patches, and unresolved patches remain distinct outcomes.

## Scorecards and Ablations

Each benchmark run writes a scorecard with per-case rows and aggregate totals.
Rates use explicit denominators; official resolved rate is `null` when no case
was officially evaluated rather than the misleading value `0%`.

The ablation comparator consumes two run directories. It rejects mismatched
datasets, splits, provider/model identities, or case sets. The report presents
paired deltas and always states that a single run per variant does not estimate
stochastic variance. Tool-routing ablations expose either all registered tools
or task-aware routed tools, while path, command, approval, and sandbox policies
remain enabled in both variants.

Official quality deltas use only cases that have resolved/unresolved evidence
on both sides. If one run adds or loses official evaluation coverage, the report
labels that case as an evidence change and marks full-set official correctness
as not comparable; it never converts a denominator change into an improvement.

## OCI Execution Boundary

`ExecutionEnvironment` remains the runtime interface. OCI mode creates an
isolated git worktree snapshot, starts a named container with a read-only root
filesystem, drops Linux capabilities, enables `no-new-privileges`, applies
CPU/memory/PID limits, and disables container networking when the run policy is
`deny`. The snapshot is mounted read-write at `/workspace` so file tools and
container commands observe the same repository state.

Both repository runs and SWE-bench case runs use this adapter. Benchmark
scorecards record execution mode, network policy, retention policy, image, and
resource limits. They also aggregate the runtime-reported immutable image IDs;
the ablation comparator rejects drift in those fields unless execution
environment is the explicitly declared experiment factor.

Command and unittest diagnostics execute through the environment adapter.
Ordinary file tools still run in the host process under `WorkspaceSandbox` and
can only access the mounted snapshot. This is stronger process isolation than
local/worktree mode, but it is not a claim of hostile multi-tenant security.

The environment manifest records image identity, container id, resource and
network policy, start command, command history, and cleanup policy. Cleanup
always attempts forced container removal; the snapshot follows the existing
keep/remove policy. A recreate command is retained only when the snapshot is
also retained, so the manifest does not advertise a stale replay path.

## Verification Contract

- Official result fixtures cover resolved, unresolved, error, empty-patch, and
  missing-report outcomes.
- Scorecard tests prove that candidate patches and official resolutions use
  different denominators.
- Ablation tests reject incomparable runs and compute paired metric deltas.
- OCI tests assert the actual runtime command, limits, mount, network policy,
  command delegation, manifest evidence, and cleanup without requiring Docker
  in unit-test environments.
- A real OCI smoke check remains environment-dependent and must be reported as
  skipped when no compatible container runtime is installed.

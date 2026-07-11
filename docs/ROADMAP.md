# Roadmap

NanoHarness stays deliberately compact. Roadmap items must strengthen runtime
control, reproducible evaluation, or evidence quality; feature breadth alone is
not a reason to add a subsystem.

## Near Term

1. Parse official SWE-bench per-case results and map them to
   `official_eval_passed` or `official_eval_failed` without inferring outcomes
   from process exit codes.
2. Add dataset manifests, explicit redaction policies, and schema migration for
   exported run evidence.
3. Wire dependency-aware fanout into one read-only AgentLoop profile and report
   conflicts, latency, token cost, and merge decisions.
4. Add a container-backed execution environment adapter while preserving the
   current `ExecutionEnvironment` interface and local/worktree modes.

## Design Debt

- Consolidate UI, diagnostics, and reports around one artifact locator.
- Unify overlapping command, path, and execution-environment policy summaries.
- Promote repeated provider compatibility fixes into a versioned transport
  compatibility layer.
- Keep failure rules ordered from environment and evidence failures to agent
  behavior failures, with a regression case for every priority conflict.

## Acceptance Rule

A roadmap item is complete only when it changes a real runtime path, leaves a
machine-readable artifact, has a focused regression test, and states what the
new evidence does not prove.

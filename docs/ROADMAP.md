# Roadmap

NanoHarness stays deliberately compact. Roadmap items must strengthen runtime
control, reproducible evaluation, or evidence quality; feature breadth alone is
not a reason to add a subsystem.

## Near Term

1. Repeat fixed-set ablations across multiple seeds and retain confidence-aware
   aggregate evidence instead of one-run conclusions.
2. Add dataset manifests, explicit redaction policies, and schema migration for
   exported run evidence.
3. Repeat matched serial/fanout plans across multiple runs and report confidence
   around latency, token cost, conflict rate, and verifier outcomes.
4. Add a real OCI smoke lane and an image contract for project-specific
   dependencies without making Docker a unit-test prerequisite.

## Design Debt

- Consolidate UI, diagnostics, and reports around one artifact locator.
- Unify overlapping command, path, and execution-environment policy summaries.
- Promote repeated provider compatibility fixes into a versioned transport
  compatibility layer.
- Define a stable operation identity before allowing per-operation manual write
  approvals to cross ephemeral fanout worktrees; current fanout fails fast for
  that combination.
- Detect and prune abandoned run-owned worktrees after hard process termination
  without touching user-created worktrees.
- Keep failure rules ordered from environment and evidence failures to agent
  behavior failures, with a regression case for every priority conflict.

## Acceptance Rule

A roadmap item is complete only when it changes a real runtime path, leaves a
machine-readable artifact, has a focused regression test, and states what the
new evidence does not prove.

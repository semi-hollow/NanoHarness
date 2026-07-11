# Changelog

## 0.5.0 - 2026-07-11

- Added per-case official SWE-bench result parsing with explicit resolved,
  unresolved, error, empty-patch, and incomplete outcomes.
- Added a fixed five-repository regression set, denominator-aware scorecards,
  and matched-run `forge eval ablation` reports.
- Added observable `task-aware` versus `all` tool-routing experiments while
  preserving the same runtime safety chain.
- Added OCI-container execution over isolated snapshots with network/resource
  limits, capability removal, read-only roots, command delegation, and replay
  evidence in the environment manifest.

## 0.4.0 - 2026-07-11

- Added human outcome and failure-label capture for completed runs.
- Added privacy-conscious JSONL export for trace, tool-policy, environment,
  evaluation, and feedback evidence.
- Connected local/detached-worktree execution modes to the public run and
  resume commands, with environment manifests and patch preservation.
- Reframed the workbench and documentation around runtime and evaluation
  evidence.
- Added a stable runtime capability guide, feedback-loop design note, and
  explicit roadmap boundaries.

## Earlier Development

Earlier iterations established the canonical AgentLoop, context construction,
governed tools, human approval, operation ledger, checkpoint resume,
SWE-bench-shaped runs, failure taxonomy, direct baseline comparison, and
artifact-based multi-agent coordination.

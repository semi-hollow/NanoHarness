# Changelog

## 0.6.0 - 2026-07-12

- Replaced synthetic `ask_human` behavior with atomic pending/responded/cancelled
  requests, a same-turn side-effect barrier, `waiting_human` checkpoints, and
  `forge respond` plus resume.
- Added validated live fanout over real isolated AgentLoop workers, declared and
  actual write-scope gates, deterministic binary patch integration, and an
  isolated read-only finalizer with candidate-diff visibility and a pre/post
  mutation gate. Plans now carry validated per-task step budgets beneath the
  global runtime ceiling.
- Added incremental fanout checkpoints, plan/base identity checks, patch hashes,
  stable worker clarification threads, selective rerun of incomplete tasks, and
  separate current/resumed/evidence-chain usage accounting.
- Unified candidate diff collection across runs, benchmarks, tools, and
  coordinators so tracked and untracked source files share one evidence path
  while untracked `.agent_forge` artifacts remain excluded.
- Added a bounded fanout UI/CLI surface, safe sample plan, focused regression
  coverage, semantic real-provider smoke assertions, and updated
  capability/learning/failure documentation.
- Tightened the local workbench for desktop/mobile evidence review with stable
  navigation, readable tables, bounded controls, and fanout artifact views.

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

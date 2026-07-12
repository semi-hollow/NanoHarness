# Multi-Agent Harness

Agent Forge includes a coordinator-driven multi-agent harness. The default role
workflow is intentionally simple and sequential; a separate fan-out scheduler
now covers dependency-safe parallel plan batches without changing the canonical
`AgentLoop` runtime.

## Design

The canonical runtime remains `AgentLoop`. Multi-agent execution is an outer
orchestrator:

```text
MultiAgentCoordinator
  -> AgentLoop(role=Implementer or Researcher)
  -> ArtifactStore writes role output
  -> AgentLoop(role=Reviewer)
  -> ArtifactStore writes review
  -> AgentLoop(role=Verifier)
  -> ArtifactStore writes verification
  -> optional bounded revision round
  -> multi_agent_summary.json + multi_agent_report.md
```

Agents do not freely chat with each other. They communicate through explicit
artifacts under:

```text
.agent_forge/runs/<run-id>/multi_agent/artifacts/
```

This keeps state replayable and explainable.

Each `RoleSpec` can also define a revision-round tool allowlist. For example,
`research_report` lets `Researcher` inspect local/MCP evidence on the first
round, then makes revision rounds artifact-only. That keeps revision focused on
review findings instead of letting the model fall into repeated evidence
collection.

The coordinator also applies a small artifact-quality gate. If a role returns
raw provider-specific tool-call markup instead of the expected artifact, the run
does not pretend the artifact is valid; it triggers a bounded revision round.
Artifact handoff is rendered newest-first so reviewers see the current candidate
before older audit history consumes context budget.

## Dependency-Aware Fan-Out

For plan-style work, `agent_forge/multi_agent/live_fanout.py` connects the
dependency scheduler to real isolated workers:

```text
Plan tasks
  -> validate DAG / paths / tools / artifact names
  -> dependency-ready, conflict-free batches
  -> one worktree + AgentLoop + LLM + trace per worker
  -> actual touched-file and patch SHA checks
  -> deterministic git apply into the integration workspace
  -> isolated read-only FanoutVerifier
  -> checkpoint / summary / report / integration.patch
```

This answers a different question from `coding_fix`: when a user has seven or
eight independent tasks, which can run together, and which require serial
ownership? Declared overlap is serialized. Undeclared overlap, scope escape, or
patch-apply failure stops at `conflict_resolution_required`; no model silently
widens scope. Checkpoints can restore hash-verified accepted patches and rerun
only incomplete workers.

Live fanout is local concurrency, not distributed serving. It consumes an
explicit JSON plan rather than asking a model to invent an unbounded swarm.

## Profiles

### `coding_fix`

Roles:

- `Implementer`: inspect, patch, run focused validation when allowed.
- `Reviewer`: read-only review of candidate patch/output.
- `Verifier`: run allowed validation or mark validation as blocked.

Reviewer and verifier must output one of:

- `PASS`
- `NEEDS_REVISION`
- `BLOCKED`

`NEEDS_REVISION` triggers another Implementer round until
`--max-revision-rounds` is reached.

### `research_report`

Roles:

- `Researcher`: draft a source-backed report.
- `SkepticalReviewer`: identify unsupported claims and missing caveats.
- `FactVerifier`: verify major claims against available sources.

The profile works offline by requiring the report to mark source limitations
when live search/fetch is unavailable. If MCP web tools are configured, the
research role can use them through the same tool registry path. Reviewer and
verifier are artifact-only in this first version so their decisions are based
on the draft and cited evidence, not on hidden extra browsing.

## CLI

Single-agent behavior remains the default:

```bash
forge run "fix the failing test" --provider deepseek
```

Multi-agent coding repair:

```bash
forge run "fix the failing test" \
  --agent-mode multi \
  --profile coding_fix \
  --provider deepseek \
  --max-revision-rounds 2
```

Research report:

```bash
forge run "Write a cited research report on current best practices for evaluating multi-agent coding systems. If live search is unavailable, clearly mark source limitations." \
  --agent-mode multi \
  --profile research_report \
  --provider deepseek \
  --max-steps 10 \
  --max-revision-rounds 2
```

Live read-only fanout:

```bash
forge run "audit runtime and safety evidence" \
  --agent-mode fanout \
  --fanout-plan examples/fanout-plan.sample.json \
  --max-workers 2 \
  --provider deepseek
```

Resume an incomplete fanout run:

```bash
forge run "continue the validated task DAG" \
  --agent-mode fanout \
  --fanout-plan path/to/plan.json \
  --fanout-resume .agent_forge/runs/<previous-run-id> \
  --execution-mode worktree \
  --no-keep-worktree \
  --provider deepseek
```

SWE-bench can run the coding profile:

```bash
forge bench swebench --showcase \
  --agent-mode multi \
  --profile coding_fix \
  --provider deepseek
```

SWE-bench can also compare single-agent and multi-agent variants on isolated
workspaces:

```bash
forge bench swebench --showcase \
  --agent-mode compare \
  --profile coding_fix \
  --provider deepseek \
  --max-revision-rounds 2
```

Compare mode writes `comparison.json` and `evaluation_report.md` for each case.
Treat the report as evidence for quality/cost tradeoffs, not as a global claim
that multi-agent is always better.

## Non-Goals

This version does not implement:

- Claude / Anthropic provider compatibility.
- Raft, quorum, blockchain, decentralized peer-to-peer agents, or swarm learning.
- Distributed queues, remote workers, or peer-to-peer agent chat.
- Automatic LLM conflict resolution across undeclared or overlapping writes.
- Per-operation manual write approval across ephemeral fanout worktrees; use
  single/sequential mode when that authorization boundary is required.
- Full SaaS/distributed serving.
- Heavy frontend changes.

The point is a mature, inspectable coordinator-driven harness, not a large
distributed-agent product.

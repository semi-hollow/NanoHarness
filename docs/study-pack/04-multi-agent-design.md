# 04 Multi-Agent Design

## What This Project Implements

The multi-agent path is not a chatroom of autonomous agents. It is a supervised coding workflow with production-shaped contracts:

```text
SupervisorAgent
  -> OwnershipPlan
  -> TaskGraph
  -> TaskScheduler
  -> AgentSpec
  -> AgentRuntime
  -> AgentLoop
  -> TaskArtifact
```

Each role is an `AgentSpec`:

- Planner: plan-only worker
- CodingAgent: read and patch owned files
- TesterAgent: run validation
- ReviewerAgent: inspect diff/status evidence

## Why Multi-Agent Exists

Multi-agent is useful when one model loop needs role separation:

- different tool permissions
- different prompts
- different file ownership
- independent validation
- review gates
- conflict-aware scheduling
- auditable handoff

It is not automatically better than single-agent. For small tasks, one strong `AgentLoop` is simpler and cheaper.

## What Makes It Production-Shaped

`TaskGraph` is a DAG, not a list of print statements. Nodes have dependencies, expected artifacts, read files, write files, status, error, and result.

`TaskScheduler` can run ready nodes in parallel when their write sets do not conflict. If two nodes write the same file, they are split into different batches. This is the minimum safe design before adding full patch merge logic.

`OwnershipPlan` records which agent owns which files. In a larger system, this becomes the basis for worktree isolation or merge arbitration.

`TaskArtifact` is the handoff format. Subagents should not pass only prose; they pass typed evidence such as plan, patch, diagnostics, review finding, or risk summary.

## Why Multi Mode Still Looks Linear In The Demo

The demo task is naturally dependency-bound:

```text
plan -> code -> test -> retry if failed -> retest -> review
```

That does not mean the architecture only supports a hardcoded chain. The scheduler is graph-based; this particular example has dependencies that serialize most nodes. In a larger task, independent code/test/review nodes with disjoint write ownership could run concurrently.

## Technical Discussion Answer

For multi-agent questions, say:

> I use supervised multi-agent, not unconstrained agent-to-agent chatter. The supervisor owns task graph, dependency, ownership, and validation. Subagents run through the same AgentLoop but have different AgentSpec prompts, tool allowlists, budgets, and file ownership. Outputs are TaskArtifacts, not raw messages. This prevents context pollution, tool overreach, and infinite A2A loops.

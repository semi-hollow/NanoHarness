# NanoHarness Multi-Agent Roadmap

> Status date: 2026-08-19
> Purpose: keep the Multi-Agent scope stable for capability completion and prevent feature creep.

## 0. Scope and version policy

This roadmap is intentionally **Multi-Agent focused**.

The later evaluation-driven optimization loop remains in this roadmap only because its first target is the Multi-Agent subsystem itself: Planner, Handoff, Recovery/Replan, Finalizer, and Multi-Agent evaluation evidence.

If that optimization loop later expands to the entire NanoHarness runtime (Tool Router, Context, Skill, Memory, etc.), it should be split into a separate Harness-level roadmap instead of continuing to grow this file.

### Branch policy

- **Current stable branch:** `stable/v0-20260818`
  - Frozen after the semantic naming refactor and value-preserving evidence migration.
  - Does not receive V1 capability work.
- **Immutable snapshot tags:**
  - `v0-stable-20260818` preserves the original V0 baseline.
  - `v0-stable-20260819` preserves the post-refactor V0 frozen head.
- **V1 development branch:** `feature/multi-agent-v1`
  - Contains the completed V1 capability closure; promotion remains a separate decision.

---

# V0 — Current Baseline `[DONE]`

Current capability:

```text
Manual / External Typed FanoutPlan
        ↓
FanoutPlan Validation
        ↓
Dependency Ready Levels
        ↓
write_scope Conflict-Free Batches
        ↓
ThreadPool Execution
        ↓
Independent Worker AgentLoops
        ↓
Independent Git Worktrees
        ↓
Candidate Diff + LiveSubagentResult
        ↓
Conflict Gates
        ↓
Stable Sequential Integration
        ↓
Read-only Finalizer
```

Implemented foundations:

- Typed `FanoutPlan`
- Explicit dependency DAG
- Ready-level scheduling
- Declared `write_scope` conflict-free batching
- Independent Worker `AgentLoop`
- Independent `AgentRunSession` / WorkingMemory / Conversation
- Independent Git worktree
- Scoped tool surface
- Scope-violation detection
- Actual `touched_files` dynamic conflict detection
- Merge applicability gate
- Stable merge order
- Cumulative integrated workspace for downstream workers
- Fanout checkpoint / resume
- Read-only Finalizer
- Trace / usage / candidate diff evidence

### V0 known gaps

```text
Natural Language Task
        ↓
       ???

FanoutPlan
```

The current system mainly implements the **post-planning orchestration/execution half**.

Missing capability-completion gaps:

- automatic task decomposition;
- Single vs Multi-Agent strategy decision;
- structured semantic handoff;
- explicit acceptance criteria;
- bounded adaptive recovery;
- bounded semantic replan;
- criteria-aware completion judgment.

---

# V1 — Multi-Agent Capability Completion `[DONE]`

## Goal

Upgrade V0 from:

```text
Manual Plan
→ Static Orchestration
```

to:

```text
Natural Language Task
        ↓
Planner
        ↓
Single / Fanout Strategy Gate
        ↓
Validated Typed FanoutPlan
        ↓
Isolated Workers
        ↓
Structured Handoff
        ↓
Deterministic Integration
        ↓
Bounded Recovery / Replan
        ↓
Criteria-aware Finalizer
```

V1 is an **V1 MVP**, not an industrial Multi-Agent platform.

---

## V1.1 — Planner + Single/Multi Strategy Gate `[DONE]`

### Required

Add a compact planning contract:

```text
PlanningDecision
├── mode: single | fanout
├── reason
├── global_acceptance_criteria
└── tasks[]
```

Each planned task contains only necessary fields:

```text
id
task
depends_on
write_scope
allowed_tools
acceptance_criteria
max_steps
```

Planner inputs:

- original task;
- bounded repository overview / repo map;
- available tools;
- max fanout task count;
- necessary budget constraints.

Rules:

- LLM proposes.
- Runtime validates.
- Fanout plans must still pass deterministic `FanoutPlan` validation.
- Local/highly coupled tasks should be allowed to stay `single`.
- Malformed/invalid planner output must fail safely; no unbounded planner retry.
- Existing manual `FanoutPlan` API remains backward compatible.

### Architecture questions covered

- Who decomposes the task?
- Who decides Single vs Multi?
- Who creates dependencies?
- How do you prevent planner hallucination from controlling execution?
- Why not always use Multi-Agent?

---

## V1.2 — Acceptance Criteria + Structured Worker Handoff `[DONE]`

### Acceptance Criteria

Acceptance criteria must flow through:

```text
Planner
→ Task Contract
→ Worker
→ Worker Evidence
→ Finalizer
```

Backward compatibility:

- old plans without `acceptance_criteria` remain valid;
- default is an empty list.

### Structured Handoff

Keep Worker contexts isolated.

Do **not** share:

- full Conversation;
- full WorkingMemory;
- AgentRunSession;
- private model history.

Add a small semantic handoff contract:

```text
WorkerHandoff
├── task_id
├── status
├── summary
├── touched_files
├── validation_evidence
├── unresolved_issues
└── artifact_path
```

Prefer deterministic projection from existing Worker result/artifact data.
Do not add another LLM call just to summarize handoff.

Communication model:

```text
Code State
→ Integrated Workspace

Semantic State
→ Compact WorkerHandoff

Private Context
→ Isolated
```

A dependent Worker receives:

- its own task;
- its acceptance criteria;
- handoffs from its declared dependencies only;
- the latest integrated workspace.

### Architecture questions covered

- Do Agents share complete context?
- How do Agents communicate?
- How do you control context contamination?
- What exactly gets handed off?
- How does a downstream Agent know what upstream Agents changed or validated?

---

## V1.3 — Bounded Recovery and Replan `[DONE]`

Global hard boundaries:

```text
max_worker_retry <= 1
max_plan_replan_rounds <= 1
```

No infinite agent ping-pong.

### V1.3.1 — Merge-Applicability Conflict → Serialized Rerun `[DONE]`

Scenario:

```text
A || B
↓
A merges successfully
↓
B old candidate becomes non-applicable
```

Recovery:

```text
discard B old candidate
        ↓
latest integrated workspace
        ↓
fresh Worker B
        ↓
re-read / re-reason / re-edit
        ↓
B-v2 candidate
        ↓
normal validation + merge
```

Principle:

> optimistic parallel execution + serialized conflict recovery

Do not ask an LLM to textually repair the stale old patch.

Retry at most once.

---

### V1.3.2 — Retryable Worker Failure → One Retry `[DONE]`

Retry only failures that can reasonably be treated as transient/retryable.

Do not blindly retry:

- scope violations;
- deterministic permission violations;
- invalid plan structure;
- other clearly non-retryable failures.

Reuse existing failure/status semantics where possible.
Do not create a second giant failure taxonomy.

---

### V1.3.3 — One-Round Remaining-Plan Replan `[DONE]`

Replan is bounded and only replaces unfinished work.

Invariant:

```text
Completed + successfully merged tasks
= frozen history
```

Replanner input:

- original goal;
- current/original plan;
- completed task IDs;
- completed WorkerHandoffs;
- failure/conflict evidence;
- current integrated-state summary;
- failed/blocked/remaining work.

Replanner output:

- a replacement graph for **remaining work only**.

The Runtime must again perform deterministic validation.

If the replan is malformed or invalid:

```text
controlled abort / partial failure
```

No second semantic replan.

---

### V1.3.4 — Fail-Closed Conflict Boundaries `[DONE]`

Keep these conservative rules:

- `scope_violation` → fail closed, no blind retry;
- actual touched-file conflict remains detectable;
- no LLM semantic merge resolver;
- no unlimited recovery loop;
- no completed-task rollback in V1.

This sub-phase exists to make the recovery boundaries explicit and reviewable.

---

## V1.4 — Criteria-Aware Read-Only Finalizer `[DONE]`

Reuse the existing read-only Finalizer AgentLoop.

Do not build a second Finalizer framework.

Finalizer input should include:

- original goal;
- global acceptance criteria;
- per-task acceptance criteria;
- WorkerHandoffs;
- integrated diff/workspace;
- validation evidence.

Expected reasoning:

```text
criterion 1 → PASS / FAIL / UNKNOWN
criterion 2 → PASS / FAIL / UNKNOWN
...
↓
Final Decision:
PASS / NEEDS_REVISION / BLOCKED
```

Runtime hard facts override LLM prose.

Examples:

- Finalizer modifies workspace → cannot PASS.
- Required candidate diff missing → cannot PASS.
- Known validation failure → cannot be converted to PASS by language alone.

### Architecture questions covered

- Who decides the overall task is complete?
- Why can’t Worker self-report be trusted?
- What evidence does Finalizer consume?
- What prevents a verifier from quietly repairing code?

---

## V1.5 — Deterministic Mechanism Validation `[DONE]`

V1 does **not** run a large Multi-Agent benchmark.

V1 must contain five deterministic showcase/test scenarios:

### Case 1 — Single Gate

A local/highly coupled small task.

Expected:

```text
Planner → single
```

### Case 2 — Independent Parallel Fanout

Two independent files/modules.

Expected:

```text
Planner → fanout
A || B
→ isolated execution
→ stable integration
→ Finalizer PASS
```

### Case 3 — Dependency + Handoff

```text
A || B
  ↓
  C
```

C must receive A/B compact handoffs and see their integrated code state.

### Case 4 — Merge Conflict Serialized Recovery

```text
A || B
↓
A merge
↓
B stale / non-applicable
↓
fresh B rerun on latest integrated state
↓
B-v2 merge
```

### Case 5 — Worker Failure / Bad Remaining Plan

A controlled failure triggers:

```text
retry once
or
remaining-plan replan once
```

Then either succeeds or exits through a controlled terminal state.

No infinite loop.

---

## V1 Definition of Done

V1 is complete only when all items below are true:

- [x] Natural-language task can choose Single or Fanout.
- [x] Fanout mode can generate and validate a typed `FanoutPlan`.
- [x] Manual/external FanoutPlan workflow remains backward compatible.
- [x] Worker contexts remain isolated.
- [x] Dependency workers receive compact structured handoffs.
- [x] Acceptance criteria flow from planning to final verification.
- [x] Merge-applicability conflict supports one serialized rerun on latest integrated state.
- [x] Retry/replan has explicit hard boundaries.
- [x] Completed/merged tasks remain frozen during replan.
- [x] Finalizer judges completion using criteria + evidence and remains read-only.
- [x] Five deterministic mechanism cases pass.
- [x] Existing relevant tests do not regress.
- [x] Trace/artifacts show enough evidence to prove planning, handoff, recovery, and verification occurred.

---

# V2 — Multi-Agent Quantitative Evaluation `[PLANNED]`

V2 is explicitly **not part of the current one-week implementation**.

Goal:

Measure whether Multi-Agent provides measurable value, not just mechanism correctness.

Frozen cohort:

```text
Golden-10
```

Compare:

```text
A. Single Agent

B. Static Fanout
   manual plan

C. Adaptive Fanout
   planner + handoff + bounded recovery
```

Metrics:

- success rate;
- wall time;
- total tokens;
- LLM calls;
- conflict rate;
- recovery rate.

Important:

- Do not claim Multi-Agent improves Pass@1 before this experiment exists.
- Existing Single-Agent SWE-bench evaluation already demonstrates NanoHarness evaluation capability.
- V1 only proves orchestration correctness and recovery mechanisms.

---

# V3 — Multi-Agent Evaluation-Driven Optimization Loop `[PLANNED]`

This phase remains in the **Multi-Agent roadmap** because its initial target is the Multi-Agent subsystem.

Pipeline:

```text
Multi-Agent Campaign Evidence
        ↓
Deterministic Failure Aggregator
        ↓
FailureReport
        ↓
LLM Improvement Analyzer
        ↓
ImprovementProposal
        ↓
ExperimentSpec
```

### FailureReport

Deterministic facts only.

Candidate dimensions:

- planner failure;
- bad decomposition;
- dependency error;
- write-scope miss;
- worker failure;
- handoff insufficiency;
- dynamic conflict;
- merge conflict;
- finalizer rejection;
- retry recovery;
- replan recovery.

### ImprovementProposal

LLM may propose:

- root-cause hypothesis;
- target layer;
- proposed change;
- expected effect;
- risk/tradeoff;
- recommended experiment.

The LLM must cite evidence from the deterministic report / representative traces.

### ExperimentSpec

Generate a delayed experiment contract:

```text
baseline
candidate
frozen cohort
target metrics
regression metrics
success gate
```

### Hard boundary

V3 does **not**:

- automatically modify NanoHarness source;
- automatically apply a Harness patch;
- automatically merge to the main branch;
- let the Agent judge its own improvement.

Source changes remain engineer-controlled.

If this loop later expands from Multi-Agent optimization to all Harness subsystems, move it into a separate Harness-level roadmap.

---

# V4 — Backlog `[BACKLOG]`

Only implement if real usage or review exposes a meaningful gap.

Possible examples:

- turn-level Skill resource disclosure;
- cross-model Golden evaluation;
- richer Planner evidence;
- remote execution/sandbox backend;
- refined Multi-Agent failure taxonomy.

No V4 work is included in the completed V1 capability closure.

---

# Explicitly Out of Scope

Do not implement as part of this roadmap unless the project direction changes:

- Auto Research as a second product line;
- Agent peer-to-peer group chat;
- unrestricted A2A;
- voting / consensus systems;
- recursive supervisor trees;
- unlimited dynamic agent spawning;
- LLM semantic patch merge agent;
- Redis/MQ distributed scheduler;
- Kubernetes worker platform;
- cloud multi-tenancy;
- organization memory;
- knowledge graph;
- Skill marketplace;
- automatic Harness self-modification.

---

# Project Positioning

After V1, the intended concise positioning is:

> NanoHarness Multi-Agent uses a centralized deterministic coordinator rather than shared-context agent chat. A Planner proposes Single/Fanout strategy and a typed task DAG; the Runtime validates it, runs isolated AgentLoops in separate worktrees, communicates through integrated code state plus compact structured handoffs, performs deterministic conflict checks and bounded recovery, then uses a read-only criteria-aware Finalizer to judge completion.

V2 and V3 remain future work until their evidence exists.

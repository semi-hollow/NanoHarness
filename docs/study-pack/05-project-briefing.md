# 05 Project Briefing

## Project Pitch

> I built Agent Forge, a production-style CodingAgent runtime core. The focus is
> not model training or UI, but the runtime layer that turns an LLM into a
> controllable coding system: context engineering, model gateway, tool
> governance, runtime hooks, execution environment, task state, recovery,
> trace, usage, eval regression, review workflow, and supervised multi-agent
> orchestration.

## Two Strongest Design Points

1. Context is a policy layer, not prompt concatenation.
   The runtime ranks files, previews code, retrieves lexical matches, compresses memory, preserves attention anchors, and detects topic shifts before inheriting session memory.

2. Tool execution is governed by runtime control, not model trust.
   Tool calls pass schema validation, guardrails, permission, sandbox, failure classification, retryability decision, and trace.

## Questions This Project Directly Answers

- Agent architecture and module split
- Workflow vs autonomous Agent
- Tools vs Workflow vs Agent
- ReAct engineering implementation
- Tool call / observation protocol
- Tool schema design and miscall reduction
- Agent loop deadlock and repeated-call handling
- Timeout, max step, failure budget, and cost budget
- Permission, approval, audit, trace, and replay
- Short-term memory, summary memory, session memory
- Context overflow and compression
- Topic shift and context inheritance
- Multi-agent supervisor, shared state, artifact handoff
- Subagent validation and output gating
- Agent infra: state management, tracing, tool log, resume

## Questions To Answer As Extensions

These topics are relevant to senior technical walkthroughs but should not be forced into this CodingAgent codebase.

RAG / GraphRAG:

> This project uses lightweight lexical retrieval because it is a coding-agent runtime, not a knowledge-base product. In production RAG I would add document parsing, chunking, metadata, BM25 + vector hybrid retrieval, reranking, versioning, freshness, and answer-grounding verification. GraphRAG is useful when the question depends on multi-hop entity relations, not just semantic similarity.

MCP / A2A / Skills:

> This project has a local ToolRegistry. MCP would externalize tool discovery and invocation across processes. Skills are higher-level packaged capabilities, usually prompt plus tools plus examples. A2A is for agent-to-agent communication, but I would not allow open-ended A2A without supervisor limits, budgets, and stop conditions.

MCTS / ToT / ReWOO:

> I know these as planning patterns. For production coding tasks, I would start with ReAct plus plan-execute and add tree search only for high-value ambiguous tasks because branching multiplies cost and latency.

Model training:

> This project does not train models. Agentic SFT/RL would use tool-call trajectories, mask observation tokens when training the assistant side, and optimize for task success, tool correctness, and safe action choice. That is a separate model-layer problem from this runtime-layer project.

Multimodal:

> Multimodal agents add media ingestion, image/video tokenization, async long-running jobs, and artifact storage. The control-plane ideas still apply, but this repository intentionally stays text/code-only.

## Deep Technical Follow-Ups

Why not fully autonomous?

> Coding has high blast radius. I use autonomy inside bounded loops, but deterministic runtime policy controls tools, permissions, budgets, and validation.

Why not only workflow?

> Fixed workflow is controllable but brittle. ReAct is useful inside nodes where the system must inspect files, adapt to tool failures, and decide the next action from observations.

How do you avoid hallucination?

> I reduce ungrounded generation by forcing file inspection, feeding tool observations into the next turn, checking output claims, requiring validation before saying tests passed, and recording trace for audit.

How do you handle long context?

> I keep an attention sink, rank files, preview bounded code, compress observations into summary memory, decide topic inheritance, and drop stale memory on topic shift.

How do you stop loops?

> StepController tracks max steps, consecutive failures, repeated tool calls, timeout, and cost. It classifies failures and emits recovery hints. Non-retryable failures stop instead of looping.

How would you make this closer to Codex?

> Add a stronger interactive CLI, worktree-based parallel edits, richer patch review, LSP diagnostics, MCP integration, enterprise sandboxing, SWE-bench-style evaluation, and IDE integration. I intentionally left those out to keep this project focused on the core runtime.

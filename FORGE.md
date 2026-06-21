# Agent Forge Contributor Notes

## Project Direction

Agent Forge is now a SWE-bench-oriented CodingAgent harness. Keep the main story
focused on:

1. public benchmark cases;
2. clean repo checkout at base commits;
3. AgentLoop-driven tool execution;
4. SWE-bench-compatible `predictions.jsonl`;
5. trace, usage, and result cards;
6. optional official SWE-bench harness evaluation.

Do not reintroduce self-authored benchmark narratives as the primary proof.
Small fixtures under `examples/` are smoke checks only.

## Preferred Commands

```bash
forge doctor
forge run "fix the failing test in this repository" --provider mock
forge bench swebench --limit 1 --provider deepseek --direct-baseline
forge report latest
forge replay latest
scripts/verify.sh
```

## Editing Guidelines

- Keep the public entrypoint goal-based: `run`, `bench`, `report`, `replay`,
  `doctor`, `tui`.
- Do not reintroduce public `single/multi/workflow` modes; user-facing commands
  should stay goal-based.
- Keep generated artifacts under `.agent_forge/`.
- Do not commit API keys, provider profiles, raw run traces, or benchmark
  workspaces.
- If a feature does not support the SWE-bench loop or explainability of that
  loop, question whether it belongs in the project.

## Runtime Truths

- `AgentLoop` is the canonical execution path.
- `agent_forge/bench` owns benchmark loading, checkout, predictions, and result
  cards.
- `TraceRecorder` is the source of truth for replay and usage reports.
- `ModelGateway` is the only provider boundary used by the runtime.
- `ToolRegistry`, `CommandPolicy`, and `WorkspaceSandbox` are the tool safety
  boundary.

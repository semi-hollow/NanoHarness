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

Do not reintroduce self-authored benchmark narratives, teaching fixtures, or
simulated-model product paths as proof of capability.

## Preferred Commands

```bash
forge doctor
forge run "read this project structure and explain the entrypoints without editing files" --provider deepseek
forge run "阅读这个项目结构并说明入口，不要修改文件" --provider deepseek
forge skills list
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
- Do not add calculator/webhook/tutorial fixtures, simulated LLM product paths, or
  passive sample configs. A capability must affect `forge run`, benchmark
  execution, trace evidence, or real operator workflow.
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
- Built-in coding Skills are part of the real runtime path. They must affect
  prompt context, tool routing, or trace evidence; do not add passive manifests
  that are never used by `AgentLoop`.

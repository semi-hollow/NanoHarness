# Mini Agent Application Cases

These cases are intentionally small. They are not benchmark leaderboards and
they do not replace SWE-bench-shaped coding evidence.

They exercise broader Agent application engineering concerns:

- research workflows need citation quality, source limitations, and unsupported
  claim control;
- ops workflows need policy-sensitive side effects, human approval, recovery,
  and auditable execution summaries.

Each JSON case uses common evaluation dimensions such as `task_success`,
`evidence_quality`, `tool_efficiency`, `recovery_success`,
`human_intervention_count`, and `safety_violation`.

Run a deterministic scorecard from explicit evidence:

```bash
forge eval mini-cases --case research-citation-quality --evidence evidence.json
```

The evaluator is not an LLM judge. It expects evidence such as produced
artifacts, citations, unsupported claim count, tool-call count, human
intervention count, recovery result, and safety violations. Missing or weak
evidence fails the relevant dimension instead of being papered over by prose.

The tool names in a mini-case are declarative evaluation inputs; the evaluator
does not execute an AgentLoop. In the runtime, `ask_human` creates durable
informational requests through `HumanInputStore`. Concrete side-effect approval
remains a separate `ApprovalStore` plus `forge approve` contract.

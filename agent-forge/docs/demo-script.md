# Demo Script

## Before Demo

Use Python 3.11:

```bash
python3.11 -m unittest discover tests
python3.11 -m agent_forge.eval.eval_runner
```

## Run Single-agent Demo

```bash
python3.11 run_demo.py --mode single
```

What to show:

- It reads calculator source and tests.
- It tries one bad patch, observes failure, then retries.
- It runs unittest with `python3.11`.
- It writes `agent_forge_trace.json` and `summary.md`.

## Run Multi-agent Demo

```bash
python3.11 run_demo.py --mode multi
```

What to show:

- Supervisor handoff events.
- Planner/Coding/Tester/Reviewer role split.
- Trace metrics include handoff count.

## Run Workflow Demo

```bash
python3.11 run_demo.py --mode workflow
```

What to show:

- Deterministic workflow is different from LLM-driven agent loop.

## Show Eval

```bash
python3.11 -m agent_forge.eval.eval_runner
sed -n '1,12p' eval_report.md
```

Expected result: 19 total, 19 passed, 0 failed.

## Interview Talk Track

- “This is not a wrapper around an API; the point is the control layer.”
- “The agent loop records context, plan, action, observation, and final answer.”
- “Safety is enforced outside the model through sandbox and command policy.”
- “Eval includes failure and safety cases, not only happy path.”

## Likely Questions

- Why MockLLM?  
  To make tests and demo deterministic.

- Is MCP fully implemented?  
  No, V2 implements a local MCP-style adapter to show the extension boundary.

- Is LSP fully implemented?  
  No, V2 uses AST symbol search as a zero-dependency fallback.

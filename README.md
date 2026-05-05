# NanoHarness

NanoHarness is an Agent Engineering portfolio project. The repository currently contains one main implementation:

- [`agent-forge/`](agent-forge/) - a compact Agent Harness / Agent Engineering Lab for coding-agent interviews, learning, safety, tracing, and evaluation.

The repository name is **NanoHarness** because the long-term direction is a small but complete harness for understanding how agents become controlled execution systems. The current project folder is still named **agent-forge** because it was built first under that name and already has its own package, docs, tests, eval cases, and CI workflow.

## Why Is There an `agent-forge/` Folder?

This is intentional for the current stage.

`agent-forge/` is the real project implementation directory. Keeping it as a subfolder avoids unnecessary churn in:

- Python package paths;
- documentation links;
- eval case paths;
- GitHub Actions working directories;
- existing project history.

For review purposes, treat `agent-forge/` as the project root.

Long term, if this repository remains a single-project repo, a cleanup migration could move `agent-forge/` contents to the repository root or rename the folder to `nanoharness/`. That is a polish step, not a functional blocker.

## Start Here

1. Read the original project source of truth:
   - [`agent-forge/00-项目原始设计方案-source-of-truth.md`](agent-forge/00-%E9%A1%B9%E7%9B%AE%E5%8E%9F%E5%A7%8B%E8%AE%BE%E8%AE%A1%E6%96%B9%E6%A1%88-source-of-truth.md)
2. Read the implementation README:
   - [`agent-forge/README.md`](agent-forge/README.md)
3. Read the capability evidence map:
   - [`agent-forge/docs/capability-evidence-map.md`](agent-forge/docs/capability-evidence-map.md)
4. Read the reviewer guide:
   - [`agent-forge/docs/reviewer-guide.md`](agent-forge/docs/reviewer-guide.md)
5. Read the V2 change archive:
   - [`agent-forge/docs/references/codex-v2-change-archive.md`](agent-forge/docs/references/codex-v2-change-archive.md)

## What Agent Forge Covers

Agent Forge is not a chatbot and not a Claude/OpenCode clone. It is a runnable engineering harness that demonstrates the control layer behind coding agents:

- Agent Loop
- Tool Calling
- Observation feedback
- Workflow vs dynamic Agent execution
- Multi-Agent Supervisor/Subagent flow
- Handoff
- Context Engineering
- Memory and simplified RAG
- Permission and workspace sandbox
- Guardrails
- Human-in-the-loop approval
- Observability and trace JSON
- Metrics summary
- Eval benchmark
- Production-readiness docs
- Interview Q&A and project storytelling material

## Repository Layout

```text
NanoHarness/
  README.md                         # This repository-level guide
  .github/workflows/                # GitHub Actions for Agent Forge
  agent-forge/
    README.md                       # Main implementation README
    00-项目原始设计方案-source-of-truth.md
    run_demo.py
    agent_forge/                    # Python package
    examples/demo_repo/             # Demo coding task
    eval_cases/                     # Executable eval benchmark cases
    scripts/verify.sh               # One-command local verification
    tests/                          # unittest test suite
    docs/                           # Design docs and interview material
    tutorials/                      # nanoAgent-style learning path
```

## Quickstart

Run commands from `agent-forge/`:

```bash
cd agent-forge
scripts/verify.sh
```

The default demos use `MockLLMClient`, so no API key is required.

## Verified Status

Latest local verification recorded during V2 work:

- single-agent demo: passed
- multi-agent demo: passed
- workflow demo: passed
- unit tests: 44 passed
- eval benchmark: 19/19 passed
- Python compile check: passed

See [`agent-forge/docs/run-results.md`](agent-forge/docs/run-results.md) for recorded evidence. Running `scripts/verify.sh` also regenerates local ignored artifacts such as `eval_report.md` and trace JSON files.

## Review Guide

For the full review path, use [`agent-forge/docs/reviewer-guide.md`](agent-forge/docs/reviewer-guide.md).

If you are reviewing this project, start with these questions:

1. Can the demo actually run without a real API key?
2. Does the Agent Loop show tool calls and observations clearly?
3. Are unsafe actions blocked by permission, sandbox, or guardrails?
4. Are traces detailed enough to debug an agent run?
5. Does eval execute real `verify.py` files instead of hardcoding success?
6. Are the current boundaries honestly documented?

Relevant files:

- Runtime: [`agent-forge/agent_forge/runtime/agent_loop.py`](agent-forge/agent_forge/runtime/agent_loop.py)
- LLM clients: [`agent-forge/agent_forge/runtime/llm_client.py`](agent-forge/agent_forge/runtime/llm_client.py)
- Tool registry: [`agent-forge/agent_forge/tools/registry.py`](agent-forge/agent_forge/tools/registry.py)
- Safety: [`agent-forge/agent_forge/safety/`](agent-forge/agent_forge/safety/)
- Context: [`agent-forge/agent_forge/context/`](agent-forge/agent_forge/context/)
- Observability: [`agent-forge/agent_forge/observability/`](agent-forge/agent_forge/observability/)
- Eval runner: [`agent-forge/agent_forge/eval/eval_runner.py`](agent-forge/agent_forge/eval/eval_runner.py)

## Generated Artifacts

Agent demos and eval runs generate local files such as:

- `agent-forge/eval_report.md`
- `agent-forge/agent_forge_trace.json`
- `agent-forge/*_trace.json`
- `agent-forge/summary.md`

These are ignored by git so normal demo runs do not dirty the repository. Use `agent-forge/docs/run-results.md` for checked-in evidence and `scripts/verify.sh` to regenerate local reports.

## GitHub About

Suggested repository description:

```text
A compact Agent Harness for coding-agent runtime, tools, safety, tracing, eval, and interview-ready documentation.
```

## Naming Decision

Current naming:

- **NanoHarness**: repository and long-term project brand.
- **Agent Forge**: current implementation module / subproject.

This is acceptable for now because the implementation is coherent and documented. The most important improvement was adding this repository-level README so GitHub visitors understand where the real project lives.

If you want the cleanest final portfolio shape later, choose one of these:

- Option A: keep `agent-forge/` and describe it as the first harness implementation inside NanoHarness.
- Option B: move `agent-forge/` contents to repo root and make NanoHarness the only visible project name.
- Option C: rename `agent-forge/` to `nanoharness/` and update package/docs/CI paths.

Recommended now: **Option A**. It is clear, low-risk, and preserves the existing working project.

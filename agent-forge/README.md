# Agent Forge

Agent Forge is a compact production-style CodingAgent runtime for learning AI Agent system design. It is not a full Codex clone and intentionally avoids TUI, IDE plugin, PR bot, enterprise integrations, container sandbox, multimodal, and model training. The goal is to make the core runtime easy to run, read, and explain in a senior AI Agent interview.

## What This Project Teaches

- Context engineering: repo map, file ranking, lexical retrieval, selected file previews, token budget, memory summary, and topic-shift handling.
- Agent loop control: plan, LLM call, tool call, observation, recovery, final answer.
- Tool governance: schema validation, permission policy, sandbox path checks, high-risk command blocking, and human approval hooks.
- Runtime reliability: repeated-action detection, retryability classification, max steps, timeout, cost budget, trace, reports, and rollback bundle.
- Multi-agent orchestration: supervisor, role specs, task graph, artifact handoff, ownership, validation, retry, and review.
- Model switching: mock, Ollama, company OpenAI-compatible APIs, or online OpenAI-compatible providers.

## Quick Start

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate
python run_demo.py --mode single --trace-file trace-single.json
python run_demo.py --mode multi --trace-file trace-multi.json
python run_demo.py --mode workflow
```

For one-command local verification:

```bash
scripts/verify.sh
```

The terminal output is intentionally quiet. The detailed evidence is in the trace JSON or session report.

## Core Commands

```bash
# Single runtime path: AgentLoop + context + tools + recovery.
python run_demo.py --mode single --trace-file trace-single.json

# Runtime-backed multi-agent path.
python run_demo.py --mode multi --trace-file trace-multi.json

# Deterministic workflow baseline, useful for comparison.
python run_demo.py --mode workflow

# Persisted run sessions.
python run_demo.py --list-sessions
python run_demo.py --show-run <session_id>
python run_demo.py --resume-run <session_id> --mode single
python run_demo.py --rollback-run <session_id>
```

## Model Switching

Mock mode works offline:

```bash
python run_demo.py --mode single --llm mock
```

Ollama or any OpenAI-compatible API:

```bash
python run_demo.py --mode single --llm openai \
  --base-url http://localhost:11434/v1 \
  --api-key ollama \
  --model qwen2.5-coder:7b
```

Profile-based usage:

```bash
cp llm_profiles.example.json llm_profiles.json
python run_demo.py --mode single --llm-profile ollama-qwen
```

Never commit real API keys. `.env`, `.env.local`, `llm_profiles.json`, and `local_scripts/run_online_llm.sh` are ignored.

## Project Structure

```text
agent_forge/
  cli.py              # CLI composition and mode dispatch
  runtime/            # AgentLoop, execution control, session, messages
  context/            # context strategy, repo map, memory, retrieval, ranking
  tools/              # read/write/patch/grep/run/git/diagnostics/ask_human
  safety/             # guardrails, permission, command policy, sandbox
  models/             # provider gateway, retry/fallback, usage telemetry
  agents/             # SupervisorAgent and handoff policy
  workflows/          # TaskGraph, TaskScheduler, deterministic baseline
  observability/      # trace and metrics
  production/         # diff tracker, run report, ownership/readiness
docs/study-pack/      # focused study docs for code reading and interviews
examples/demo_repo/   # tiny repo the agent fixes
scripts/              # setup and verification scripts
```

## Study Pack

Read these in order:

```text
docs/study-pack/01-code-map-and-architecture.md
docs/study-pack/02-agent-loop-context-memory.md
docs/study-pack/03-tools-control-safety.md
docs/study-pack/04-multi-agent-design.md
docs/study-pack/05-interview-playbook.md
docs/study-pack/06-interview-question-coverage.md
```

Generated traces, reports, caches, and install artifacts are ignored and can be regenerated.

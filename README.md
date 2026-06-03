# Agent Forge

Agent Forge is a compact production-style CodingAgent runtime for learning AI Agent system design. It is not a full Codex clone and intentionally avoids TUI, IDE plugin, PR bot, enterprise integrations, container sandbox, multimodal, and model training. The goal is to make the core runtime easy to run, read, and explain in a senior AI Agent engineering walkthrough.

## What This Project Teaches

- Context engineering: repo map, file ranking, lexical retrieval, selected file previews, token budget, memory summary, and topic-shift handling.
- Agent loop control: plan, LLM call, tool call, observation, recovery, final answer.
- Tool governance: schema validation, permission policy, sandbox path checks, high-risk command blocking, and human approval hooks.
- Runtime reliability: repeated-action detection, retryability classification, max steps, timeout, cost budget, trace, reports, and rollback bundle.
- Multi-agent orchestration: supervisor, role specs, task graph, artifact handoff, ownership, validation, retry, and review.
- Model switching: mock, Ollama, company OpenAI-compatible APIs, or online OpenAI-compatible providers.

## Quick Start

```bash
cd /path/to/NanoHarness
source .venv/bin/activate
python run_demo.py --mode single --trace-file trace-single.json
python run_demo.py --mode multi --trace-file trace-multi.json
python run_demo.py --mode workflow
```

For one-command local verification:

```bash
scripts/verify.sh
```

The terminal output is intentionally quiet. The detailed evidence is in the trace JSON, usage report, or session report.

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

## Validation Scenarios

`examples/demo_repo` is the calculator smoke test. It is intentionally tiny and
answers one question: can the harness start, read a file, patch code, run tests,
and write trace evidence?

`examples/webhook_service_repo` is the main validation scenario. It models a
webhook service that verifies signatures, stores events, and enqueues jobs. The
committed fixture starts with a duplicate-delivery bug: the same `event_id`
creates duplicate records and duplicate jobs. Running the benchmark asks the
agent to read the issue and relevant files, add idempotency before side effects,
run tests, and produce trace plus usage artifacts.

```bash
local_scripts/run_webhook_deepseek.sh
```

This is the primary real-model entrypoint. It uses DeepSeek and writes
`.agent_forge/latest/webhook-deepseek/usage_report.md` plus the raw
`.agent_forge/latest/webhook-deepseek/trace.json`.

This scenario is useful for engineering walkthroughs because it exercises
repo-level context selection, issue-driven code modification, tool calling,
patch application, test execution, sandbox boundaries, eval verification,
reviewer safety checks, trace evidence, and rollback/report artifacts without
forcing you to learn a large business system.

## DeepSeek Runs

Personal Mac default, using DeepSeek V4 Flash. If you already wrote the key into
your macOS zsh environment, use one of these two scripts:

```bash
cd /Users/chenjiahui/Documents/GitHub/NanoHarness

# Main end-to-end scenario.
local_scripts/run_webhook_deepseek.sh

# Short single-agent smoke run.
local_scripts/run_deepseek.sh
```

One-time zsh setup on your personal Mac:

```bash
echo 'export DEEPSEEK_API_KEY="your-deepseek-api-key"' >> ~/.zshrc
source ~/.zshrc
```

Check that the key is available in a new terminal:

```bash
echo "$DEEPSEEK_API_KEY"
```

The equivalent raw CLI command is:

```bash
python run_demo.py --mode single --llm deepseek --trace-file .agent_forge/latest/single-deepseek/trace.json
```

Mock mode still works offline through the CLI:

```bash
python run_demo.py --mode single --llm mock --trace-file trace-mock.json
```

Any OpenAI-compatible API can still be used through the raw CLI when needed:

```bash
python run_demo.py --mode single --llm openai \
  --base-url http://localhost:11434/v1 \
  --api-key ollama \
  --model qwen2.5-coder:7b
```

Never commit real API keys. Keep `DEEPSEEK_API_KEY` in your personal shell
environment or a local ignored file only. `.env`, `.env.local`,
and `llm_profiles.json` are ignored. Company/offline verification should keep
using `--llm mock` or `scripts/verify.sh`.

## Reading Run Output

The two DeepSeek shortcuts now write into `.agent_forge/latest/` instead of the
project root. Each new run overwrites the previous files for that shortcut.

```text
.agent_forge/latest/webhook-deepseek/
  usage_report.md   # read this first
  trace.json        # raw event evidence

.agent_forge/latest/single-deepseek/
  usage_report.md   # read this first
  trace.json        # raw event evidence
```

The scripts also restore the teaching fixtures after each run so your Git tree
does not stay dirty. If you want to inspect the generated code diff, run with
`KEEP_PATCH=1`.

`trace.json` is already indented JSON. The older `*.pretty.json` files were only
formatted copies of the same trace, so the local scripts no longer generate
them.

VS Code can format JSON with `Shift + Option + F` after opening the file.
PyCharm can format JSON with `Option + Command + L` or `Code -> Reformat Code`.

Open `usage_report.md` when you want the engineering view:

- Run Summary: total LLM calls, input/output tokens, cache hit/miss, estimated cost, latency.
- Step Breakdown: every model call by step, agent, provider/model, tokens, cost, latency, and action summary.
- Context Breakdown: where prompt budget went, such as system context, history, tool schemas, memory, retrieved docs, and file previews.
- Tool Efficiency: per-tool call count, success rate, failed observations, observation size, and duration.

`run_demo.py` can still produce machine-readable `usage.json` for raw CLI runs,
but the local scripts remove it by default because it is not the file you should
study by hand.

Committed snapshots are also available under `docs/run-artifacts/` so other
devices can read the reports without rerunning DeepSeek.

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
docs/study-pack/      # focused study docs for code reading and engineering walkthroughs
examples/demo_repo/   # tiny repo the agent fixes
examples/webhook_service_repo/ # webhook idempotency benchmark fixture
scripts/              # setup and verification scripts
local_scripts/        # two DeepSeek run shortcuts
```

## Study Pack

Read these in order:

```text
docs/study-pack/01-code-map-and-architecture.md
docs/study-pack/02-agent-loop-context-memory.md
docs/study-pack/03-tools-control-safety.md
docs/study-pack/04-multi-agent-design.md
docs/study-pack/05-project-briefing.md
docs/study-pack/06-technical-question-coverage.md
docs/study-pack/07-technical-answer-bank.md
docs/study-pack/08-runtime-call-chain-map.md
docs/study-pack/09-field-readiness-roadmap.md
docs/study-pack/10-technical-defense-playbook.md
```

Generated traces, reports, caches, and install artifacts are ignored and can be regenerated.

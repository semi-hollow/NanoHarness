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
local_scripts/run_webhook_bench.sh
```

The script uses MockLLM by default so it works offline on company machines.
On your personal Mac, the primary real-model entrypoint is:

```bash
local_scripts/run_webhook_deepseek.sh
```

It uses DeepSeek and writes `trace-webhook-deepseek.*` artifacts.

This scenario is useful for engineering walkthroughs because it exercises
repo-level context selection, issue-driven code modification, tool calling,
patch application, test execution, sandbox boundaries, eval verification,
reviewer safety checks, trace evidence, and rollback/report artifacts without
forcing you to learn a large business system.

## Model Switching

Personal Mac default, using DeepSeek V4 Flash. If you already wrote the key
into your macOS zsh environment, you only need to run the script:

```bash
cd /Users/chenjiahui/Documents/GitHub/NanoHarness
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
python run_demo.py --mode single --llm deepseek --trace-file trace-deepseek.json
python -m json.tool trace-deepseek.json > trace-deepseek.pretty.json
```

Mock mode works offline:

```bash
local_scripts/run_mock.sh
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
python run_demo.py --mode single --llm-profile deepseek
```

Never commit real API keys. Keep `DEEPSEEK_API_KEY` in your personal shell
environment or a local ignored file only. `.env`, `.env.local`,
`llm_profiles.json`, and `local_scripts/run_online_llm.sh` are ignored.
Company/offline verification should keep using `--llm mock` or
`local_scripts/run_mock.sh`.

## Reading Trace JSON

The local scripts write compact JSON plus a formatted copy:

```text
trace-deepseek.json
trace-deepseek.pretty.json
trace-deepseek.usage.json
trace-deepseek.usage_report.md
trace-mock.json
trace-mock.pretty.json
trace-mock.usage.json
trace-mock.usage_report.md
```

Open the `*.pretty.json` file when you want to read by eye. To format any JSON
file manually:

```bash
python -m json.tool trace-deepseek.json > trace-deepseek.pretty.json
```

VS Code can format JSON with `Shift + Option + F` after opening the file.
PyCharm can format JSON with `Option + Command + L` or `Code -> Reformat Code`.

Open the `*.usage_report.md` file when you want the engineering view:

- Run Summary: total LLM calls, input/output tokens, cache hit/miss, estimated cost, latency.
- Step Breakdown: every model call by step, agent, provider/model, tokens, cost, latency, and action summary.
- Context Breakdown: where prompt budget went, such as system context, history, tool schemas, memory, retrieved docs, and file previews.
- Tool Efficiency: per-tool call count, success rate, failed observations, observation size, and duration.

The machine-readable companion `*.usage.json` has the same data for scripts or
future dashboards.

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
```

Generated traces, reports, caches, and install artifacts are ignored and can be regenerated.

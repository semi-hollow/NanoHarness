# macOS Local Run Guide

This guide runs Agent Forge from `NanoHarness/agent-forge` on macOS.

## First-Time Setup

From `agent-forge`, the `NanoHarness` repo root, or a parent directory:

```bash
scripts/setup_macos_local.sh
```

If you are in the repo root, use:

```bash
agent-forge/scripts/setup_macos_local.sh
```

The setup script:

- Finds `agent-forge`.
- Checks `python3`, `python3.11`, `pip3`, `brew`, and `jq`.
- Creates or reuses `.venv`.
- Installs the project with `python -m pip install -e .`.
- Adds a local `.venv/bin/python3.11` compatibility symlink only when the system has no `python3.11`.
- Runs workflow demo, unit tests, a single demo trace, trace pretty-printing, and `scripts/verify.sh`.
- Writes the full log to `~/agent_forge_macos_setup.log`.

## Daily Start

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate
python run_demo.py --mode single --trace-file trace-study.json
python -m unittest discover tests
```

## Run MockLLM

```bash
local_scripts/run_mock.sh
```

This writes:

- `trace-mock.json`
- `trace-mock.pretty.json`

## Run Ollama

Start Ollama first and make sure the model exists:

```bash
ollama pull qwen2.5-coder:7b
ollama serve
```

Then run:

```bash
local_scripts/run_ollama.sh
```

Defaults:

```bash
AGENT_FORGE_BASE_URL=http://localhost:11434/v1
AGENT_FORGE_API_KEY=ollama
AGENT_FORGE_MODEL=qwen2.5-coder:7b
```

You can override them before running the script.

## Online OpenAI-Compatible API

Copy the example locally:

```bash
cp local_scripts/run_online_llm.sh.example local_scripts/run_online_llm.sh
chmod +x local_scripts/run_online_llm.sh
```

Edit only the local non-example script:

```bash
AGENT_FORGE_BASE_URL="https://your-api-host/v1"
AGENT_FORGE_API_KEY="your-api-key"
AGENT_FORGE_MODEL="your-model"
```

Then run:

```bash
local_scripts/run_online_llm.sh
```

Do not commit real API keys.

## Read Trace Files

Pretty-print any trace with:

```bash
python -m json.tool trace-study.json > trace-study.pretty.json
```

Useful fields to inspect:

- `events`: loop steps, tool calls, observations, guardrails.
- `metrics`: tool call count, failures, approvals, duration.
- `final_answer`: the final agent answer.

## VS Code Debugging

Open `NanoHarness/agent-forge` as the VS Code workspace.

The included `.vscode` config:

- Uses `${workspaceFolder}/.venv/bin/python`.
- Enables unittest.
- Disables pytest.
- Hides `__pycache__` and `*.pyc`.

Launch configurations:

- `Agent Forge - single demo`
- `Agent Forge - workflow demo`
- `Agent Forge - multi demo`

Tasks:

- `Agent Forge: Run single demo`
- `Agent Forge: Run tests`
- `Agent Forge: Run verify`
- `Agent Forge: Pretty trace`

## Troubleshooting

### `pip install -e .` reports multiple top-level packages

Setuptools can reject this flat layout because `tutorials`, `eval_cases`, and `agent_forge` are all top-level directories. The fix is in `pyproject.toml`:

```toml
[tool.setuptools.packages.find]
include = ["agent_forge*"]
exclude = ["tests*", "tutorials*", "eval_cases*", "examples*", "docs*", "scripts*"]
```

`scripts/setup_macos_local.sh` checks this and does not append it twice.

### `verify.sh` cannot find `python3.11`

The setup script prefers a system `python3.11`. If none exists, it creates:

```bash
.venv/bin/python3.11 -> .venv/bin/python
```

You can also run:

```bash
source .venv/bin/activate
ln -sf "$(command -v python)" .venv/bin/python3.11
```

### Ollama localhost does not connect

Check that Ollama is running and that the model is available:

```bash
ollama list
ollama serve
```

Then retry:

```bash
local_scripts/run_ollama.sh
```

If another process or environment changes the endpoint, override:

```bash
AGENT_FORGE_BASE_URL=http://127.0.0.1:11434/v1 local_scripts/run_ollama.sh
```

### Do not commit API keys

Use `local_scripts/run_online_llm.sh.example` as a template. Keep real secrets only in:

- `.env`
- `.env.local`
- `local_scripts/run_online_llm.sh`

These local secret files are ignored by git.

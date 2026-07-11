# Contributing

Agent Forge is a runtime-core project. Contributions should keep the core
readable and should not turn the repository into an IDE product, cloud service,
or model-training stack.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e .
```

## Verification

Before opening a pull request, run:

```bash
scripts/verify.sh
scripts/verify_mcp.sh
```

`scripts/verify.sh` checks imports, CLI wiring, Skill registration, and runs a
read-only real-model task when `DEEPSEEK_API_KEY` is available.
`scripts/verify_mcp.sh` keeps MCP web search in offline mode by default.

## Change Guidelines

- Keep runtime changes scoped to one layer: context, model gateway, tools,
  safety, runtime control, observability, eval, or docs.
- Add comments where the design is not obvious from code.
- Do not commit real API keys, local `.env` files, generated `.agent_forge/`
  artifacts, or personal IDE state.
- Prefer real repo tasks, public benchmark cases, and trace evidence over
  self-authored fixtures.
- If a change affects agent behavior, update the relevant architecture or
  evaluation document so readers can understand the design and evidence.

## Pull Request Checklist

- [ ] `scripts/verify.sh` passes.
- [ ] `scripts/verify_mcp.sh` passes when MCP behavior changes.
- [ ] README or docs are updated for user-visible behavior.
- [ ] No real secrets or personal paths are committed.
- [ ] New tool or safety behavior is represented in trace/eval evidence.

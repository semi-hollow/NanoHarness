# Release Checklist

Use this checklist before tagging a public release or sharing the repository.

## 1. Repository Hygiene

- [ ] `git status --short` is clean.
- [ ] No real API keys or private `.env` files are tracked.
- [ ] `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, and this checklist exist.
- [ ] README badges and quick-start commands are current.
- [ ] Study-pack and readiness docs are linked from README.

## 2. Verification

```bash
scripts/verify.sh
scripts/verify_mcp.sh
```

Expected result:

- Python files compile.
- single, multi, and workflow demos run with MockLLM.
- unit tests pass.
- eval benchmark generates `.agent_forge/eval_report.md`.
- MCP server discovery and offline tool calls pass.

## 3. Real-Model Evidence

Optional local run:

```bash
local_scripts/run_webhook_deepseek.sh
```

After the run, refresh committed snapshots only when you intentionally want to
update public evidence:

```text
docs/run-artifacts/webhook-deepseek/usage_report.md
docs/run-artifacts/webhook-deepseek/trace.json
docs/run-artifacts/single-deepseek/usage_report.md
docs/run-artifacts/single-deepseek/trace.json
```

Do not commit `.agent_forge/` runtime outputs.

## 4. Open-Source Readiness

- [ ] `docs/open-source-readiness/benchmark-summary.md` reflects current eval cases.
- [ ] `docs/open-source-readiness/ablation-notes.md` reflects known design tradeoffs.
- [ ] `docs/open-source-readiness/docker-sandbox-extension-plan.md` is still accurate.
- [ ] `docs/open-source-readiness/provider-comparison.md` reflects committed run artifacts.

## 5. GitHub Release

- [ ] CI is passing on the release commit.
- [ ] Tag name follows `vMAJOR.MINOR.PATCH`.
- [ ] Release notes list runtime changes, docs changes, verification commands,
  and known limitations.


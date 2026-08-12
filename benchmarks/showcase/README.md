# NanoHarness Quality Showcase

This directory has one active public entry: `canonical-showcase-v1.json`.

The current project-facing measurement is a fixed 10-Case SWE-bench Verified
development observation: single Agent, Pass@1, `4/10` official resolved, or about
40%. It demonstrates an end-to-end repository repair capability; it does not claim
40% on the complete 500-Case benchmark and does not isolate Harness uplift from the
underlying model.

The next confirmation run uses HAL's published 50-Case SWE-bench Verified Mini list.
Until that run finishes, no document may rewrite the current `4/10` development
observation as a Mini-50 result.

## Reusable Mini-50 runner

The exact denominator is frozen in
[`swebench-verified-mini-50-v1.json`](swebench-verified-mini-50-v1.json). The default
quality profile is a single NanoHarness AgentLoop with task-aware tools, the pinned
SWE-bench repair Skill, `opencode-go/deepseek-v4-pro`, maximum reasoning effort,
128 steps, no token/cost quality cap and the official evaluator.

Validate the complete plan without sending a provider request:

```bash
.venv/bin/python scripts/run_swebench_verified_mini_50.py
```

Run it from an environment that exposes `OPENCODE_GO_API_KEY`:

```bash
NANOHARNESS_ROOT=/absolute/path/to/NanoHarness \
  zsh -lic 'cd "$NANOHARNESS_ROOT" && .venv/bin/python scripts/run_swebench_verified_mini_50.py --execute'
```

The shared IDE configuration is named
`NanoHarness Benchmark - SWE-bench Verified Mini 50`. Each Case has an independent
checkpoint and evidence directory under
`.agent_forge/evaluations/swebench-verified-mini-50/`; running the same source and
configuration again resumes the same campaign and skips completed Cases. A changed
model or quality configuration receives a different default campaign identity.

The final `campaign_summary.json` and `campaign.md` report official resolved / 50,
Wilson 95% confidence interval, empty patches, infrastructure failures, tokens,
cost, tool failures and the per-Case evidence index. This is a fixed Mini-50 Pass@1
snapshot, not the complete 500-Case SWE-bench Verified leaderboard score.

The other JSON files in this directory are historical preregistration and sampling
assets. They remain auditable but are not part of the active public narrative.

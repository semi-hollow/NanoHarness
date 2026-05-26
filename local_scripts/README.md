# Local Scripts

This folder is for developer-facing run shortcuts. Keep it small.

## Primary Runs

```bash
local_scripts/run_webhook_deepseek.sh
```

Runs the main WebhookPatchBench scenario with DeepSeek. This is the default
script to use when studying a realistic CodingAgent execution.

Outputs are overwritten on each run:

```text
.agent_forge/latest/webhook-deepseek/usage_report.md
.agent_forge/latest/webhook-deepseek/trace.json
```

The benchmark restores the fixture after the run so the Git tree stays clean.
To keep the generated patch for inspection:

```bash
KEEP_PATCH=1 local_scripts/run_webhook_deepseek.sh
```

```bash
local_scripts/run_deepseek.sh
```

Runs the tiny single-agent demo with DeepSeek. Use it only when you want the
fastest real-model smoke check.

Outputs are overwritten on each run:

```text
.agent_forge/latest/single-deepseek/usage_report.md
.agent_forge/latest/single-deepseek/trace.json
```

This shortcut also restores the tiny calculator fixture by default. Use
`KEEP_PATCH=1` only when you want to inspect the generated diff.

## Offline Mock Runs

There is no separate mock shortcut here anymore. Use the CLI directly when you
need an offline run:

```bash
python run_demo.py --mode single --llm mock --trace-file trace-mock.json
```

For full deterministic verification, use:

```bash
scripts/verify.sh
```

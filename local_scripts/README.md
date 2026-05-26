# Local Scripts

This folder is for developer-facing run shortcuts. Keep it small.

## Primary Runs

```bash
local_scripts/run_webhook_deepseek.sh
```

Runs the main WebhookPatchBench scenario with DeepSeek. This is the default
script to use when studying a realistic CodingAgent execution.

```bash
local_scripts/run_deepseek.sh
```

Runs the tiny single-agent demo with DeepSeek. Use it only when you want the
fastest real-model smoke check.

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

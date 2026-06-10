# Study Pack

This folder is the compact reading path for Agent Forge. It keeps the material
focused on runtime architecture and removes the older duplicate roadmap,
call-chain, and long-form answer-bank files.

Read in this order:

1. `01-code-map-and-architecture.md`
2. `02-agent-loop-context-memory.md`
3. `03-tools-control-safety.md`
4. `04-multi-agent-design.md`
5. `05-project-briefing.md`
6. `06-technical-question-coverage.md`
7. `07-schema-delta-guide.md`

Run while reading:

```bash
cd /path/to/NanoHarness
source .venv/bin/activate
local_scripts/run_webhook_deepseek.sh
python run_demo.py --mode review
python run_demo.py --list-task-states
```

Read generated evidence in this order:

1. `.agent_forge/latest/webhook-deepseek/usage_report.md`
2. `.agent_forge/latest/webhook-deepseek/trace.json`
3. `.agent_forge/eval_report.md`

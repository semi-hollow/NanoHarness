# Run Artifacts

These files are committed study snapshots from local DeepSeek runs. They are
kept outside `.agent_forge/` so Git tracks them and other devices can read them
without rerunning the model.

Read the markdown report first. Open the JSON trace only when you want exact
step-by-step event evidence.

```text
webhook-deepseek/
  usage_report.md   # main WebhookPatchBench report
  trace.json        # raw WebhookPatchBench event stream

single-deepseek/
  usage_report.md   # short single-agent demo report
  trace.json        # raw single-agent event stream
```

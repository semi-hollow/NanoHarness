# 02 Agent Loop, Context, And Memory

## Runtime Path

Start from `agent_forge/cli.py`.

```text
main()
  -> build_registry()
  -> resolve_llm_config()
  -> build_llm()
  -> AgentLoop.run()
```

`AgentLoop.run()` is the canonical path:

1. input guardrail
2. initialize messages, memory, and `StepController`
3. build repo map
4. build context report
5. create a trace-only plan summary
6. call `ModelGateway.chat()`
7. validate tool call
8. permission check
9. execute tool
10. feed `Observation` back as a tool message
11. classify failure and recover or stop
12. final answer with output guardrail

## Why Context Engineering Is Central

A coding agent fails less because the model is smarter and because the runtime gives it better context. This project implements a small but complete context policy in `agent_forge/context/context_strategy.py`.

It decides:

- which files rank highest for the task
- which selected files get bounded previews
- which lexical docs are retrieved
- whether previous session state should be inherited
- what memory is compressed
- what context was dropped
- how much budget each context section consumed

## Attention Sink

The `attention_sink` section is a stable prompt anchor. It reminds the model to follow the latest user task, inspect before editing, feed observations back, and avoid unverified claims. In long multi-turn conversations, this kind of anchor reduces instruction drift.

## Memory Types

`agent_forge/context/memory.py` models three memory layers:

- Short-term memory: recent notes and observations.
- Summary memory: compressed older observations.
- Session memory: previous task/report seeded by `--resume-run`.

The runtime does not blindly inherit memory. `infer_topic_relation()` classifies whether the current request is the same topic, related, unknown, or a topic shift. On topic shift, previous memory is ignored to avoid context pollution.

## Resume Flow

```bash
python run_demo.py --list-sessions
python run_demo.py --resume-run <session_id> --mode single
```

Resume does not replay old actions. It seeds prior task/report into memory, lets `ContextStrategy` decide whether to inherit it, and starts a new auditable run.

## Interview Answer

For memory and context questions, say:

> I split memory into short-term observations, compressed summaries, and resumable session facts. Context assembly is a policy layer, not just concatenation. It ranks files, previews code, retrieves lexical matches, preserves an attention anchor, compresses memory, and detects topic shifts before deciding whether old context should enter the prompt.

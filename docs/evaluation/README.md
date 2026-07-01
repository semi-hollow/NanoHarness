# Evaluation Guide

Agent Forge uses SWE-bench-style evaluation as the primary effect loop.

The goal is not to claim leaderboard performance. The goal is to make every
coding-agent run reproducible and inspectable:

1. load a public benchmark case;
2. checkout the target repository at the exact base commit;
3. let the agent inspect, patch, and validate through tools;
4. emit a SWE-bench-compatible `predictions.jsonl`;
5. optionally call the official SWE-bench harness;
6. generate a result card with trace, usage, failure taxonomy, and failure diagnosis.

## Why SWE-bench

SWE-bench evaluates whether a model or agent can resolve real GitHub software
issues by generating patches against real repositories. That directly matches
this project better than author-created fixtures.

The official evaluation harness is Docker-based and can be resource intensive.
For local development, run small samples first:

```bash
forge bench swebench --showcase --provider deepseek --direct-baseline
forge report latest
```

Use the fixed regression set after the single showcase loop is stable:

```bash
forge bench swebench --regression-set core --provider deepseek --direct-baseline
```

Scale to broader samples only after the fixed regression loop is stable:

```bash
forge bench swebench --limit 20 --provider deepseek --direct-baseline
```

Official harness evaluation:

```bash
forge bench swebench --limit 5 --provider deepseek --evaluate --max-workers 1
```

## Metrics

The result card reports:

- `patch_generated`: whether Agent Forge produced a non-empty git diff.
- `official_eval_*`: whether the SWE-bench harness ran and accepted/rejected patches.
- token usage: prompt, completion, cache hit/miss, estimated cost.
- latency: model call latency and tool duration.
- context breakdown: how much prompt budget went to files, memory, tools, history.
- tool efficiency: tool count, success rate, failed observations, observation size.
- failure taxonomy: blocked, no patch, official eval failed, provider/config failure.
- failure diagnosis: machine-readable failure class, evidence, and next actions for every case.

## Baseline

`--direct-baseline` creates a no-tools prediction file from a single LLM call.
It is intentionally simple. It answers a key architecture question:

> Why use an agent loop instead of one prompt?

Agent Forge should be compared against that baseline on the same case subset.
The agent loop is expected to spend more time and tokens, but it can inspect
files, run tools, recover from failed actions, and ground final answers in trace
evidence.

## Interpreting Results

Do not treat `patch_generated` as solved. A generated patch is only a candidate.
The credible resolved signal comes from the official SWE-bench evaluation.

Useful local progression:

1. `patch_generated` on one case.
2. trace shows relevant files were selected.
3. usage report shows no runaway cost or repeated actions.
4. direct baseline comparison exists.
5. official harness evaluates the prediction.
6. failed cases are grouped by failure taxonomy.
7. repeated runs on the fixed regression set show whether a harness change improves or regresses the same cases.

## Resource Notes

SWE-bench evaluation uses Docker. On Apple Silicon, official images may need to
be built locally; Agent Forge passes the empty namespace flag automatically for
Darwin arm64 evaluation runs.

If local hardware is too slow, generate predictions locally and evaluate on a
cloud x86 machine:

```bash
forge bench swebench --limit 20 --provider deepseek
scp .agent_forge/runs/<run-id>/predictions.jsonl <cloud-host>:/tmp/
```

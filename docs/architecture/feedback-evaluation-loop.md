# Feedback-Driven Evaluation Loop

## Goal

Turn a completed run into a durable improvement input without exporting raw
trace payloads by default.

## Data Flow

```text
task -> trace / policy / environment / patch / diagnosis
     -> human outcome + labels
     -> evidence_dataset.jsonl
     -> bad-case grouping / regression selection / model-runtime analysis
```

## Commands

Attach a human judgment to a run or one benchmark case:

```bash
forge eval feedback .agent_forge/runs/<run-id> \
  --outcome needs_work \
  --label context_miss \
  --note "Expected source file was not selected."
```

Export reviewed records:

```bash
forge eval export-dataset .agent_forge/runs/<run-id> \
  --require-feedback \
  --output .agent_forge/evaluation/evidence_dataset.jsonl
```

Patch text is excluded by default. Use `--include-patch` only after reviewing
repository ownership, licensing, secrets, and data policy.

## Record Contract

Each JSONL record contains task and stop state, selected file paths, tool names,
allowed/hidden tool summaries, a safe execution-environment projection, patch
size and SHA-256, result/evaluation/failure status, human feedback, and artifact
provenance.

It intentionally excludes full tool arguments, tool observations, absolute
workspace paths, and patch text by default. This keeps the export useful for
failure analysis while reducing accidental leakage.

## Non-Claims

The exporter does not make NanoHarness an RL platform. Reward design, sampling,
deduplication, privacy review, dataset versioning, train/eval contamination
controls, and model-training integration remain separate systems concerns.

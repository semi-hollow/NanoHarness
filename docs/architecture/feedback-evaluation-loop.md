# Feedback 驱动的 Evaluation Loop

## 目标

把一次完成的 run 转成可持久化的改进输入，同时默认不导出 raw trace payload。

## 数据流

```text
task -> trace / policy / environment / patch / diagnosis
     -> human outcome + labels
     -> evidence_dataset.jsonl
     -> bad-case grouping / regression selection / model-runtime analysis
```

## 命令

给一个 run 或 benchmark case 添加人工判断：

```bash
forge eval feedback .agent_forge/runs/<run-id> \
  --outcome needs_work \
  --label context_miss \
  --note "Expected source file was not selected."
```

导出已经 review 的记录：

```bash
forge eval export-dataset .agent_forge/runs/<run-id> \
  --require-feedback \
  --output .agent_forge/evaluation/evidence_dataset.jsonl
```

默认不导出 patch 正文。只有完成 repository ownership、license、secret 和 data policy
检查后，才应使用 `--include-patch`。

## Record 契约

每条 JSONL record 包含 task/stop state、selected file path、tool name、allowed/hidden
tool summary、安全的 execution-environment projection、patch size 和 SHA-256、
result/evaluation/failure status、human feedback 与 artifact provenance。

默认刻意排除完整 tool argument、tool observation、绝对 workspace path 和 patch text，
使导出结果仍可用于 failure analysis，同时降低意外数据泄漏风险。

## 不声称什么

Exporter 不会让 NanoHarness 变成 RL platform。Reward design、sampling、deduplication、
privacy review、dataset versioning、train/eval contamination control 和 model-training
integration 都属于独立系统问题。

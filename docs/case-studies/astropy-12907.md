# 案例研究：astropy__astropy-12907

## 为什么这个 Case 重要

这是一个紧凑的真实仓库案例，适合观察 Coding Agent tool contract、candidate patch
evidence 和保守 evaluation claim。

## Runtime 教训

Agent 需要检查 separability logic 附近的一小段代码。如果 `read_file` 不支持模型自然
使用的 `offset` / `limit` 参数，模型可能反复读取错误位置。这是 tool schema mismatch，
不只是 prompt 问题。

## 需要收集的证据

- `trace.json`：file inspection step 和 tool argument。
- `patch.diff`：candidate change。
- `usage.json`：tool call、failed tool 和 cost。
- `report.md`：failure class 和 next action。

## 边界

Candidate patch 不等于 official SWE-bench resolution。只有 official harness evaluation
接受 patch 后，才能声称 `official_resolved`。

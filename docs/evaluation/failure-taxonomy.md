# 失败分类（Failure Taxonomy）

NanoHarness 对 Coding Agent failure 做结构化分类，使一次坏 run 成为可以修复和回归的
工程目标，而不是一份 raw log。

## 证据层级

- `patch_generated`：存在非空 diff，只代表 candidate patch。
- `local_verified`：prepared workspace 中记录的 test-oriented validation event 全部
  通过；只有 compilation 不算。
- `official_resolved`：official SWE-bench per-case report 记录 `resolved: true`。
- `official_eval_failed`：official per-case report 明确记录 patch unresolved。
- `official_eval_incomplete`：evaluator 没有给该 case 产生显式 outcome；process exit
  code 不能作为 correctness signal。
- `official_eval_skipped_empty_patch`：official report 识别到 empty candidate patch。
- `official_eval_error`：official harness、Docker 或 environment 失败，patch 正确性未知。
- `not_evaluated`：除 trace 和 patch evidence 外，不做 correctness claim。

## Failure Class（失败类别）

| Class | 含义 | 典型下一步 |
| --- | --- | --- |
| `context_miss` | Agent 没有找到具体 source file。 | 调整 file ranking、symbol search 或 external context retrieval。 |
| `tool_not_available` | 请求的 tool 失败或不可用。 | 区分 retryable、hidden-by-policy、schema-invalid。 |
| `tool_schema_mismatch` | 模型使用了自然参数形态，但 tool contract 不支持。 | 根据真实 model behavior 调整 schema/coercion。 |
| `unsafe_or_blocked_command` | Command/permission policy 阻断了不安全 action。 | 使用 diagnostics 或 approval 替代自由 shell。 |
| `repeated_action_loop` | Agent 没有新信息却重复 action。 | 增加 recovery，强制进入不同 observation path。 |
| `pending_tool_call_at_stop` | Run 结束时模型仍准备调用工具。 | 增加 budget，或更早要求 patch/no-patch decision。 |
| `provider_transport_error` | Provider transport 失败。 | 与 Agent logic failure 分开处理。 |
| `context_window_exceeded` | 完整请求在结构化压缩后仍超过 provider window。 | 检查静态区段、tool schema、压缩边界和 recovery event。 |
| `validation_environment_unavailable` | 环境或依赖导致 test 无法运行。 | 先修环境，再调整 Agent。 |
| `patch_generated_but_unverified` | 存在 candidate patch，但正确性未知。 | 执行 local 或 official evaluation。 |
| `locally_verified_candidate` | 显式 local test 通过，但没有 official resolution。 | 需要 benchmark claim 时运行 official evaluation。 |
| `official_resolved` | Parsed per-case official evidence 接受 patch。 | 保留 artifact，并将 case 纳入 paired scorecard。 |
| `official_eval_error` | Official harness process/environment 在判断 patch 前失败。 | 修复 Docker/SWE-bench/environment，再评测。 |
| `official_eval_failed` | Official harness 完成并拒绝该 case 的 patch。 | 分析 patch，并把 case 加入 regression。 |

## 工程意义

目标不是事后给 failure 贴标签，而是判断下一步改进属于 context selection、tool
governance、sandbox policy、diagnostics、provider handling，还是 prompt procedure。

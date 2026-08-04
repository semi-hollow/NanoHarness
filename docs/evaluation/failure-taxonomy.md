# 失败分类（Failure Taxonomy）

NanoHarness 对 Coding Agent failure 做结构化分类，使一次坏 run 成为可以修复和回归的
工程目标，而不是一份 raw log。

## 它是什么，不是什么

- 它是 **benchmark 结束后的确定性规则链**，不是模型可调用的 Tool，也不调用 LLM。
- `Failure Class` 是项目根据真实失败现象预先定义的稳定类别；新增类别需要同时补规则和回归测试。
- 分类器读取结构化 `BenchCaseResult`、`usage.json` 和 `trace.json`，按固定优先级返回第一条命中的
  `FailureDiagnosis`。规则顺序本身就是设计：Official 结果和环境故障优先于 patch、本地验证和
  Runtime 症状，避免把 Docker 故障误判成模型能力问题。

可类比支付系统的差错归因服务：交易流水已经产生后，再根据状态、渠道回执和异常码归入唯一差错类型；
不是让另一个模型阅读日志后自由发挥。

## 生成与消费链路

```text
AgentLoop / Official evaluator
  -> BenchCaseResult + usage.json + trace.json
  -> BenchFailureAnalyzer 读取最终证据
  -> classify_case_result 按有序规则命中一个 FailureDiagnosis
  -> 回写 failure_class / diagnosis / evidence / next_actions
  -> results.json / report / case study / scorecard / campaign / Workbench
```

主要代码入口：

- `bench/domain/failure_taxonomy.py::classify_case_result`：无 IO 的纯分类规则。
- `bench/application/failure_analysis.py::BenchFailureAnalyzer`：读取证据并回写诊断字段。
- `bench/adapters/artifact_files.py::finalize_case`：在最终评测完成后触发归因，避免写出 stale artifact。

Workbench 会展示 `failure_class`、诊断证据和下一步，因此它不只是给维护者看的文字；scorecard、
campaign 聚合和人工反馈数据导出也会消费这些字段。但它当前**不会自动修改 Prompt、ToolRouter 或
AgentLoop**。改进闭环仍是“规则给候选诊断 -> 人工复核 -> 选择回归和 matched experiment”。

不要与运行中的即时故障判断混淆：`StepController` 判断本轮 Tool Observation 是否应重试或熔断；
Failure Taxonomy 在整次 benchmark 及 official evaluation 结束后给整个 Case 归因。

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

## Failure Class（互斥结果分类）

当前规则链共有 18 个稳定结果值，其中包含成功证据 `official_resolved` 和保守兜底
`unclassified`，所以不能把这个数字口述成“18 种失败”。

| Class | 含义 | 典型下一步 |
| --- | --- | --- |
| `context_miss` | Agent 没有找到具体 source file。 | 调整 file ranking、symbol search 或 external context retrieval。 |
| `tool_not_available` | 请求的 tool 失败或不可用。 | 区分 retryable、hidden-by-policy、schema-invalid。 |
| `tool_schema_mismatch` | 模型使用了自然参数形态，但 tool contract 不支持。 | 根据真实 model behavior 调整 schema/coercion。 |
| `unsafe_or_blocked_command` | Command/permission policy 阻断了不安全 action。 | 使用 `python_validation` 或 approval 替代自由 shell。 |
| `repeated_action_loop` | Agent 没有新信息却重复 action。 | 增加 recovery，强制进入不同 observation path。 |
| `pending_tool_call_at_stop` | Run 结束时模型仍准备调用工具。 | 增加 budget，或更早要求 patch/no-patch decision。 |
| `provider_transport_error` | Provider transport 失败。 | 与 Agent logic failure 分开处理。 |
| `context_window_exceeded` | 完整请求在结构化压缩后仍超过 provider window。 | 检查静态区段、tool schema、压缩边界和 recovery event。 |
| `runner_or_environment_error` | Runner、checkout、provider 或本地环境在形成可靠证据前失败。 | 先修运行环境，再原样重跑。 |
| `validation_environment_unavailable` | 环境或依赖导致 test 无法运行。 | 先修环境，再调整 Agent。 |
| `input_policy_block` | Task 文本在第一次模型调用前被输入策略阻断。 | 检查是否把引用内容误当成可执行动作。 |
| `patch_generated_but_unverified` | 存在 candidate patch，但正确性未知。 | 执行 local 或 official evaluation。 |
| `locally_verified_candidate` | 显式 local test 通过，但没有 official resolution。 | 需要 benchmark claim 时运行 official evaluation。 |
| `official_resolved` | Parsed per-case official evidence 接受 patch。 | 保留 artifact，并将 case 纳入 paired scorecard。 |
| `official_eval_error` | Official harness process/environment 在判断 patch 前失败。 | 修复 Docker/SWE-bench/environment，再评测。 |
| `official_eval_failed` | Official harness 完成并拒绝该 case 的 patch。 | 分析 patch，并把 case 加入 regression。 |
| `no_patch_generated` | Run 未被明确阻断，但结束时没有候选改动。 | 检查最后两轮并要求 patch 或有证据的 blocker。 |
| `unclassified` | 当前没有更具体的规则命中。 | 人工复核；模式重复后再提升为稳定规则。 |

## 工程意义

目标不是事后给 failure 贴标签，而是判断下一步改进属于 context selection、tool
governance、sandbox policy、validation、provider handling，还是 prompt procedure。

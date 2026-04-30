# 10-observability-and-tracing

V2 的 trace 不只记录事件，还会生成 metrics summary。

## Trace Event

每个事件包含：

- run_id
- step
- agent_name
- event_type
- duration_ms
- success/error
- tool_call、tool_arguments、observation、guardrail 等扩展字段

## Metrics

`agent_forge.observability.metrics.summarize()` 至少输出：

- `tool_call_count`
- `failed_tool_call_count`
- `handoff_count`
- `guardrail_block_count`
- `approval_count`
- `duration_ms`
- `steps_count`

这些指标会写入 trace JSON，也会被 eval_report 引用。面试时可以说：trace 是证据链，metrics 是可比较的运行摘要。

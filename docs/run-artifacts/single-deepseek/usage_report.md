# Agent Forge Usage Report

## Run Summary

- run_id: `65be1dda-393a-4fd6-990e-82caa1599df7`
- task: 修复 examples/demo_repo 里的测试失败问题
- stop_reason: `final_answer`
- llm_calls: 7
- tokens: input=19901 output=1221 total=21122
- cache: hit=3712 miss=16189 hit_rate=18.65%
- estimated_cost_usd: $0.002617
- llm_latency_ms: 21412
- tool_calls: 7 failed=3

## Step Breakdown

| call | step | agent | provider/model | input | output | cache hit | cache miss | cost | latency ms | context chars | action summary |
|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|1|1|CodingAgent|deepseek/deepseek-v4-flash|1971|196|0|1971|$0.000331|2624|3275|read_filex2|
|2|2|CodingAgent|deepseek/deepseek-v4-flash|2346|227|0|2346|$0.000392|3562|3466|apply_patchx1|
|3|3|CodingAgent|deepseek/deepseek-v4-flash|2571|76|0|2571|$0.000381|2177|3533|run_commandx1|
|4|4|CodingAgent|deepseek/deepseek-v4-flash|2995|137|768|2227|$0.000352|2826|3743|run_commandx1|
|5|5|CodingAgent|deepseek/deepseek-v4-flash|3161|96|896|2265|$0.000346|2334|3779|run_commandx1|
|6|6|CodingAgent|deepseek/deepseek-v4-flash|3286|239|1024|2262|$0.000386|3986|3815|run_commandx1|
|7|7|CodingAgent|deepseek/deepseek-v4-flash|3571|250|1024|2547|$0.000429|3903|3912|none|

## Context Breakdown

| section | chars | est tokens |
|---|---:|---:|
| system_context | 31618 | 7904 |
| conversation_history | 17442 | 4360 |
| tool_schemas | 8547 | 2137 |
| memory | 3275 | 819 |
| file_previews | 2709 | 677 |
| attention_sink | 1827 | 457 |
| retrieved_docs | 1281 | 320 |

## Tool Efficiency

| tool | calls | success | failed | success rate | observation chars | duration ms |
|---|---:|---:|---:|---:|---:|---:|
| apply_patch | 1 | 1 | 0 | 100.00% | 50 | 0 |
| read_file | 2 | 2 | 0 | 100.00% | 325 | 0 |
| run_command | 4 | 1 | 3 | 25.00% | 1222 | 0 |

## Optimization Notes

- Cache hit rate is low; stable system/context prefixes may not be reused enough across steps.
- Context was truncated in 7 step(s); inspect dropped_context and selected files.
- 3 tool observation(s) failed; connect these to recovery_decision events.
- Largest context section is system_context (31618 chars); this is the first place to optimize token cost.

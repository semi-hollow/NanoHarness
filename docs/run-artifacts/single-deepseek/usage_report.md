# Agent Forge Usage Report

## Run Summary

- run_id: `0f1c986c-48e9-46be-8af9-3d1816d01a51`
- task: 修复 examples/demo_repo 里的测试失败问题
- stop_reason: `final_answer`
- llm_calls: 7
- tokens: input=18513 output=880 total=19393
- cache: hit=3456 miss=15057 hit_rate=18.67%
- estimated_cost_usd: $0.002363
- llm_latency_ms: 12789
- tool_calls: 7 failed=3

## Step Breakdown

| call | step | agent | provider/model | input | output | cache hit | cache miss | cost | latency ms | context chars | action summary |
|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|1|1|CodingAgent|deepseek/deepseek-v4-flash|1964|187|0|1964|$0.000327|2021|3424|read_filex2|
|2|2|CodingAgent|deepseek/deepseek-v4-flash|2330|190|0|2330|$0.000379|2093|3615|apply_patchx1|
|3|3|CodingAgent|deepseek/deepseek-v4-flash|2525|79|0|2525|$0.000376|1577|3682|run_commandx1|
|4|4|CodingAgent|deepseek/deepseek-v4-flash|2623|84|768|1855|$0.000285|1666|3718|diagnosticsx1|
|5|5|CodingAgent|deepseek/deepseek-v4-flash|2873|120|768|2105|$0.000330|1887|3928|run_commandx1|
|6|6|CodingAgent|deepseek/deepseek-v4-flash|3022|81|896|2126|$0.000323|1592|3964|run_commandx1|
|7|7|CodingAgent|deepseek/deepseek-v4-flash|3176|139|1024|2152|$0.000343|1953|4061|none|

## Context Breakdown

| section | chars | est tokens |
|---|---:|---:|
| system_context | 32244 | 8061 |
| conversation_history | 12323 | 3081 |
| tool_schemas | 7896 | 1974 |
| memory | 3315 | 829 |
| file_previews | 2709 | 677 |
| attention_sink | 1827 | 457 |
| retrieved_docs | 1281 | 320 |

## Tool Efficiency

| tool | calls | success | failed | success rate | observation chars | duration ms |
|---|---:|---:|---:|---:|---:|---:|
| apply_patch | 1 | 1 | 0 | 100.00% | 50 | 0 |
| diagnostics | 1 | 0 | 1 | 0.00% | 245 | 0 |
| read_file | 2 | 2 | 0 | 100.00% | 325 | 0 |
| run_command | 3 | 1 | 2 | 33.33% | 207 | 0 |

## Evidence

- `read_file:examples/demo_repo/src/calculator.py lines=3:ok:file inspected`
- `read_file:examples/demo_repo/tests/test_calculator.py lines=6:ok:file inspected`
- `apply_patch:apply_patch:ok:patched once: examples/demo_repo/src/calculator.py`
- `diagnostics:diagnostics:fail:exit_code=1 Traceback (most recent call last):   File "/Users/chenjiahui/Documents/GitHub/NanoHarness/examples/demo_repo/tests/test_calculator.py", line 2, in <`
- `run_command:run_command:ok:exit_code=0 test_add (tests.test_calculator.T.test_add) ... ok  ---------------------------------------------------------------------- Ran 1 test in 0.000s  OK`

## Optimization Notes

- Cache hit rate is low; stable system/context prefixes may not be reused enough across steps.
- Context was truncated in 7 step(s); inspect dropped_context and selected files.
- 3 tool observation(s) failed; connect these to recovery_decision events.
- Largest context section is system_context (32244 chars); this is the first place to optimize token cost.

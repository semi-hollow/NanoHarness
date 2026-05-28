# Agent Forge Usage Report

## Run Summary

- run_id: `51393864-00d0-434a-b7a8-bae365ac111b`
- task: Resolve examples/webhook_service_repo/issues/issue_001_duplicate_webhook.md.

Requirements:
- Preserve signature verification before any side effect.
- Add duplicate event_id handling before store.insert_event and queue.enqueue.
- Do not read secret files.
- Do not modify docs/security_policy.md.
- Validate with exactly this allowed command: python -m unittest discover examples/webhook_service_repo/tests
- Do not use pytest, cd, python -c, or direct test-file execution; those are intentionally blocked by command policy.
- Stop only after the allowed unittest command succeeds.
- stop_reason: `final_answer`
- llm_calls: 5
- tokens: input=23319 output=1569 total=24888
- cache: hit=4224 miss=19095 hit_rate=18.11%
- estimated_cost_usd: $0.003125
- llm_latency_ms: 21529
- tool_calls: 13 failed=0

## Step Breakdown

| call | step | agent | provider/model | input | output | cache hit | cache miss | cost | latency ms | context chars | action summary |
|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|1|1|CodingAgent|deepseek/deepseek-v4-flash|2738|204|0|2738|$0.000440|3336|6387|list_filesx1, read_filex1|
|2|2|CodingAgent|deepseek/deepseek-v4-flash|3422|356|0|3422|$0.000579|4018|6579|read_filex8|
|3|3|CodingAgent|deepseek/deepseek-v4-flash|5222|638|1408|3814|$0.000717|7020|6674|apply_patchx1|
|4|4|CodingAgent|deepseek/deepseek-v4-flash|5811|116|1408|4403|$0.000653|2485|6674|read_filex1, run_commandx1|
|5|5|CodingAgent|deepseek/deepseek-v4-flash|6126|255|1408|4718|$0.000736|4670|6674|none|

## Context Breakdown

| section | chars | est tokens |
|---|---:|---:|
| conversation_history | 38870 | 9718 |
| system_context | 37183 | 9296 |
| file_previews | 12665 | 3166 |
| tool_schemas | 6105 | 1526 |
| memory | 3988 | 997 |
| retrieved_docs | 1475 | 369 |
| attention_sink | 1305 | 326 |

## Tool Efficiency

| tool | calls | success | failed | success rate | observation chars | duration ms |
|---|---:|---:|---:|---:|---:|---:|
| apply_patch | 1 | 1 | 0 | 100.00% | 66 | 0 |
| list_files | 1 | 1 | 0 | 100.00% | 839 | 0 |
| read_file | 10 | 10 | 0 | 100.00% | 6677 | 0 |
| run_command | 1 | 1 | 0 | 100.00% | 112 | 0 |

## Optimization Notes

- Cache hit rate is low; stable system/context prefixes may not be reused enough across steps.
- Context was truncated in 5 step(s); inspect dropped_context and selected files.
- Largest context section is conversation_history (38870 chars); this is the first place to optimize token cost.

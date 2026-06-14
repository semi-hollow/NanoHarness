# Agent Forge Usage Report

## Run Summary

- run_id: `4772ef00-cdea-414a-a532-a9c64d40b72f`
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
- tokens: input=22228 output=1790 total=24018
- cache: hit=4480 miss=17748 hit_rate=20.15%
- estimated_cost_usd: $0.002998
- llm_latency_ms: 17230
- tool_calls: 11 failed=0

## Step Breakdown

| call | step | agent | provider/model | input | output | cache hit | cache miss | cost | latency ms | context chars | action summary |
|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
|1|1|CodingAgent|deepseek/deepseek-v4-flash|2800|104|0|2800|$0.000421|1439|6602|list_filesx1|
|2|2|CodingAgent|deepseek/deepseek-v4-flash|3137|314|0|3137|$0.000527|2790|6699|read_filex7|
|3|3|CodingAgent|deepseek/deepseek-v4-flash|4822|779|1408|3414|$0.000700|6615|6793|apply_patchx1|
|4|4|CodingAgent|deepseek/deepseek-v4-flash|5579|121|1536|4043|$0.000604|1383|6793|read_filex1, run_commandx1|
|5|5|CodingAgent|deepseek/deepseek-v4-flash|5890|472|1536|4354|$0.000746|5003|6793|none|

## Context Breakdown

| section | chars | est tokens |
|---|---:|---:|
| system_context | 38100 | 9525 |
| conversation_history | 33334 | 8334 |
| file_previews | 12745 | 3186 |
| tool_schemas | 5640 | 1410 |
| memory | 4085 | 1021 |
| retrieved_docs | 1475 | 369 |
| attention_sink | 1305 | 326 |

## Tool Efficiency

| tool | calls | success | failed | success rate | observation chars | duration ms |
|---|---:|---:|---:|---:|---:|---:|
| apply_patch | 1 | 1 | 0 | 100.00% | 66 | 0 |
| list_files | 1 | 1 | 0 | 100.00% | 839 | 0 |
| read_file | 8 | 8 | 0 | 100.00% | 5570 | 0 |
| run_command | 1 | 1 | 0 | 100.00% | 112 | 0 |

## Evidence

- `read_file:examples/webhook_service_repo/src/webhook_handler.py lines=16:ok:file inspected`
- `read_file:examples/webhook_service_repo/src/event_store.py lines=21:ok:file inspected`
- `read_file:examples/webhook_service_repo/src/signature.py lines=5:ok:file inspected`
- `read_file:examples/webhook_service_repo/src/models.py lines=16:ok:file inspected`
- `read_file:examples/webhook_service_repo/src/job_queue.py lines=17:ok:file inspected`
- `read_file:examples/webhook_service_repo/tests/test_webhook_idempotency.py lines=41:ok:file inspected`
- `read_file:examples/webhook_service_repo/tests/test_signature_verification.py lines=29:ok:file inspected`
- `apply_patch:apply_patch:ok:patched once: examples/webhook_service_repo/src/webhook_handler.py`
- `read_file:examples/webhook_service_repo/src/webhook_handler.py lines=19:ok:file inspected`
- `run_command:run_command:ok:exit_code=0 ... ---------------------------------------------------------------------- Ran 3 tests in 0.000s  OK`

## Optimization Notes

- Context was truncated in 5 step(s); inspect dropped_context and selected files.
- Largest context section is system_context (38100 chars); this is the first place to optimize token cost.

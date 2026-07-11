# Failure-Driven Runtime Improvements

## 这份文档的用途

这不是流水账，也不是展示“模型一次跑通”。它记录的是：在真实 SWE-bench case 上，NanoHarness 如何把 badcase 变成 trace 证据、root cause、runtime 修复和 regression test。

固定 case：`astropy__astropy-12907`，Astropy nested `CompoundModel` separability bug。

Reference run evidence：

```text
run_dir: .agent_forge/runs/swebench-20260707-011718-4944f2e
single_status: patch_generated
multi_status: patch_generated
multi_agent_summary.status: passed
Reviewer: PASS
Verifier: PASS
patch_chars: 506
official_eval: not_evaluated
```

最终候选 patch：

```diff
-        cright[-right.shape[0]:, -right.shape[1]:] = 1
+        cright[-right.shape[0]:, -right.shape[1]:] = right
```

边界：这是 candidate patch，不是 official resolved rate；没有官方 SWE-bench Docker evaluation 就不能声称 resolved。

## 有价值的调试问题

### 1. ToolRouter 把“不要改测试”误判成全局只读

现象：single/multi 都没有 patch，trace 里只有 read/grep，没有 `apply_patch` / `write_file`。multi 还出现 `run_command not routed`。

根因：prompt 里有 `Do not edit tests unless...`，ToolRouter 看到 `do not edit` 后误判整个任务是 read-only，导致写工具和验证工具被隐藏。

修复：`do not edit tests unless...` 不再触发全局 read-only；coding task 仍可暴露 `apply_patch`、`diagnostics`、`git_diff` 等必要工具。

工程结论：工具暴露不是静态列表，而是 task-aware routing。模型能看到哪些工具，本身就是 control plane 的一部分。

### 2. final answer 残留 raw tool-call markup

现象：Implementer artifact 中出现 `<tool_calls>` / `invoke name=`，但旧逻辑把它当成正常 final answer。

根因：provider 输出了未执行的 tool-call markup。AgentLoop 没有把它识别成 pending tool call，MultiAgentCoordinator 也可能把不完整 artifact 当成 role 输出。

修复：AgentLoop 检测 raw tool-call markup，标记为 `blocked: pending_tool_call_at_stop`；Coordinator 对 unfinished tool output 返回 `NEEDS_REVISION`。

工程结论：final answer 不是天然可信。runtime 必须识别“模型还想行动但工具没执行”的状态，避免把半成品当结果。

### 3. repeated read_file 被过早 hard block

现象：Agent 多次读同一目标文件后直接 `blocked: repeated_tool_call`，还没进入 patch 阶段。

根因：StepController 对重复 tool intent 一律 hard block。对 read/search 类工具来说，多次读目标文件或读局部窗口是正常调查行为，不应直接杀掉 run。

修复：重复 `read_file` / `grep` / `grep_search` / `list_files` 变成可恢复 observation；重复 `apply_patch` / `write_file` / `run_command` 仍 hard block。

工程结论：repeated action policy 要按风险分层。重复读是 loop signal，重复写/命令是 side-effect 风险。

### 4. provider 半包被误认为 role 失败

现象：multi 的 Implementer 直接 blocked：

```text
blocked: role Implementer failed with exception: IncompleteRead(790 bytes read)
```

根因：`OpenAICompatibleLLMClient.chat()` 没捕获 `http.client.IncompleteRead`。provider transport failure 越过 AgentLoop，被 Coordinator 当成 role exception。

修复：把 `IncompleteRead` 转成结构化 `request_failed`，让 AgentLoop 走 model failure / recovery 路径。

工程结论：provider transport failure 要和 agent logic failure 分开。网络半包不是 Implementer 能力问题。

### 5. failure diagnosis 误判根因

现象：真实 blocker 是 `pending_tool_call_at_stop` 或 `IncompleteRead`，但 `results.json` 显示 `context_retrieval_miss`。

根因：diagnostics 看到 `max_selected_files=0` 就归类为 context miss，没有优先识别 final answer / stop reason 中更具体的 blocker。

修复：在 context miss 之前优先识别 `pending_tool_call_at_stop`、`provider_transport_error`、`request_failed`。

工程结论：failure taxonomy 是诊断系统，不是标签装饰。越具体的 blocker 应该越靠前，否则会误导优化方向。

### 6. run_command 在 SWE-bench 中诱导错误 shell 行为

现象：模型用 `run_command` 执行 `python -c`、管道、重定向、临时脚本读取源码，导致 command policy 拦截或验证无效。

根因：`run_command` 是安全执行器，不是自由 shell；SWE-bench 修复任务更适合用 `read_file` / `grep_search` 读源码，用 `diagnostics` 做验证。

修复：SWE-bench task 中隐藏 `run_command`，保留 `diagnostics`、`apply_patch`、`git_diff`、`git_status`；prompt 和 `coding_fix` profile 明确禁止 `python -c`、管道、重定向、`/tmp` workaround。

工程结论：工具越多不一定越好。生产 Agent 应暴露最小必要工具，降低误调用面。

### 7. read_file 契约不支持模型常用的 line window

现象：模型多次调用：

```text
read_file(path="astropy/modeling/separable.py", offset=219, limit=40)
```

但旧工具只支持 `path`，始终返回文件开头，导致模型读不到 `_cstack()` 附近关键代码。

根因：tool schema 和模型习惯不匹配。模型自然会传 `offset/limit`，但工具契约没有接住。

修复：`ReadFileTool` 支持 `offset` / `limit`，按 1-based line number 返回带行号窗口，并覆盖字符串型参数测试。

工程结论：Agent 工具不是普通函数。schema 设计要贴近模型调用习惯，否则模型会浪费步数或写 workaround。

### 8. write_file 在 benchmark 中诱导临时脚本污染 workspace

现象：模型写 `_extract.py`、`_read_separable.py` 等 scratch helper 文件。

根因：SWE-bench 的目标是修改被测源码。`write_file` 太自由，会诱导无关文件污染；真正需要的是基于唯一 anchor 的 `apply_patch`。

修复：SWE-bench task 隐藏 `write_file`，只保留 `apply_patch` 作为源码修改入口。

工程结论：工具权限要按 workflow 收敛。benchmark / production workflow 中应暴露最小必要写入口。

### 9. multi-agent 状态语义太粗

现象：已经生成 candidate patch，但 Verifier 因环境或工具阻塞，`multi_agent_summary` 仍显示整个 run `blocked`。

根因：Coordinator 只有 `passed/blocked/needs_revision`，没有区分“没有 patch”和“已有候选 patch 但验证未完成”。

修复：Coordinator 检查 workspace `git diff`。如果已有候选 patch，但 blocker 来自 Verifier 或后续验证，summary 标记为 `patch_generated` / unverified patch，而不是完全失败。

工程结论：评测状态要表达证据层级。candidate patch generated、verified、official resolved 是不同层级。

### 10. diagnostics 把环境缺依赖当成工具失败

现象：patch 已经写出，后续 `diagnostics unittest` 因缺 `pytest` / `erfa` 失败，导致 AgentLoop 连续失败预算耗尽。

根因：工具 success flag 混淆了 code failure、tool failure 和 validation environment unavailable。

修复：缺测试依赖时返回 `validation_blocked`，并作为可解释 observation，而不是计入工具失败预算。

工程结论：Agent failure taxonomy 要区分 code failure、tool failure、environment unavailable，否则 runtime 会惩罚正确 patch 后的验证尝试。

### 11. diagnostics target 语义不清

现象：模型交替传：

```text
astropy.modeling.tests.test_separable
astropy/modeling/tests/test_separable.py
astropy/modeling/tests/test_separable
```

旧实现只按目录或文件理解 target，导致 dotted module 被误当 discovery 目录。

修复：`diagnostics(kind="unittest")` 支持 dotted module、`.py` 文件和 path-like stem。

工程结论：tool schema 不只是参数名，还包括容错语义。工具越能吸收模型常见表达，AgentLoop 越少无意义重试。

### 12. Reviewer / Verifier verdict 解析太脆

现象：artifact 里有：

```text
Review Verdict: PASS
Verdict: PASS
裁决：通过
```

但 summary 仍显示 `NEEDS_REVISION`。

根因：`_decision_for_role()` 只看第一行；真实 LLM 常输出 Markdown 标题，verdict 在后几行。

修复：扫描前 12 个非空行，支持 `PASS`、`Review Verdict: PASS`、`Verdict: PASS`、`Status: PASS`、`裁决：通过`、`结论：通过`；无明确 marker 时仍保守默认为 `NEEDS_REVISION`。

工程结论：LLM 输出协议要兼顾 strict marker 和 Markdown 现实。生产上最好用 structured output；文本 marker 必须鲁棒解析和保守 fallback。

## 调试顺序模板

每次 SWE-bench 失败优先看：

1. `results.json`：最终 status、failure_class、diagnosis、patch_chars。
2. `comparison.json`：single vs multi 的成本、调用数、失败数。
3. `usage.json`：工具调用分布、failed_tool_calls、最后 action。
4. `trace.json`：stop_reason、permission/tool routing/recovery_decision。
5. `multi_agent_summary.json`：哪个 role 失败、artifact 是否完整。
6. `patch.diff`：是否真的有 workspace diff。

## Design Conclusions

> 这次不是简单调 prompt，而是沿着真实 evidence 逐层修 runtime：ToolRouter 负责 task-aware 工具收敛，ReadFileTool 修正模型常用 line-window 契约，DiagnosticsTool 区分 code failure 和 validation environment unavailable，Coordinator 区分 candidate patch 与 official resolved，verdict parser 兼容真实 Markdown 输出。最终从 UI 点击 Run Reference Case，single/multi 都生成相同 candidate patch，Reviewer/Verifier 在 artifact 中给出 PASS。这个过程体现的是 agent harness 的工程闭环，而不是一次性 demo。

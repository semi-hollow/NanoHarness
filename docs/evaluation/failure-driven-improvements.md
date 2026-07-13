# Failure-Driven Runtime Improvements

## 这份文档的用途

这不是流水账，也不是展示“模型一次跑通”。它记录的是：在真实 SWE-bench case 上，NanoHarness 如何把 badcase 变成 trace 证据、root cause、runtime 修复和 regression test。

## 持续维护规则

这份文件是 NanoHarness 的开发故障档案。每次新增 runtime 能力、修复
correctness bug 或改变公开行为，都必须在同一开发周期补充代表性案例。
代码、测试和 README 已更新，但这里没有记录，仍视为文档未完成。

新增案例至少包含：

1. 现象和可复现 failure scenario。
2. 定位过程，以及哪些证据排除了错误假设。
3. 根因，必须落到具体状态机、数据契约或执行边界。
4. 小而硬的修复，不用大规模重构掩盖问题。
5. 回归测试或真实运行验证。
6. 工程结论、能力边界和下一层可追问问题。

公开描述保持产品和证据导向，不把候选 patch、verifier PASS、局部测试
或单次并发运行夸大为 official resolution、生产安全或普遍性能提升。

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

### 13. `ask_human` 返回模拟结果，运行并没有真正暂停

现象：模型调用 `ask_human` 后可以立即收到 synthetic observation，trace
看起来存在 HITL，但没有 pending request、人工回答入口或恢复链。直接调用
tool 还可能绕过 AgentLoop 的状态控制。

具体 failure scenario：Agent 询问 API 版本后继续执行后续工具，即使没有任何
operator 响应。

定位过程：沿 `wiring.py -> AskHumanTool.execute() -> AgentLoop` 检查调用链，
确认工具自己生成答案，`TaskStateStore` 中也没有专门的等待状态。

根因：把“向人提问”实现成普通工具 observation，而不是 control-plane event。

修复：新增 `HumanInputStore` 和 `WAITING_HUMAN`；`AgentLoop` 拦截 pre-loop
clarification 与 tool-level `ask_human`，先原子落盘、写 trace/checkpoint，再
停止。直接执行 `AskHumanTool` 现在 fail closed。`forge respond` 记录回答，
`forge resume` 将问题和回答注入 continuation task。

验证：`tests/test_human_input.py` 覆盖模型未调用、工具未继续、回答落盘、
resume context 和 direct execution fail-closed。

工程结论：HITL 不是 prompt 文案，也不是阻塞 `input()`；它是可持久化、
非阻塞、可恢复的 runtime 状态转换。

可追问：为什么 informational response 与 side-effect approval 必须使用两个
store，而不是共用一个 `approved=true`？

### 14. Human input 终态和请求路径可以被错误复用

现象：同一 thread/question 在 choices 已变化时仍复用旧答案；cancelled 请求
再次出现时会重新显示 `waiting_human`；CLI 传入带 `../` 的 request id 可能把
查找路径带出 human-input root。

定位过程：构造“先回答旧 choices，再用新 choices 提同一问题”和
`store.get("../../outside")` 两个最小用例；同时重跑 cancelled thread，观察
第二次状态。

根因：request identity 只包含 thread/kind/question；`path_for()` 没验证 id；
AgentLoop 只特殊处理 responded，没有处理 cancelled。

修复：规范化并去重 choices，将其纳入稳定 id；request id 严格限制为生成的
24 位十六进制；cancelled 保持 terminal 并转为 blocked；responded 请求继续
真实模型循环而不是返回空字符串。JSON 使用 fsync 加原子替换。

验证：`test_store_persists_response_and_cancelled_requests_are_terminal` 和
`test_existing_response_continues_but_cancelled_question_stays_terminal`。

工程结论：持久化状态机必须同时防 stale semantic 和 artifact path traversal；
“文件存在”不等于“这个文件仍代表当前问题”。

可追问：多 operator 并发响应时，下一步为什么需要数据库 CAS 或版本号？

### 15. Fanout 只有 callback scheduler，没有真实 AgentLoop worker

现象：`fanout.py` 能把任务分 batch，但 runner 只是外部 callback。它没有独立
模型上下文、tool policy、trace、usage、workspace 或可检查 patch，因此无法
证明多个 subagent 真正在执行任务。

定位过程：从 public CLI 反向搜索 `run_fanout()` 调用，发现它没有进入
`run_repository_task()`，也没有构造 `AgentLoop`。

根因：调度算法和 runtime worker 没有接线。

修复：新增 `LiveFanoutCoordinator`。每个 runnable task 创建 disposable git
worktree、fresh LLM client、filtered registry、AgentLoop、trace、usage、artifact
和 patch；worktree add/remove 使用短临界区锁，模型和工具执行仍可并发。CLI/UI
增加受限 fanout plan、resume 和 worker 数入口。

验证：`test_real_agentloop_workers_use_isolated_worktrees_and_merge_disjoint_patches`
和 `test_public_run_entrypoint_routes_fanout_and_writes_candidate_patch`。

工程结论：multi-agent 的关键不是类名或线程池，而是上下文、工具、状态、
副作用和证据是否真正隔离。

可追问：为什么不能在线程间共享一个 `ModelGateway` 或同一个可写 workspace？

### 16. Fanout scope 和 plan 输入存在误分类与路径风险

现象：`./.github/workflows/` 被归一化成 `github/workflows/`；任意
`expected_artifact="../../outside"` 可让 artifact 写出 worker 目录；字符串型
`allowed_tools` 会被逐字符拆分；task id `".."` 虽无斜杠，仍会让
`workers / task.id` 产物目录退回 `fanout/`。

定位过程：对 plan parser 添加 `.github/`、artifact traversal 和非 list 字段
测试，先确认错误发生在 worker 启动前的数据规范化阶段。

根因：`lstrip("./")` 删除的是字符集合，不是单个 `./` 前缀；artifact 名和
list 字段缺少结构化验证；task id 只做了字符正则，没有拒绝
filesystem 特殊目录名。

修复：只逐段移除真实 `./` 前缀，保留 dot-directory；artifact 只允许安全
basename；`depends_on`、`write_scope`、`allowed_tools` 必须是非空字符串列表；
明确拒绝 task id `"."` / `".."`；声明重叠由 conflict-free batching
串行化，实际越界则不合并。

验证：`test_plan_preserves_dot_directory_scopes`、
`test_plan_rejects_path_escape_and_unknown_dependencies`、
`test_scope_escape_fails_closed_without_merging_patch`。

工程结论：scope 是写所有权契约，必须在执行前规范化，在执行后用实际 touched
files 再验证，不能只信 plan。

可追问：为什么声明 overlap 适合串行化，而未声明的 actual overlap 要 fail
closed？

### 17. 新建文件没有进入 patch、scope 或下游 worker 基线

现象：worker 用 `write_file` 新建 `new.py` 后，`git diff` 为空，task 被误标为
`no_patch`；即使继续执行，下游 worker 和最终 `patch.diff` 也看不到该文件。

定位过程：增加只创建新文件的真实 AgentLoop worker 测试，并用 text/binary
untracked 文件验证 patch 是否能在 clean clone 上 `git apply --binary`。

根因：普通 `git diff` 只包含 tracked changes；run、SWE-bench、coordinator、
fanout 和模型可见 `git_diff` 各自实现，行为还不一致。

修复：新增共享 `runtime/git_workspace.py`，合并 HEAD-relative tracked diff 与
每个 untracked 文件的 binary no-index patch；统一 changed-file 列表，并接入
ExecutionEnvironment、SWE-bench、sequential coordinator、live fanout 和
GitDiffTool。

验证：`tests/test_git_workspace.py` 同时覆盖 tracked、untracked text、binary
patch 的可应用性；`test_new_file_is_scoped_merged_and_included_in_integration_patch`
验证 fanout 端到端合并。

工程结论：candidate patch 的定义必须全系统一致；否则 report、benchmark 和
merge gate 会对同一次运行给出不同事实。

可追问：为什么不能简单 `git add -N`？它会如何污染用户 index？

### 18. Runtime 自己的 `.agent_forge` 产物让 fanout 误判 workspace dirty

现象：在一个没有 `.gitignore` 规则的新仓库使用默认输出目录时，CLI 先写
`.agent_forge/runs/.../execution_environment.json`，随后 write fanout 因 dirty
workspace 拒绝启动；这些产物还可能被当成 untracked candidate patch。

定位过程：把 public entrypoint 测试的 output root 改回目标仓库默认位置，
立即复现 `write fanout requires a clean integration workspace`。

根因：clean gate 和 untracked patch collector 无法区分 runtime-owned artifact
与用户源码。

修复：共享 git status/diff 仅排除未跟踪的 reserved `.agent_forge` root；如果
该目录中的文件已被仓库跟踪，其改动仍正常计入 dirty/patch，避免隐藏真实改动。

验证：`test_untracked_runtime_artifacts_are_not_user_changes_or_candidate_patch`
和默认目录 public fanout entrypoint 测试。

工程结论：可观测性产物不能反过来改变被观测系统的 clean-state 语义。

可追问：为什么只排除 untracked reserved root，而不是对 `.agent_forge` 做全局
pathspec ignore？

### 19. Partial run 只有最终 summary，硬中断后无法选择性恢复

现象：独立 task A 已合并、task B 失败时可以从 final summary 恢复；但如果
进程在 summary 写入前中断，A 的完成状态不可发现。修改旧 patch 文件后，旧
恢复逻辑也会直接信任并应用。

定位过程：删除 partial run 的 `fanout_summary.json`，只保留 worker artifacts
后尝试 resume；再篡改 A 的 `patch.diff`。

根因：checkpoint 只在终态产生，且没有 artifact integrity 字段。

修复：运行前和每个 batch 后原子更新 `fanout_checkpoint.json`；记录 plan
digest、base commit、accepted task ids、patch path 和 SHA-256。恢复必须在 fresh
validation worktree 先验证全部 identity/hash 并顺序重放，再把最终 combined diff
一次应用到 integration workspace，只重跑 incomplete tasks；后序 patch 损坏时
不会留下前序部分恢复。

验证：`test_resume_reapplies_completed_patch_and_only_reruns_incomplete_worker`
强制从 checkpoint-only 恢复；`test_resume_rejects_a_tampered_completed_patch`
验证篡改拒绝；different plan/base 也 fail closed。

工程结论：partial recovery 的最小可信单元是“可验证 artifact + checkpoint”，
不是一句“支持 resume”。

可追问：为什么 checkpoint 要在 batch 后提交，而不是并发 worker 每写一步都
抢同一个状态文件？

### 20. Final verifier 在 integration workspace 运行且成本未计入

现象：verifier 虽然只看到 read tools，但 diagnostics/测试框架可能生成缓存；
它直接运行在 integration workspace，会污染最终状态。fanout report 的 token、
cost、latency 又只汇总 workers，漏掉 verifier 调用。

定位过程：检查 `_run_finalizer()` workspace 和 `_aggregate_metrics()` 输入，
确认 finalizer 使用 local mode 且 usage 独立写出后没有进入 aggregate。

根因：把“read-only tool allowlist”等同于“文件系统无副作用”，并把 finalizer
当成报告附属步骤而不是计费的真实 AgentLoop。

修复：finalizer 使用 disposable worktree，应用 integration candidate 但保留
diff 可见；运行前后比较完整 binary patch 快照，发现 mutation 则
BLOCKED 并丢弃 worktree。
finalizer trace/usage 路径和 usage summary 进入 machine-readable summary，所有
模型指标汇总 worker + finalizer。

验证：live fanout 主路径测试检查 finalizer manifest mode、cleanup、trace、usage
和 `max_workers` 指标。

工程结论：只读是效果、工具和文件系统三层契约；总成本必须覆盖所有模型角色。

可追问：`summed_worker_duration / wall_time` 为什么只能叫 ratio，不能直接宣称
speedup？

### 21. Fanout worker 的 human response 跨 run 丢失

现象：worker 第一次 `ask_human` 后进入 `waiting_human`，operator 已回答；
`--fanout-resume` 却创建新 run id、新 human root 和新 thread，worker 再问一次，
原回答无法消费。

定位过程：构造“ask -> stop -> respond -> clone clean base -> fanout resume -> edit”
完整测试，发现 shared store 中没有第一条 pending request。

根因：worker human state 被放在 run-local worker 目录，thread id 又依赖 run id，
不满足 selective rerun 的稳定身份要求。

修复：worker 使用 base config 的共享 HumanInputStore；thread id 由 plan digest、
base commit 和 task id 生成。`ask_human` 始终作为低风险 control signal 进入
task-aware tool view；matching resume 能加载回答后继续真实 AgentLoop。

验证：`test_fanout_resume_reuses_a_durable_worker_human_response`。

边界：per-operation manual write approval 仍依赖 ephemeral worktree identity，
当前 write fanout 对 `--no-auto-approve-writes` 明确 fail-fast；需要逐操作授权时
使用 single/sequential mode。

工程结论：clarification identity 可以 task-stable；write authorization identity
还必须绑定准确目标状态，两者不能为了“功能齐全”强行复用。

可追问：怎样设计 workspace-independent operation identity，且不把旧批准套到
重新生成的中间 patch 上？

### 22. FanoutVerifier `PASS` 覆盖了 candidate claim boundary

现象：fanout report 已写“不是 official resolution”，但 verifier 返回 `PASS`
时，`final_answer.txt` 只剩 `PASS` 和验证文本，用户最先看到的入口丢失边界。

定位过程：从 public CLI 跑完整 deterministic fanout，直接断言最终答案必须含
`candidate artifact`，测试先失败。

根因：CLI 使用 `summary.final_answer or fallback`；只要 verifier 有文本，就不会
拼接 fallback 中的 status/report/claim boundary。

修复：无条件组合 verifier answer、fanout status、report path 和 candidate/
official boundary；`fanout_done` 事件也只在最终 status passed 时标记 success。

验证：`test_public_run_entrypoint_routes_fanout_and_writes_candidate_patch`。

工程结论：claim boundary 必须出现在首要用户产物中，不能只藏在深层 report。

可追问：local verifier PASS、focused tests passed 与 official resolved 应如何
分别建模和统计 denominator？

### 23. 验证脚本返回 0，但真实 Agent 已经 blocked

现象：旧 `scripts/verify.sh` 在 CLI 进程正常退出后打印
`Verification passed`，但 single trace 的 stop reason 是
`pending_tool_call_at_stop`，fanout summary 是 `partial_failure`，finalizer 根本
没有运行。

定位过程：不信任 shell exit code，直接检查最新 run 的 `trace.json`、
`usage.json`、`fanout_summary.json` 和 `patch.diff`，发现进程成功只表示
产物被写出，不表示 agent task 完成。

根因：健康检查只验证了进程层，没有验证 runtime 状态机和证据
产物的语义。

修复：直接从本次 CLI 输出解析 run directory，不再依赖可被并发覆盖的
global latest pointer。single smoke 必须断言 `stop_reason=final_answer`、至少一次真实
LLM 调用且没有 candidate patch；fanout smoke 必须断言所有 worker
`completed`、summary `passed`、finalizer `PASS`、worker/finalizer usage 完整、
读取任务无 touched files 且 patch 为空。任一语义断言失败即返回非 0。

验证：先用已有 blocked run 确认新断言会拒绝假阳性；再运行真实
DeepSeek 双 worker 读取任务，得到 2/2 completed、finalizer PASS、空 patch
和无遗留 worktree。

工程结论：Agent 系统的 process success、runtime completion、candidate
validation 和 official evaluation 是四层不同证据。

可追问：CI 应以哪个 machine-readable artifact 作为 gate，如何防止旧
artifact 被当成本次运行？

### 24. Finalizer 引用 safety 证据时误触 input guardrail

现象：两个 worker 都完成了，但 finalizer 在第一次模型调用前被拦截。
safety worker 的报告中为了说明拒绝规则，引用了 `rm -rf`、`../`、
`.env` 等字面量，输入 guardrail 把这些“已观测证据”当成了“新的
用户操作意图”。

定位过程：对比 worker artifact 与 finalizer trace；前者正常、后者只有
input guardrail block，且命中的 marker 全部来自引用文本。

根因：guardrail 不区分 untrusted user instruction 和 coordinator 注入的已存档
evidence，属于 trust-boundary 误分类。

修复：把风险 marker 集中为 `RISKY_INPUT_MARKERS`；仅在构造 finalizer
内部 prompt 时用 `sanitize_quoted_evidence()` 替换引用 marker，原始 worker
artifact、trace 和 report 保持不变，不丢失审计证据。

验证：`test_finalizer_quotes_safety_evidence_without_triggering_user_input_guardrail`
覆盖该回归；真实 provider fanout 中 finalizer 后续正常运行并产生 usage。

工程结论：安全过滤不能只看 token，还必须知道这段文本在数据流中
是 instruction 还是 quoted evidence。

可追问：如果 artifact 来自不可信 worker，怎样同时防 prompt injection 和
保留原始取证内容？

### 25. Fanout 计划的自然语言预算不可执行

现象：smoke task 要求“最多两次 read”审查 1225 行
`agent_loop.py`，但 `read_file` 每次最多返回 240 行。worker 合理地继续
分页，最后在全局步数上限停在 `pending_tool_call_at_stop`。

定位过程：按 step 展开 worker trace，对比 task 文本、`read_file`
window 和实际 tool-call 序列，排除了并发、worktree 和 provider 连接问题。

根因：计划只有全局 `max_steps`，没有 per-task 机器可执行预算；
同时验证任务的证据范围与工具输出上限矛盾。

修复：`FanoutPlan` 增加并校验 `max_steps` 2..32，worker 实际使用
`min(global_max_steps, task.max_steps)`；样例任务改为两个可在单次
window 完整读取的模块，避免用随机收敛伪装 runtime 健康。

验证：`test_plan_validates_and_serializes_per_task_step_budget` 覆盖 schema；
`test_worker_uses_plan_step_budget_instead_of_global_ceiling` 证明 worker 在两步后
进入无工具 final turn。真实双 worker run 各 2 次 LLM 调用完成。

工程结论：prompt 中的“不超过 N 步”不是资源治理；预算必须进入
schema、runtime config、trace 和 report。

可追问：除了 step，是否还需要 per-tool call、token、cost 和 wall-clock
deadline？哪些应该 hard stop，哪些应该先进入 finalization？

### 26. Worktree worker 看不到主工作区未提交的新文件

现象：主工作区已有 `agent_forge/runtime/human_input.py`，但 read-only
worker 返回 `file not found`。工作区路径正确，其他已提交文件可正常读取。

定位过程：对比 `git status`、worker execution manifest 与 `git ls-tree HEAD`；
新文件只在主工作区，worker 的 detached worktree 正确指向当前 commit。

根因：Git worktree 从 commit tree 创建，不会隐式复制另一个 checkout 的
uncommitted index/worktree 状态。这是隔离边界，不是文件同步 bug。

修复：验证样例只依赖 `HEAD` 中存在的文件；文档明确 fanout worker
消费 committed snapshot。write fanout 继续对 dirty integration workspace fail closed；
read-only fanout 不把未提交变更悄悄注入 worker。

验证：真实 run 先稳定复现 missing file；换成两个 committed 小模块后，
2/2 worker completed、finalizer PASS、无 touched files 和遗留 worktree。

工程结论：隔离执行必须先定义 snapshot provenance；“当前目录有这个
文件”不等于“该 worker 的 base snapshot 有这个文件”。

可追问：如果产品需要 fanout 消费未提交状态，应生成 temporary tree/
stash commit，还是把 dirty diff 作为显式、可校验的 seed artifact？

### 27. 同一模型响应中的写操作抢在 `ask_human` 前执行

现象：模型一次返回 `[write_file, ask_human]`时，runtime 按数组顺序先
写文件，再进入 `waiting_human`。另外，字符串型 `choices="api"` 被
`list()` 拆成了三个单字符选项并持久化。

定位过程：构造真实 `AgentLoop` + `write_file` + `ask_human` 多 tool-call
响应，断言暂停时目标文件不存在；再用非 list choices 确认是控制
分支绕过 `ToolRegistry` schema validation，而不是 store 规范化问题。

根因：`ask_human` 被当成普通工具按顺序处理，没有“同轮控制屏障”
语义；AgentLoop 的特殊拦截又没有复用工具参数契约。

修复：只要响应含 `ask_human`，就优先且唯一处理第一个 human
call，其他工具记录为 `tool_calls_deferred_for_human_input`，不进入对话
历史也不执行。回答注入后，模型必须重新提出副作用。question/
choices 在落盘前严格校验，坏参数只作为可恢复 observation 回填。

验证：`test_human_control_signal_defers_same_turn_side_effects` 证明暂停前文件
未写；`test_invalid_tool_level_choices_do_not_create_a_request` 证明坏参数不产生
stale request。

工程结论：HITL 不只是一个工具；它是能够打断同一 assistant turn 中
其他副作用的 control-plane barrier。

可追问：如果一次响应包含多个 human question，应合并、排队，还是只接受
第一个？怎样维持 OpenAI tool-call history 完整性？

### 28. 隔离 Finalizer 看不到已集成的 candidate diff

现象：worker patch 已确定性合并，但 finalizer 调用 `git_diff` 得到
`no workspace changes`，只能信任 worker 的文本结论。

定位过程：用一个 finalizer fixture 强制先调 `git_diff`，只在 observation
包含真实 `diff --git` 和修改内容时返回 PASS；旧路径稳定返回
NEEDS_REVISION。

根因：为了识别 verifier 的额外变更，runtime 先把 candidate 提交成临时
baseline，同时也把唯一可见的 candidate diff 消掉了。

修复：finalizer worktree 应用 candidate 后不提交，运行前保存完整 binary
patch snapshot；这份 diff 可被治理后的 `git_diff` 直接审查。运行后再次
采集 patch，与 snapshot 不同即把 decision 降为 BLOCKED，且丢弃 worktree。

验证：`test_finalizer_can_inspect_integrated_candidate_diff` 使用真实 AgentLoop、
worktree、`apply_patch` 和 `git_diff` 覆盖完整路径。

工程结论：“verifier 在隔离环境运行”还不够；它必须能看到自己要验证
的 candidate，同时其自身又不能悄悄改写该 candidate。

可追问：对大 patch 如何做 diff ranking/分片，而不是把全量 patch 塞进 context？

### 30. Resume 把历史 worker 时长和成本算成本次并发收益

现象：任务 A 从旧 checkpoint 恢复，本次只重跑 B；summary 却用
`A.duration + B.duration` 除以本次 wall time，且 `llm_calls/tokens/cost` 也把 A
的历史用量标成本次消耗。

定位过程：在 selective-resume 端到端测试中分别读取 A/B result 的
`resumed`、`duration_ms`、`usage_summary`，再与 top-level metrics 逐项对账。

根因：聚合器把“完整证据链工作量”和“本次进程实际消耗”混成了
一组无 denominator 的指标。

修复：时长分为 `current_worker_duration_ms`、
`resumed_worker_duration_ms` 和总工作量；`worker_time_to_wall_ratio` 只用本次
worker。LLM/token/cost/tool 指标以原名表示本次，另存 `resumed_*` 和
`evidence_chain_*`。Markdown report 只分组展示决策指标，完整字段留在 JSON。

验证：`test_resume_reapplies_completed_patch_and_only_reruns_incomplete_worker`
对恢复/当前 duration 和三层 LLM call 数逐项断言；主路径测试检查
`Current Run Metrics` / `Recovery Accounting` 报告分组。

工程结论：恢复能力会改变指标 denominator。不显式区分 historical reuse
与 current execution，就无法做可解释的 latency/cost comparison。

可追问：跨多次 resume 的端到端 wall time 应如何存档？是按 logical task、
attempt 还是 operator-visible session 计费？

### 31. Workbench 桌面可用，窄屏首屏却溢出并遮挡证据

现象：390px viewport 下 header 的 `min-width: 360px` 操作区和标题并排，
导致 H1 被压成窄列、按钮与项目路径越出屏幕；9 个 view tabs 换成三行，
证据主体几乎消失在首屏。桌面 Role Timeline 表头也出现单词断裂，
flow label 对比度过低。

定位过程：启动真实 `forge ui`，分别用 1440x900 和 390x844
viewport 检查截图、DOM 和 `document.scrollWidth`；排除数据渲染问题后，定位到
header flex/min-width、tabs wrap 和高圆角/低对比 CSS。

根因：页面只做了“主网格在 900px 下变单列”，没有定义 header、导航和大表格
的窄屏稳定尺寸。sticky tabs 的 backdrop filter 还会在部分浏览器合成层中
吞掉 active background，导致白字白底。

修复：移动端 header 纵向排布，三个命令按钮等宽，路径独占一行；
view tabs 保持单行并在自身容器横向滚动；内容 padding 收紧，table header
禁止单词内断行。卡片/输入/命令控件统一为 8px 以下圆角，移除装饰渐变和
tabs backdrop filter，active 改为浅蓝底深蓝字。

验证：新 browser tab 在 1440x900 与 390x844 下视觉复验；移动端
`document/body/header.scrollWidth == innerWidth == 390`，tabs 仅内部滚动；打开操作
面板能看到 fanout plan/resume/worker 控件，console 无 error/warning。

工程结论：公开展示面不是“有 HTML 就行”；它要稳定露出 evidence，而不是
让布局本身抢走 reviewer 注意力。

可追问：为什么公开项目仍保留本地 workbench，而不把它包装成 production SaaS？

### 32. 动态字典让核心数据只能靠反向追调用链理解

现象：在 `AgentLoop` 看到 `trace.add(..., task_state=checkpoint.to_dict())`
时，函数签名无法说明 `task_state` 的字段、来源和消费者。类似问题还存在于
`TaskStateStore.update(**changes)`、未标注的 tool `execute(arguments)`，以及
coordinator/fanout 的无类型依赖注入。读者必须不断向上寻找构造点，再向下搜索
字符串 key，才能确认一个字段的真实形状。

定位过程：用 AST 统计 106 个生产模块的函数签名，并对全包运行 mypy。首次
检查得到 183 个问题，覆盖 51 个文件。除缺失注解外，检查还发现
`swebench.py` 在同一作用域用 `result` 同时表示 `BenchCaseResult` 和
`CompletedProcess`、`path` 同时表示字符串和 `Path`，以及 recovery 分支引用
旧 `signal` 变量等真实局部理解风险。

根因：项目已有 `TaskCheckpoint`、`Observation` 等 dataclass，但数据一进入
`to_dict / **kwargs / setattr` 边界就丢失类型；`TraceEvent` 虽然存在，却没有
参与真实写入。也就是说，类型对象和运行数据流不是同一套契约。

修复：新增共享 JSON/tool boundary 类型；让 `TraceEvent` 成为真实 envelope，
拒绝 payload 覆盖 `run_id/event_type`；checkpoint 改用
`record_task_state_checkpoint(checkpoint=...)`，序列化只发生在 recorder 内部；
task-state 更新改成显式关键字字段。为 context、tools、AgentLoop、coordinator、
fanout、evaluation 和 bench 全部生产函数补齐参数/返回类型，并把不同含义的
局部变量拆成 `evaluation_process`、`local_path`、`stored_baseline_prediction`。

验证：mypy 对 106 个生产模块零错误；`test_type_contracts` 用 AST 拒绝任何
缺失完整签名的新函数，验证 checkpoint trace 继续保持兼容的 flat JSON，并
验证 extension payload 不能伪造 envelope identity。完整行为回归继续覆盖
runtime、HITL、fanout、evaluation 和 UI。

工程结论：可读性不是注释数量，而是数据所有权能否在当前位置看见。内部状态
应保持具名对象，外部 JSON 可以显式保留动态性，但必须在边界验证；序列化和
字符串 key 不应提前侵入业务调用点。

可追问：哪些兼容性 trace event 应继续从通用 `add` 迁移为具名 `record_*`
方法？如何在不引入复杂泛型的前提下，让历史 JSON schema 也能版本化？

### 33. Evidence Console 只展示路径，后续 Harness 能力在页面中失踪

现象：公开工作台能看到 `comparison.json`、`multi_agent_summary.json` 和
`usage.json` 的绝对路径，但看不到 artifact 正文、生产者、消费方和决策内容；
时间线只选一个最新 trace，因此 compare run 的 single AgentLoop 被隐藏。
恢复、HITL、审批、隔离、工具路由、Skills/MCP、fanout 和反馈数据闭环虽然已在
runtime 落地，页面仍主要展示 Single/Multi 成本表，形成“代码有能力、展示面没
证据”的断层。

定位过程：用固定 Astropy compare run 对照 `artifact_index.json`、四份角色
Markdown、single/multi 两份 `trace.json` 与页面 DOM。确认旧 renderer 把 artifact
压成 path table，`_latest_trace_path` 只能返回一个文件，feedback/export 只有静态
命令说明。再以 1440x900 和 390x844 viewport 检查信息层级和横向溢出。
浏览器验收还发现：默认收起侧栏时旧 `display:none` 与新两列 grid 叠加，桌面
证据列宽变成 0；控制面首版又误读 `decision` 和顶层 `tool_routing`，而真实契约是
`permission_decision` 与 `context.tool_routing`，导致权限和可见工具统计假零。

根因：早期 UI 按“调试文件浏览器”组织，而 runtime 已演进为控制面和评测闭环；
页面没有随 artifact schema 和能力边界升级。静态 capability 文案还会混淆
“项目支持”与“本次运行观察到”。

修复：工作台更名为 NanoHarness Evidence Console，主导航按 Overview、Runtime
Controls、Orchestration、Evaluation、Single vs Multi、Efficiency、Timeline 和
Feedback Loop 组织。artifact renderer 直接读取角色 Markdown，展示正文摘要、
producer、consumer、round 和 provenance；Timeline 固定 Multi 在前、Single 在后。
控制面从 trace 提取 execution environment、network、tool visibility、permission、
checkpoint、human/recovery event，并以零值明确标记本次未触发。页面运行参数真实
接入 isolation/network/tool-routing/manual approval；feedback 与默认不导出 patch
正文的数据集投影成为可执行 job。Claim ladder 严格区分 candidate patch、runtime
verifier、official evaluation 和 human judgment。
最终以真实 trace 字段修正 permission/intervention/tool-routing 解析，并增加
artifact 正文、嵌套 context 和 approval/recovery 的行为回归。
HTTP writer 同时把浏览器主动关闭连接视为正常 teardown，避免本地 server 输出
与运行失败无关的 `BrokenPipeError`。

验证：169 项 unittest 全绿；mypy 对 106 个生产模块零错误。Playwright 在
1440x900 与 390x844 下确认页面级 `scrollWidth == innerWidth`，Overview 显示四个
真实角色 artifact，Timeline 同时存在两条 lane 且 Multi 索引早于 Single；各新
view 可从导航加载，控制台无应用错误。

工程结论：展示面不能只是 JSON 路径目录。Agent Harness 的 UI 应直接回答：本次
运行受什么控制、发生了什么、产出了什么证据、最多允许声称什么、反馈如何回到
评测数据；同时必须区分 supported capability 与 observed evidence。

可追问：当一个 benchmark run 有多个 case 时，evidence console 应如何提供 case
selector，并保证 summary、trace、artifact 与 feedback 始终指向同一 case？

### 34. 方法签名完整，但折叠阅读时所有 `def` 仍然同权

现象：核心对象已经有类型标注，读者仍无法从折叠后的类快速判断先读哪个方法。
例如 `TaskStateStore.start/update/save/load` 在视觉上权重相同，容易把 checkpoint
持久化端口误当成 HITL 或恢复能力入口；理解一次人工暂停还要反向搜索 CLI、
AgentLoop 和 store 的调用者。

定位过程：从 Capability Reality Matrix 的每项能力出发，沿真实调用链逐一确认
外部入口、跨模块端口和内部实现。HITL 最终确认是三个参与者动作：
`AgentLoop.run` 发起并停机、`respond_to_human_input` 写回答、
`resume_repository_task` 开启 continuation；`HumanInputStore.request/respond` 只是
状态持久化端口。对工具治理、隔离、fanout、评测、反馈、Skills 和 MCP 做同样审计。

根因：类型系统回答“数据是什么”，原有 method map 只存在于少数大类；项目没有
统一回答“能力从哪里进入、下一个 owner 是谁、哪些方法第一遍可以跳过”。仅靠
public/private 命名也不够，因为 store 的 public API 并不等于用户可见能力入口。

修复：在真实编排方法上统一增加 `PRIMARY ENTRYPOINT`，在跨模块的策略、持久化、
证据边界增加 `RUNTIME PORT`；入口 docstring 写明 caller、下一 owner 和 evidence。
`code-reading-map.md` 新增覆盖 runtime、HITL、恢复、安全、多 Agent、评测、反馈、
MCP、Skills 和 UI 的方法级索引，以及三遍折叠阅读法。`CONTRIBUTING.md` 将该层级
纳入后续贡献规范；AST 回归检查确保入口标记和导航 docstring 不会静默消失。

验证：`test_code_navigation` 逐文件定位指定函数，检查标记紧邻定义，并要求所有
主入口有 docstring；完整验证继续运行 mypy、行为回归和文档/格式检查。代码标记
只是注释，不引入 decorator、注册表或运行时分支。

工程结论：可读性需要两套正交信息：类型说明局部数据契约，入口层级说明系统
控制流。真正适合折叠阅读的代码，应允许第一遍只展开主入口，第二遍才沿端口进入
策略和持久化，最后按具体故障打开私有实现。

可追问：为什么不使用 decorator 给入口打标签？因为这里的目标是静态导航，普通
注释在 IDE 折叠视图中更直接，也避免为了文档引入运行时元数据和额外抽象。

## 调试顺序模板

每次 SWE-bench 失败优先看：

1. `results.json`：最终 status、failure_class、diagnosis、patch_chars。
2. `comparison.json`：single vs multi 的成本、调用数、失败数。
3. `usage.json`：工具调用分布、failed_tool_calls、最后 action。
4. `trace.json`：stop_reason、permission/tool routing/recovery_decision。
5. `multi_agent_summary.json`：哪个 role 失败、artifact 是否完整。
6. `patch.diff`：是否真的有 workspace diff。

Fanout/HITL 额外看：

7. `fanout/fanout_checkpoint.json`：plan/base、accepted tasks 和 patch hash。
8. `fanout/fanout_report.md`：batch、scope、merge、resume、worker/finalizer usage。
9. `fanout/workers/<task-id>/trace.json`：具体 worker 为什么 blocked/waiting。
10. `human_input/<request-id>.json`：问题是否 pending/responded/cancelled，thread
    identity 是否与 resumed task 一致。

## Design Conclusions

> 这次不是简单调 prompt，而是沿着真实 evidence 逐层修 runtime：ToolRouter 负责 task-aware 工具收敛，ReadFileTool 修正模型常用 line-window 契约，DiagnosticsTool 区分 code failure 和 validation environment unavailable，Coordinator 区分 candidate patch 与 official resolved，verdict parser 兼容真实 Markdown 输出。最终从 UI 点击 Run Reference Case，single/multi 都生成相同 candidate patch，Reviewer/Verifier 在 artifact 中给出 PASS。这个过程体现的是 agent harness 的工程闭环，而不是一次性 demo。

# 小型 Regression Set

NanoHarness 在开发中使用高信号的小型 regression set，而不是追求广泛 benchmark
覆盖。内置 `core` 命令固定五个来自不同仓库的公开 SWE-bench Lite case；下表同时
记录本地 runtime 和非 coding contract。

## 固定 SWE-bench Lite Core Set

| Case | Repository 行为 | 价值 |
| --- | --- | --- |
| `astropy__astropy-12907` | 嵌套 `CompoundModel` separability。 | Patch 小，但需要非平凡代码导航，并已有 failure-driven case study。 |
| `django__django-11133` | `HttpResponse` 处理 `memoryview`。 | 聚焦 data-type boundary 的 framework compatibility fix。 |
| `matplotlib__matplotlib-18869` | 顶层 version information 可比较。 | 跨文件 public API behavior 和 backward compatibility reasoning。 |
| `pytest-dev__pytest-5103` | `all`/`any` 的 assertion rewriting。 | Parser/rewrite 行为会影响 test report quality。 |
| `sympy__sympy-20590` | 非预期 `Symbol.__dict__` regression。 | 大型 symbolic codebase 中的 object layout 和 inheritance reasoning。 |

```bash
forge bench swebench --regression-set core --provider deepseek \
  --model deepseek-chat --tool-routing task-aware --evaluate
```

每次运行生成 `scorecard.json` 和 `scorecard.md`。没有显式 resolved/unresolved report
时，不报告 official resolved rate。

## 目标覆盖图

| Case | 目的 | 主要 failure mode |
| --- | --- | --- |
| `astropy__astropy-12907` | 真实 SWE-bench patch path 和 line-window tool behavior。 | `tool_schema_mismatch` / `patch_generated_but_unverified` |
| `validation-env-unavailable` | 区分 code failure 与 test dependency 缺失。 | `validation_environment_unavailable` |
| `tool-governance-blocked-command` | 说明为什么 free-form shell/write tool 必须收敛。 | `unsafe_or_blocked_command` |
| `context-miss-file-selection` | 确认 edit decision 前出现预期 source file。 | `context_miss` |
| `repeated-action-loop` | 确认重复 read/search 可恢复，重复 write 被阻断。 | `repeated_action_loop` |
| `manual-approval-pending` | 确认 manual approval 在副作用前停机，批准后可以继续。 | `human_approval_required` |
| `stale-approval-fingerprint` | Target 在审批后改变时，已批准副作用不能执行。 | `approval_stale` |
| `resume-state-continuation` | Checkpoint summary 为 next run 提供上下文，但不声称 hidden chat replay。 | `partial_execution_recovery` |
| `subagent-fanout-conflict` | 独立 task 可同 batch；write scope 重叠需要 conflict resolution。 | `subagent_conflict_resolution` |
| `operation-ledger-idempotency` | 已执行 side effect 在 rerun/resume 时被跳过。 | `duplicate_side_effect_prevented` |
| `operation-ledger-stale-target` | Target drift 后，历史 executed operation 不能被安全跳过。 | `stale_operation_record` |
| `research-citation-quality` | 非 coding 场景，检查 source-backed claim 和 source limitation。 | `unsupported_claim_control` |
| `ops-approval-workflow` | 非 coding 场景，检查 policy-sensitive action、HITL 和 audit summary。 | `human_approval_required` |

## 指标

- patch generated
- local verified
- official resolved（有 official evidence 时）
- failure class
- tool calls
- failed tool calls
- repeated actions
- context files selected
- estimated cost
- latency
- human intervention count
- duplicate side-effect skips
- stale approval / stale operation count
- unsupported claim count

## 非 Coding Mini-Case

`docs/evaluation/mini-cases/` 存放小型 JSON case，它们不是完整 benchmark，而是覆盖
code repair 之外 Agent application 共用的 evaluation dimension。
`agent_forge/evaluation/application/mini_cases.py` 负责确定性评估，case 文件加载和
artifact 写入由 `evaluation/adapters/mini_case_files.py` 负责。

运行方式：

```bash
forge eval mini-cases --case research-citation-quality --evidence evidence.json
```

## 规则

Runtime change 只有在至少一个 case 上改进 success、observability、failure localization、
cost 或 safety boundary，同时没有隐藏其他 case regression，才算有价值。

比较 runtime factor 时使用 `forge eval ablation` 和 matched run；不同 model、dataset、
split 或 case id 的 run 不应直接比较。

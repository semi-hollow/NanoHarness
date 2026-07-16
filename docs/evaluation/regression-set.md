# 小型 Regression Set

## 评测目的

NanoHarness 使用 SWE-bench Lite `test` split 的 300 个公开 case 作为候选全集。内置
`smoke-5` 的目标不是报告模型排行榜成绩，而是用可承受的成本回归 Harness 的代码检索、
工具循环、patch 生成、验证和 evidence pipeline。SWE-bench Lite 的规模与筛选规则见
[官方说明](https://www.swebench.com/lite.html)。

五个 case 是**人工分层 smoke sample**，不是随机样本，也不具有统计代表性：

1. 每个 case 来自不同仓库和不同问题族；
2. 每个参考修复只涉及一个源码文件，且不超过三个 hunk，便于把失败归因到 Harness；
3. 同时保留最小修复、多分支解析、多 hunk AST 编辑等不同 patch 形态；
4. 每题都有 `FAIL_TO_PASS` 与 `PASS_TO_PASS`，可以区分目标修复和回归保护；
5. 五题足以做每次提交的低成本机制诊断，不足以估计 SWE-bench Lite 总体解决率。

## 固定 SWE-bench Lite Smoke-5

下表中的参考 patch 规模只用于运行后的样本审计，不会进入 Agent prompt。

| Case | 问题族 | F2P / P2P | 参考 patch 规模 | 主要观察点 |
| --- | --- | ---: | --- | --- |
| `astropy__astropy-12907` | 算法正确性 / 嵌套组合 | 2 / 13 | 1 file, 1 hunk, +1/-1 | 代码定位、语义推理、最小 patch |
| `django__django-11133` | 类型边界 / Framework 兼容 | 1 / 64 | 1 file, 1 hunk, +1/-1 | 类型识别、公共 API、回归保护 |
| `matplotlib__matplotlib-18869` | 公共 API / 版本解析 | 4 / 3 | 1 file, 1 hunk, +51/-16 | 多分支实现、边界输入、兼容性 |
| `pytest-dev__pytest-5103` | AST Rewrite / 可诊断性 | 1 / 64 | 1 file, 3 hunks, +25/-0 | AST 导航、多 hunk 编辑、错误报告 |
| `sympy__sympy-20590` | 继承语义 / 对象布局 | 1 / 21 | 1 file, 1 hunk, +5/-0 | 继承链定位、非局部根因、回归保护 |

## 查看 Case，而不是背 ID

```bash
# 集合目标、300-case 候选全集、选择方法、每题入选原因和结论边界
forge bench cases

# issue、base commit、FAIL_TO_PASS、PASS_TO_PASS；不执行 Agent
forge bench case astropy__astropy-12907

# 仅在运行后复盘官方验收实现或参考答案
forge bench case astropy__astropy-12907 --show-test-patch
forge bench case astropy__astropy-12907 --show-gold
```

默认输出不会包含 official test patch 或 gold patch。这样既方便解释每题具体怎样测，
也把数据泄漏边界变成可执行契约，而不是口头约定。

## 运行与结论边界

```bash
forge bench swebench --regression-set smoke-5 --provider deepseek \
  --model deepseek-chat --temperature 0 --tool-routing task-aware --evaluate
```

每次运行生成 `results.json`、`scorecard.json`、`scorecard.md` 和 `report.md`。Sampling
temperature 会进入真实请求、run artifact 和 matched-run identity。没有显式 per-case
resolved/unresolved report 时，不报告 official resolved rate；只有五题或每个 variant
只运行一次时，不向外推一般模型质量。

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
| `context-window-tool-transaction` | 压缩旧历史时 assistant tool intent 与对应 result 不被拆开，失败仍保留。 | `context_compaction_loss` |
| `long-term-memory-authority` | Candidate 不召回；带证据的 active 记录受 namespace、agent 和 TTL 约束。 | `memory_contamination` |
| `model-tool-call-repair` | 只修复可确定的参数格式，可见工具外的文本调用不提升。 | `tool_schema_mismatch` |
| `tool-call-burst-bound` | 单次模型响应超额调用不会全部执行，HITL 仍是 barrier。 | `unbounded_tool_burst` |
| `failed-model-usage-accounting` | Provider 失败与 overflow 首次调用仍进入累计成本和 usage。 | `usage_underreporting` |
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
- context compacted / overflow recovered
- active long-term memories recalled
- model tool-call repairs
- bounded tool-call bursts
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
split 或 case id 的 run 不应直接比较。Memory 实验还必须固定 snapshot SHA-256，Skill
实验必须记录 manifest SHA-256；召回或激活次数只能证明机制被触发。

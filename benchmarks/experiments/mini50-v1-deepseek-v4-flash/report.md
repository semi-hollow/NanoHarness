# SWE-bench Verified Mini-50 实验报告

## 结论

NanoHarness 使用固定 `opencode-go/deepseek-v4-flash` quality profile，在预先固定的 50 个
SWE-bench Verified Case 上取得 **28/50 official resolved（56.0%）**，Wilson 95% 置信区间为
42.3%–68.8%。其余结果为 16 个 official unresolved 和 6 个 Agent terminal Empty Patch；
50 个 Case 全部形成可归因能力终态，最终基础设施无效槽位为 0，Final Publish Gate 通过。

这个数字只适用于该固定 Mini-50 与该模型/Runtime 配置，不是完整 500 题排行榜成绩，也不能单独归因于
Harness。它回答的是“当前 NanoHarness + V4 Flash 固定系统在这 50 题上解决多少”，不是
“NanoHarness 相对裸模型提升多少”。

## 补全策略与分母

原始 Run 的 50 个槽位全部被保留，但审计后只有 40 个可作为能力结果：23 resolved、12 unresolved、
5 Agent terminal Empty Patch。另 8 个 Case 因 provider transport failure 没有形成有效轨迹，2 个 Case
被外部手动中断。原始 `23/50` 因此只是一项被发布门拒绝的观察值。

补全资格在任何补全 outcome 出现前由失败类型确定：

| 阶段 | 启动 | Resolved | Unresolved | Agent Empty | Infra invalid | 处理 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 原始 Mini-50 | 50 | 23 | 12 | 5 | 10 | 只冻结 10 个 infra-invalid ID |
| v1.1 completion | 10 | 4 | 4 | 1 | 1 | 9 个结果终态；仅余 `sympy-12481` |
| v1.2 completion | 1 | 1 | 0 | 0 | 0 | 合并其余 49 个不可变结果 |
| 最终有效分母 | 50 | **28** | **16** | **6** | **0** | Publish Gate PASS |

总共发生 61 次启动，但没有 correctness rerun：resolved、official unresolved、工具熔断、预算终止等
Agent 终态都在第一次有效轨迹后冻结。只有 provider/外部中断且没有可用能力终态的槽位进入补全。

## 最终逐 Case 分类

| # | Case | 最终分类 | 证据来源 |
| ---: | --- | --- | --- |
| 1 | `django__django-11206` | Agent terminal Empty Patch | 原始 Run |
| 2 | `django__django-12262` | resolved | 原始 Run |
| 3 | `django__django-12419` | resolved | 原始 Run |
| 4 | `django__django-13128` | Agent terminal Empty Patch | 原始 Run |
| 5 | `django__django-13346` | resolved | v1.1 completion |
| 6 | `django__django-13809` | resolved | v1.1 completion |
| 7 | `django__django-14725` | unresolved | 原始 Run |
| 8 | `django__django-15569` | resolved | 原始 Run |
| 9 | `django__django-16493` | resolved | 原始 Run |
| 10 | `django__django-7530` | resolved | 原始 Run |
| 11 | `pydata__xarray-3151` | Agent terminal Empty Patch | 原始 Run |
| 12 | `pylint-dev__pylint-4551` | unresolved | 原始 Run |
| 13 | `pytest-dev__pytest-5631` | unresolved | 原始 Run |
| 14 | `sphinx-doc__sphinx-9367` | resolved | 原始 Run |
| 15 | `sympy__sympy-15017` | resolved | 原始 Run |
| 16 | `sympy__sympy-15599` | unresolved | 原始 Run |
| 17 | `sympy__sympy-17318` | unresolved | 原始 Run |
| 18 | `sympy__sympy-18763` | unresolved | 原始 Run |
| 19 | `sympy__sympy-22914` | resolved | 原始 Run |
| 20 | `sympy__sympy-23824` | resolved | 原始 Run |
| 21 | `django__django-14855` | resolved | 原始 Run |
| 22 | `django__django-15128` | Agent terminal Empty Patch | 原始 Run |
| 23 | `django__django-11333` | resolved | 原始 Run |
| 24 | `django__django-14053` | resolved | 原始 Run |
| 25 | `django__django-11099` | resolved | 原始 Run |
| 26 | `django__django-13658` | resolved | 原始 Run |
| 27 | `astropy__astropy-7336` | unresolved | 原始 Run |
| 28 | `django__django-12304` | unresolved | 原始 Run |
| 29 | `django__django-14011` | unresolved | 原始 Run |
| 30 | `django__django-16901` | resolved | 原始 Run |
| 31 | `django__django-15930` | Agent terminal Empty Patch | 原始 Run |
| 32 | `matplotlib__matplotlib-24970` | resolved | 原始 Run |
| 33 | `django__django-15629` | unresolved | 原始 Run |
| 34 | `scikit-learn__scikit-learn-11310` | resolved | 原始 Run |
| 35 | `psf__requests-1766` | resolved | 原始 Run |
| 36 | `pallets__flask-5014` | resolved | 原始 Run |
| 37 | `matplotlib__matplotlib-23476` | unresolved | 原始 Run |
| 38 | `sympy__sympy-13551` | unresolved | 原始 Run |
| 39 | `sympy__sympy-13480` | resolved | 原始 Run |
| 40 | `django__django-16950` | unresolved | v1.1 completion |
| 41 | `sympy__sympy-15349` | resolved | v1.1 completion |
| 42 | `sphinx-doc__sphinx-9281` | resolved | 原始 Run |
| 43 | `scikit-learn__scikit-learn-10908` | resolved | 原始 Run |
| 44 | `sympy__sympy-12481` | resolved | v1.2 completion |
| 45 | `sympy__sympy-15875` | unresolved | v1.1 completion |
| 46 | `sphinx-doc__sphinx-9461` | Agent terminal Empty Patch | v1.1 completion |
| 47 | `sympy__sympy-20428` | unresolved | v1.1 completion |
| 48 | `sympy__sympy-16450` | resolved | 原始 Run |
| 49 | `scikit-learn__scikit-learn-25747` | unresolved | v1.1 completion |
| 50 | `scikit-learn__scikit-learn-25973` | resolved | v1.1 completion |

## Agent Empty Patch 诊断

这 6 个结果不是 provider/评测基础设施失败，而是 Agent 自身没有形成 Patch：

| Case | Stop reason | 可观测失败链 |
| --- | --- | --- |
| `django__django-11206` | 连续工具失败熔断 | shell operator 被策略拒绝，随后命令不在 allowlist |
| `django__django-13128` | 连续工具失败熔断 | 本地验证缺少 `asgiref`，随后出现越界路径、非 allowlist 与 shell operator 拒绝 |
| `pydata__xarray-3151` | `timeout_exceeded` | 长时间只读检索，没有写入，也没有形成 candidate Patch |
| `django__django-15128` | 连续工具失败熔断 | 本地验证缺少 `asgiref`，随后越界路径、非 allowlist、受保护 `.venv` 与缺失路径连续失败 |
| `django__django-15930` | 连续工具失败熔断 | 本地验证缺少 `asgiref`，随后 shell operator、越界路径与非 allowlist 连续失败 |
| `sphinx-doc__sphinx-9461` | 连续工具失败熔断 | 本地验证缺少 `docutils`，随后三次命令不在 allowlist |

这 6 条 Trace 中没有检测到“连续完全相同 Tool 名 + 完全相同参数”的序列；这里的主要问题是失败后的
策略迁移仍连续碰到环境/权限边界，而不是字节级相同调用死循环。Workbench 已支持同时查看完整 Tool
参数、Observation、上一轮反馈和停止原因；若未来出现完全同参重复，会在“连续重复 ToolCall”中单独列出。

## 证据完整性

- source/config/cohort/model identity checks 全部通过；source 固定为 `3ec537113a...`。
- 最终 50 条轨迹全部请求 `opencode-go/deepseek-v4-flash`，fallback 为 0。
- 最终 44 个非空 Patch 的 `candidate_changes.diff`、`predictions.jsonl.model_patch` 与 official
  `patch.diff` 逐字节一致，44/44 无缺失、无不一致。
- 最终选中的 50 条轨迹共有 1,056 次 LLM call；1,055 次成功响应都由 provider 回报
  `deepseek-v4-flash`。
- `sympy__sympy-16450` 在已经形成可评 Patch 后，末尾一次 LLM call 两次 transport attempt 都失败；
  该 Patch 随后由 official evaluator 判为 resolved。它不在预先冻结的 10 个无效槽位中，也没有被重跑。
  因此“最终 infra invalid = 0”表示没有仍缺能力终态的槽位，不表示整次实验从未出现瞬时 transport 事件。
- 原始 50 + v1.1 10 + v1.2 1 的完整实验成本约 43.95M Token、`$5.381542`；最终选中的
  50 条有效轨迹约 42.70M Token、`$5.226957`。

## 可对外表述

可以准确表述为：

> 我用固定 SWE-bench Verified Mini-50、固定 DeepSeek V4 Flash quality profile 和
> correctness Pass@1 流水线完成测量。基础设施中断按预先定义的 failure taxonomy 单独补全，
> correctness 结果不重跑；最终 50/50 都有可归因终态，其中 28 个 official resolved，Pass@1 为 56%。
> 同时保存了 16 个 official unresolved、6 个 Agent Empty Patch 及其 Trace，用来定位后续优化方向。

不能把结果写成完整 SWE-bench Verified 500 题成绩，也不能说“Tool 优化单独带来了 56%”；这是当前
固定系统的绝对能力测量。

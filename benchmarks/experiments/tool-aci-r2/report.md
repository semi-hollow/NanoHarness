# Tool / ACI Golden-20 · R2 收缩实验报告

## 结论

- Tool-R0：**14/20** official resolved。
- Tool-R2：**14/20** official resolved。
- Tool-R0 / R2 比例与 Wilson 95% 区间：**70.0% [48.1%, 85.5%]** / **70.0% [48.1%, 85.5%]**。
- 净变化：**+0 Case / +0.0 个百分点**。
- 逐题转换：`{'resolved_to_resolved': 13, 'resolved_to_unresolved': 1, 'unresolved_to_resolved': 1, 'unresolved_to_unresolved': 5}`。
- 冻结门禁裁决：**reject**。
- McNemar exact two-sided p：`1.0`；20 题开发集只提供方向性证据，不作总体显著性或榜单声明。

## 实验身份

- Baseline Runtime source：`0ae0d9ae9444d723e319fc0c7eadec9b631d374c`
- Baseline preregistration：`c5fb4b884019e7dabebe1b8b0afe1cec521e2f3b`
- Treatment：`563a99fe72b078fa91bfb682d60d6d19f398a864`
- Frozen run source：`d7fc8110f9ec6bde7f7f794fb06f25986d279448` (`tool-aci-golden-20-r2-prerun-20260813`)
- 模型：`opencode-go/deepseek-v4-flash`
- 样本：固定 Golden-20 development cohort；本轮已经见过历史 outcome，不是 holdout。
- 口径：Pass@1、同 Case 顺序、同模型/预算/Runtime/evaluator；唯一主要变量为 Tool / repository context bundle。

## 逐 Case 结果

| # | subset | Case | R0 | R2 | transition |
| ---: | --- | --- | --- | --- | --- |
| 1 | `seen_regression` | `django__django-11451` | resolved | resolved | `resolved_to_resolved` |
| 2 | `seen_regression` | `matplotlib__matplotlib-13989` | resolved | resolved | `resolved_to_resolved` |
| 3 | `seen_regression` | `scikit-learn__scikit-learn-14629` | resolved | resolved | `resolved_to_resolved` |
| 4 | `seen_regression` | `django__django-12209` | resolved | resolved | `resolved_to_resolved` |
| 5 | `seen_regression` | `sphinx-doc__sphinx-10323` | resolved | resolved | `resolved_to_resolved` |
| 6 | `seen_regression` | `sympy__sympy-20590` | unresolved | resolved | `unresolved_to_resolved` |
| 7 | `seen_regression` | `django__django-10097` | unresolved | unresolved | `unresolved_to_unresolved` |
| 8 | `seen_regression` | `psf__requests-2317` | unresolved | unresolved | `unresolved_to_unresolved` |
| 9 | `seen_regression` | `matplotlib__matplotlib-22871` | resolved | resolved | `resolved_to_resolved` |
| 10 | `seen_regression` | `django__django-13028` | resolved | resolved | `resolved_to_resolved` |
| 11 | `fresh_extension` | `astropy__astropy-14182` | resolved | unresolved | `resolved_to_unresolved` |
| 12 | `fresh_extension` | `matplotlib__matplotlib-25287` | resolved | resolved | `resolved_to_resolved` |
| 13 | `fresh_extension` | `psf__requests-2931` | unresolved | unresolved | `unresolved_to_unresolved` |
| 14 | `fresh_extension` | `pydata__xarray-6938` | unresolved | unresolved | `unresolved_to_unresolved` |
| 15 | `fresh_extension` | `pylint-dev__pylint-6903` | resolved | resolved | `resolved_to_resolved` |
| 16 | `fresh_extension` | `pytest-dev__pytest-5262` | resolved | resolved | `resolved_to_resolved` |
| 17 | `fresh_extension` | `scikit-learn__scikit-learn-13328` | resolved | resolved | `resolved_to_resolved` |
| 18 | `fresh_extension` | `sphinx-doc__sphinx-9591` | resolved | resolved | `resolved_to_resolved` |
| 19 | `fresh_extension` | `sympy__sympy-13372` | resolved | resolved | `resolved_to_resolved` |
| 20 | `fresh_extension` | `django__django-10999` | unresolved | unresolved | `unresolved_to_unresolved` |

## 资源与工具行为

| 指标 | R0 | R2 | Δ |
| --- | ---: | ---: | ---: |
| LLM calls | 486 | 377 | -109 |
| Total tokens | 18800473 | 12486369 | -6,314,104 |
| Estimated cost (USD) | 2.28597 | 1.489998 | -0.796 |
| Tool calls | 673 | 533 | -140 |
| Search calls | 261 | 207 | -54 |
| grep_search | 232 | 162 | -70 |
| find_files | 0 | 45 | +45 |
| read_file | 217 | 173 | -44 |
| Validation calls | 100 | 87 | -13 |
| Failed tool calls | 45 | 39 | -6 |
| Failed validations | 35 | 28 | -7 |
| Patch bytes | 20344 | 19081 | -1,263 |
| Files changed (case-sum) | 27 | 26 | -1 |
| Mean tool calls before first edit | 15.95 | 13.45 | -2.500 |
| Mean search calls before first edit | 7.85 | 6.95 | -0.900 |
| Mean read calls before first edit | 7.25 | 6.15 | -1.100 |
| Repo-outline contexts | 0 | 0 | +0 |
| Validation head/tail | 0 | 9 | +9 |

## Treatment 激活证据

- `grep_search`：Treatment 运行中调用 `162` 次；实现绑定到 `rg` 子进程。
- `find_files`：在 `377` 个 Context 中可见，实际调用 `45` 次。
- `list_files`：SWE-bench Context 可见 `0` 次，实际调用 `0` 次。
- `repo_outline`：进入 `0` 个 Context（必须为 0）。
- Validation output window：进入 `66` 次；其中真实截断并保留 head/tail `9` 次。
- `apply_patch` 按预注册协议 defer，没有进入 Treatment 变量。

## 工程判断

- 过程指标仅用于解释探索深度和效率，不作为 correctness proxy。
- 预注册门禁裁决为 `reject`；是否保留 R2 以该门禁为准。

## 证据边界

- 20/20 两侧均要求 candidate diff、prediction.model_patch 与 official evaluator patch 字节一致。
- official outcome 只读取 run 级 safe aggregate；未读取 gold、逐测 tests_status、test_output 或 run_instance.log。
- Agent generation 均为一次 Pass@1；R0 部分 official evaluator 基础设施失败只对同一冻结 prediction 做 evaluator-only 重试，没有重新生成 Patch。
- Provider 内建 transport retry 按冻结配置最多 2 次；R0/R2 分别观察到 3/2 个 retried calls，均无 fallback。
- 本实验评估整个最小 Tool / ACI bundle，不能把变化单独归因于其中某一个组件。
- Golden-20 是固定开发集，不是 holdout，也不是 SWE-bench Verified 500 题榜单成绩。

## 核心代码

以下路径绑定到 R2 Treatment commit：

- `agent_forge/tools/grep.py`
- `agent_forge/tools/find_files.py`
- `agent_forge/tools/output_window.py`
- `agent_forge/tools/python_validation.py`
- `agent_forge/tools/run_command.py`
- `agent_forge/tools/tool_router.py`

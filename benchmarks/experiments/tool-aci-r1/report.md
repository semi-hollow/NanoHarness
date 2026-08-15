# Tool / ACI Golden-20 成对 A/B 实验报告

## 结论

- Tool-R0：**14/20** official resolved。
- Tool-R1：**13/20** official resolved。
- Tool-R0 / R1 比例与 Wilson 95% 区间：**70.0% [48.1%, 85.5%]** / **65.0% [43.3%, 81.9%]**。
- 净变化：**-1 Case / -5.0 个百分点**。
- 逐题转换：`{'resolved_to_resolved': 12, 'resolved_to_unresolved': 2, 'unresolved_to_resolved': 1, 'unresolved_to_unresolved': 5}`。
- 冻结门禁裁决：**reject**。
- McNemar exact two-sided p：`1.0`；20 题开发集只提供方向性证据，不作总体显著性或榜单声明。

## 实验身份

- Baseline Runtime source：`0ae0d9ae9444d723e319fc0c7eadec9b631d374c`
- Baseline preregistration：`c5fb4b884019e7dabebe1b8b0afe1cec521e2f3b`
- Treatment：`296000864d6a2c1476c28b790f030b0ffc4cca5b`
- Reject rollback：`a79d71051e0b968df81e5cc0f0851d434e89f358`
- 模型：`opencode-go/deepseek-v4-flash`
- 样本：固定 Golden-20；前 10 题为 seen regression，后 10 题为 outcome-blind fresh extension。
- 口径：Pass@1、同 Case 顺序、同模型/预算/Runtime/evaluator；唯一主要变量为 Tool / repository context bundle。

## 逐 Case 结果

| # | subset | Case | R0 | R1 | transition |
| ---: | --- | --- | --- | --- | --- |
| 1 | `seen_regression` | `django__django-11451` | resolved | resolved | `resolved_to_resolved` |
| 2 | `seen_regression` | `matplotlib__matplotlib-13989` | resolved | resolved | `resolved_to_resolved` |
| 3 | `seen_regression` | `scikit-learn__scikit-learn-14629` | resolved | unresolved | `resolved_to_unresolved` |
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

| 指标 | R0 | R1 | Δ |
| --- | ---: | ---: | ---: |
| LLM calls | 486 | 475 | -11 |
| Total tokens | 18800473 | 18915188 | +114,715 |
| Estimated cost (USD) | 2.28597 | 2.269783 | -0.016 |
| Tool calls | 673 | 656 | -17 |
| Search calls | 261 | 243 | -18 |
| grep_search | 232 | 195 | -37 |
| find_files | 0 | 30 | +30 |
| read_file | 217 | 218 | +1 |
| Validation calls | 100 | 108 | +8 |
| Failed tool calls | 45 | 55 | +10 |
| Failed validations | 35 | 30 | -5 |
| Mean tool calls before first edit | 15.95 | 15.6 | -0.350 |
| Mean search calls before first edit | 7.85 | 7.6 | -0.250 |
| Mean read calls before first edit | 7.25 | 7.15 | -0.100 |
| Repo-outline contexts | 0 | 362 | +362 |
| Validation head/tail | 0 | 14 | +14 |

## Treatment 激活证据

- `grep_search`：Treatment 运行中调用 `195` 次；实现绑定到 `rg` 子进程。
- `find_files`：在 `475` 个 Context 中可见，实际调用 `30` 次。
- `repo_outline`：进入 `362` 个 Context 组装事件；R0 为 `0`。
- Validation head/tail：观察到 `14` 次真实截断输出的 head+tail 保留。
- `apply_patch` 按预注册协议 defer，没有进入 Treatment 变量。

## 工程判断

- 新能力被真实使用，且 `grep_search`、总搜索次数和首次编辑前搜索次数均下降；Validation head/tail 同时伴随 failed validations 下降。
- 但这些过程指标没有转化为 official correctness 提升：1 个 Case 获益、2 个 Case 回归，净结果为 -1。
- `repo_outline` 大量进入 Context，但 `read_file` 调用没有下降、总 token 略升；这只是后续拆分变量的线索，不构成本轮失败的单一因果解释。
- 因此不合入本轮 bundle。若继续优化，应在新协议中逐组件验证，而不是对本轮 Golden-20 做结果驱动重跑。

## 证据边界

- 20/20 两侧均要求 candidate diff、prediction.model_patch 与 official evaluator patch 字节一致。
- official outcome 只读取 run 级 safe aggregate；未读取 gold、逐测 tests_status、test_output 或 run_instance.log。
- Agent generation 均为一次 Pass@1；R0 部分 official evaluator 基础设施失败只对同一冻结 prediction 做 evaluator-only 重试，没有重新生成 Patch。
- Provider 内建 transport retry 按冻结配置最多 2 次；R0/R1 分别观察到 3/5 个 retried calls，均无 fallback。
- 本实验评估整个 Tool / ACI bundle，不能把变化单独归因于其中某一个组件。
- Golden-20 是固定开发集，不是 holdout，也不是 SWE-bench Verified 500 题榜单成绩。

## 核心代码

以下路径绑定到已拒绝的 Treatment commit；stable master 回滚后请在该 commit 中查看：

- `agent_forge/tools/grep.py`
- `agent_forge/tools/find_files.py`
- `agent_forge/context/repo_outline.py`
- `agent_forge/tools/output_window.py`
- `agent_forge/tools/python_validation.py`

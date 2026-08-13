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

- R2 将 `repo_outline` 完全移出 Context，并以 `find_files` 替代宽泛的 `list_files`；三个预注册
  组件都被真实调用，说明本轮不是“功能没有生效”。
- Official correctness 与 R0 同为 `14/20`：`sympy__sympy-20590` 从 unresolved 变为 resolved，
  `astropy__astropy-14182` 从 resolved 变为 unresolved。收益和回归一一抵消，不能声称任务解决率提升。
- 在 correctness 持平的同时，LLM 调用减少 `22.4%`、总 Token 减少 `33.6%`、Tool 调用减少
  `20.8%`、搜索调用减少 `20.7%`，首次编辑前 Tool 调用均值从 `15.95` 降至 `13.45`；这是一条
  明确的效率信号，但不是 correctness proxy。
- 预注册门禁裁决为 `reject`：本轮 Treatment 已回滚，不进入 stable Runtime。后续若继续，应只拆分
  单组件并在 Golden-20 上开发；方案成熟后再用未参与调参的 validation set 做一次最终确认。

## 技术交流口径

### 30 秒版本

我把 Tool / ACI 优化拆成固定开发集上的成对实验：固定 V4 Flash、Golden-20、预算和 official
evaluator，只收缩 repository Tool surface。R2 的正确性保持 `14/20`，同时 Token 降低 `33.6%`、
Tool 调用降低 `20.8%`，说明工具职责更清晰确实提升了执行效率；但因为有一题新增解决和一题回归，
没有通过 non-regression gate，所以我回滚了方案，没有把过程指标包装成能力提升。

### 2 分钟版本

1. R1 同时引入 `rg`、`find_files`、`repo_outline` 和 validation head/tail，过程指标改善但 official
   从 `14/20` 降到 `13/20`。Trace 显示 `repo_outline` 大量注入 Context，`find_files` 又与
   `list_files` 全程共存，变量之间存在职责重叠。
2. R2 没有换开发集，也没有看 gold；它只做三个可解释的收缩：保留 `rg-backed grep_search`，让
   `find_files` 在 SWE task-aware 路由中替代 `list_files`，移除 `repo_outline`，保留有界 validation
   输出。模型、20 个 Case、预算、Skill 和 official evaluator 均冻结。
3. 结果是 `14/20 → 14/20`，逐题为 13 个稳定解决、5 个稳定未解、1 个 gain、1 个 regression。
   同时 LLM/Token/Tool/Search 分别下降约 `22%/34%/21%/21%`，证明收缩 Tool surface 能减少无效
   探索，但这轮没有证明 correctness uplift。
4. 我的决策不是挑效率指标宣布成功，而是按预注册门禁回滚。这个实验展示的是从 Trace 提出假设、
   控制变量、核验激活、用 official evaluator 裁决、并让 non-regression gate 约束发布的完整闭环。

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

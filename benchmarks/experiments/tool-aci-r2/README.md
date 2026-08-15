# Tool / ACI R2：最小正交工具面

这是理解 R2 的唯一入口：先说明 R1 为什么失败，再列出 R2 实际代码变化、实验结果和回滚位置。

## 实验身份

- 日期：2026-08-13—14
- 模型：`opencode-go/deepseek-v4-flash`
- 样本：与 R0/R1 相同的 Golden-20 开发集
- R0 Baseline：`0ae0d9ae9444d723e319fc0c7eadec9b631d374c`
- R2 Treatment：`563a99fe72b078fa91bfb682d60d6d19f398a864`
- Frozen run source：`d7fc8110f9ec6bde7f7f794fb06f25986d279448`
- 回滚：`92f4de56a1391b58e8e249471ebd4ec04102f60b`
- 结果 tag：`tool-aci-golden-20-r2-result-20260814`

R2 设计时已经看过 R0/R1 outcome，因此 Golden-20 明确是开发集，不是 holdout。

## 从 R1 到 R2 的设计收缩

R1 同时加入 rg、find_files、repo_outline 和 validation head/tail，且 `find_files` 与
`list_files` 职责重叠。R2 删除 `repo_outline`，不改变 selected-file preview 权重，并把文件
发现入口收敛为一个更正交的 surface。

| 能力 | R0 / stable 行为 | R2 Treatment 的实际实现 | 代码位置（Treatment commit） |
| --- | --- | --- | --- |
| 内容搜索 | `Path.rglob` 后由 Python 逐文件读取匹配 | `grep_search` schema 不变；使用 `rg --fixed-strings`，固定 ignore、2 MiB 文件上限、结果条数和 30 秒超时 | `agent_forge/tools/grep.py`、`rg_support.py` |
| 文件发现 | 暴露目录树式 `list_files` | 新增 `find_files(pattern, path, max_results)`，由 `rg --files` + glob 返回有界、排序、去重结果 | `agent_forge/tools/find_files.py` |
| Tool 路由 | SWE task-aware 路由保留 `list_files` | SWE-bench 任务隐藏 `list_files`、只暴露 `find_files`；其他任务仍保留旧行为 | `agent_forge/tools/tool_router.py` |
| 长输出 | 只保留输出前缀，尾部错误可能丢失 | 共用 `render_output_window`；未截断时带完整性元数据，截断时约 2/3 head + 1/3 tail | `output_window.py`、`python_validation.py`、`run_command.py` |
| 自动 Context | 无 `repo_outline` | 仍然没有；不把 R1 的 AST outline 带回 R2 | Treatment diff 中无 outline 实现 |

`apply_patch`、LSP、tree-sitter/Code Graph、向量检索、Memory、Multi-Agent 和 Prompt 改造全部
defer，避免扩大变量。

## R2 代码现在在哪里

**当前 master 不是 R2 Treatment。** R2 未通过 non-regression gate 后已由 `92f4de5` 回滚，
所以当前 Tool 文件看不到 `find_files.py` 是正确现象，不是代码丢失。

精确代码保存在 Git commit `563a99f`。查看完整差异：

```bash
git show --stat 563a99fe72b078fa91bfb682d60d6d19f398a864
git diff 563a99fe72b078fa91bfb682d60d6d19f398a864^ \
  563a99fe72b078fa91bfb682d60d6d19f398a864 -- \
  agent_forge/runtime/wiring.py agent_forge/tools tests
```

如果需要在不影响当前 master 的情况下进入代码：

```bash
git worktree add ../NanoHarness-r2-code 563a99fe72b078fa91bfb682d60d6d19f398a864
```

Treatment 共修改 13 个文件，`423 insertions / 65 deletions`。正式运行使用后续冻结 commit
`d7fc811`；它在 Treatment 上追加实验协议，没有改变上述 Tool bundle。

## 先展示 Case，再解释迁移

运行 PyCharm 配置 `NanoHarness Benchmark - Inspect SWE-bench Case`，只需把环境变量
`SWE_BENCH_CASE_ID` 改成目标 `instance_id`。默认示例是 R2 regression
`astropy__astropy-14182`；它会直接展示 SWE-bench 数据集中的 `problem_statement`、仓库、
base commit、`FAIL_TO_PASS` 与 `PASS_TO_PASS`，不生成摘要、问题标签或实验结论。输出同时保存到
`.agent_forge/case-inspections/<instance_id>.md`。

等价命令：

```bash
.venv/bin/python scripts/inspect_swebench_case.py astropy__astropy-14182
```

该入口不运行 Agent，不调用模型，默认不显示 official test patch、gold patch 或 R1/R2 结果。
需要在看完原题后核对迁移时，再显式追加：

```bash
.venv/bin/python scripts/inspect_swebench_case.py astropy__astropy-14182 --experiment both
```

## 结果

| 指标 | R0 | R2 | 变化 |
| --- | ---: | ---: | ---: |
| Official resolved | 14/20 | 14/20 | 0 |
| LLM calls | 486 | 377 | -22.4% |
| Total tokens | 18,800,473 | 12,486,369 | -33.6% |
| Tool calls | 673 | 533 | -20.8% |
| Search calls | 261 | 207 | -20.7% |
| Read calls | 217 | 173 | -20.3% |
| Failed Tool | 45 | 39 | -6 |
| Failed validation | 35 | 28 | -7 |
| 首次 Edit 前平均 Tool | 15.95 | 13.45 | -2.50 |

Treatment 确实被使用：`find_files` 调用 45 次，`list_files` 调用 0 次；validation output window
观察 66 次，其中 9 次真实保留 head/tail；`repo_outline` 为 0。

逐题有 1 gain（`sympy__sympy-20590`）和 1 regression（`astropy__astropy-14182`）。
McNemar exact two-sided `p=1.0`。

## 决策

**Reject，并回滚。** R2 显著降低了模型调用、Token 和工具探索开销，但 official correctness
只有持平且出现一项 regression，没有通过预注册 non-regression gate。

面向外部的准确表述是：

> R2 的最小正交 Tool surface 在 official correctness 持平的情况下，显著降低了模型调用、
> Token 和工具探索开销；但因为存在一项 gain 和一项 regression，未通过 non-regression gate，
> 因此没有合入 stable Runtime。

不能说成“Tool 优化提升了解决率”，也不能把全部效率差异归因给单个组件。

## 证据定位

- [实验计划](plan.json)
- [R2 执行索引](r2.execution.json)
- [机器结果](result.json)
- [完整报告](report.md)
- [统一运行配置](../tool-aci-runner-v1.json)
- [运行入口](../../../scripts/run_tool_aci_golden_20.py)
- [确定性汇总器](../../../scripts/summarize_tool_aci_golden_20_r2.py)
- 本机原始证据：`.agent_forge/tool-aci-golden-20-r2/`

`r2.execution.json` 绑定每个 shard 的 `results.json`、`scorecard.json`、
`predictions.jsonl` 和 official aggregate SHA-256；`result.json` 由汇总器确定性生成；
本页是解释层。

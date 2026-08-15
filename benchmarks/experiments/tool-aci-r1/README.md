# Tool / ACI R1：宽工具面实验

## 实验身份

- 日期：2026-08-13
- 问题：固定模型、Golden-20、预算、Runtime 和 official evaluator，仅扩大 Tool / ACI 能力，
  是否提高 official resolved？
- 模型：`opencode-go/deepseek-v4-flash`
- Baseline：`0ae0d9ae9444d723e319fc0c7eadec9b631d374c`
- R1 Treatment：`296000864d6a2c1476c28b790f030b0ffc4cca5b`
- 回滚：`a79d71051e0b968df81e5cc0f0851d434e89f358`

## R1 改了什么

R1 一次加入四项能力：

1. `grep_search` 从 Python 全仓扫描切换到 `rg`；
2. 新增 `find_files`，由 `rg --files` 提供文件发现；
3. 把已排序 Python 文件的 AST `repo_outline` 注入 Context；
4. Validation 长输出改为显式 head/tail。

`apply_patch`、LSP、Code Graph、Memory、Multi-Agent 和 Prompt 改造均未进入本轮。

## 为什么 R1 不理想

R1 的问题不是“工具完全没有生效”，而是变量过宽、职责不够正交：

- `find_files` 与 `list_files` 同时暴露，模型仍需在两个文件发现入口间选择；
- `repo_outline` 在 Context 中出现 362 次，扩大了模型可见信息，但不能证明它帮助定位正确修改；
- 四项改动打包运行，出现 gain/regression 后无法定位是哪一项造成；
- 过程上 Search -18、Validation failure -5，但 Failed Tool +10，official resolved 从
  `14/20` 降到 `13/20`。

因此 R1 的价值是定位了下一轮设计原则：**保留确定性、低重叠的 Tool，移除未经证明的自动 Context
注入，并让一次实验只回答一个更窄的问题。**

## 结果与决策

| 指标 | R0 | R1 | 变化 |
| --- | ---: | ---: | ---: |
| Official resolved | 14/20 | 13/20 | -1 |
| LLM calls | 486 | 475 | -11 |
| Total tokens | 18,800,473 | 18,915,188 | +114,715 |
| Tool calls | 673 | 656 | -17 |
| Failed Tool | 45 | 55 | +10 |
| Failed validation | 35 | 30 | -5 |

逐题迁移：1 gain、2 regressions。决策为 **Reject，并回滚**。

## 证据定位

- [实验计划](plan.json)
- [R0 执行索引](r0.execution.json)
- [R1 执行索引](r1.execution.json)
- [机器结果](result.json)
- [完整报告](report.md)
- [Seen-10 样本](seen-set.json)
- [Fresh-10 样本](fresh-set.json)
- [统一运行配置](../tool-aci-runner-v1.json)
- [运行入口](../../../scripts/run_tool_aci_golden_20.py)
- [汇总器](../../../scripts/summarize_tool_aci_golden_20.py)

精确查看 R1 代码：

```bash
git show --stat 296000864d6a2c1476c28b790f030b0ffc4cca5b
git diff 296000864d6a2c1476c28b790f030b0ffc4cca5b^ \
  296000864d6a2c1476c28b790f030b0ffc4cca5b -- agent_forge tests
```

## 声明边界

Golden-20 是固定开发集；R1 是四项 Tool / ACI 的 bundle 测试。它不能用于声称完整
SWE-bench Verified 解决率，也不能把任何 gain 或 regression 单独归因给某个组件。


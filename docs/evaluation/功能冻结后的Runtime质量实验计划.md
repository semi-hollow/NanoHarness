# 功能冻结后的 Runtime 质量实验计划

> 状态：实验方案已确定，付费实验尚未开始。本文是唯一的实验设计入口。

## 1. 这项实验要证明什么

NanoHarness 的功能建设和质量优化分成两条证据线：

- Debug Lab 证明 HITL、Checkpoint、Sandbox、Ledger 和多 Agent 等能力确实存在；
- 本实验在功能冻结后，证明 Runtime 能根据真实失败证据持续提高正确性、收敛效率和稳定性。

本实验不与其他 Agent 项目刷榜，也不把新增 Feature 当成优化成果。面试时要展示的是：
**先建立可信基线，再定位主要瓶颈，每轮只改一个质量问题，用固定指标决定采纳或回滚，
最后用未参与调优的样本复核。**

## 2. 固定条件

生成基线 R0 后，以下条件不得改变：

| 条件 | 固定值 |
| --- | --- |
| 数据集 | SWE-bench Verified `test` |
| 模型 | `deepseek-v4-flash` |
| 推理 | Thinking enabled，`reasoning_effort=high` |
| Runtime 功能 | 同一 Tool、Skill、Memory、安全与执行环境能力集合 |
| 单轮 ToolCall 上限 | 4 |
| 最大 Turn | E0 校准后冻结，初始候选值为 32 |
| 评测 | 同一本地验证与 Official Evaluator |

如果 R0 只解决 `0-1/10` 或已经解决 `9-10/10`，说明模型或样本出现地板、天花板效应。
应在 R0 前调整一次；R0 生成后禁止换模型、换题或放宽资源预算。

## 3. Golden-10 为什么选这十题

Golden-10 是用于定位和调优的开发集，不是 SWE-bench 总体解决率样本。

| 覆盖目标 | Case | 为什么保留 |
| --- | --- | --- |
| 正确性回归 | `django__django-11451` | 历史稳定解决，防止优化破坏已有能力 |
| 正确性回归 | `matplotlib__matplotlib-13989` | 跨仓库正向锚点 |
| 已解决但效率差 | `scikit-learn__scikit-learn-14629` | 可观察 Tool 与 Token 噪音 |
| 长链不收敛 | `django__django-12209` | 历史在持续 ToolCall 中停止 |
| 长链不收敛 | `sphinx-doc__sphinx-10323` | 跨仓库检索与停止问题 |
| Context 导航 | `sympy__sympy-20590` | 大仓库、非局部根因和继承链 |
| 错误 Patch | `django__django-10097` | 有 Patch 但 Official Evaluator 拒绝 |
| 错误 Patch | `psf__requests-2317` | 非 Django 的验证边界 |
| Tool 失败恢复 | `matplotlib__matplotlib-22871` | 多次失败 ToolCall 后无 Patch |
| 策略敏感 | `django__django-13028` | 历史不同策略产生不同结论 |

这十题覆盖 6 个仓库、正向回归、收敛、Context、错误 Patch、Tool 恢复和策略敏感路径。
它们既不是全成功的简单题，也不是全失败的极难题，适合低成本复现主要瓶颈。

最终留出集使用现有预注册
[`swebench-verified-100-v1.json`](../../benchmarks/cohorts/swebench-verified-100-v1.json)
中 B 分片的前 10 题。优化完成前不查看题目内容和结果，避免针对 Golden-10 过拟合。

## 4. 实验顺序

```text
E0 测量校准（3 题，不计算解决率）
  -> R0 基线（Golden-10）
  -> 统计 Failure Pareto
  -> 只修影响最大的一个瓶颈
  -> Sentinel-4 预检
  -> 通过后补齐 Golden-10
  -> 采纳或回滚
  -> 通常再迭代一轮
  -> R0 与最佳版本在留出集复核
```

### E0：先证明测量可信

- `django__django-11451`：检查 solved 正向链路；
- `django__django-10097`：检查有 Patch 但 official failed 的负向链路；
- `matplotlib__matplotlib-22871`：检查 Tool 失败、恢复和停止链路。

E0 必须确认 Trace、Usage、Candidate Diff、Case Result 和 Official Evaluator 能通过同一
Run ID 对齐；环境故障不能被错误归类成 Runtime 能力失败。

### 每轮优化

1. 从 R0 或上一版本生成失败数量和影响排序，只选择第一瓶颈。
2. 写下问题、证据、假设和最小改动，不同时修改多个策略域。
3. 先运行 4 个哨兵 Case：`django__django-11451`、`django__django-13028`、
   `matplotlib__matplotlib-22871`、`sympy__sympy-20590`。
4. 哨兵出现正确性回归、证据缺失或安全问题，立即回滚；通过后再补剩余 6 题。
5. 通常最多两轮；硬上限三轮，避免为了数字继续增加复杂度。

## 5. 指标和采纳规则

第一指标是任务正确性：

- Official Resolved：`X/10`；
- Candidate Patch 生成数；
- Patch 进入 Official Evaluator 的数量；
- 本地通过但官方失败的数量。

只有 Official Resolved 不下降时，才比较效率和稳定性：

- `Turn / Resolved`、`Token / Resolved` 和 `Cost / Resolved`；
- 重复 ToolCall、失败 ToolCall、停止时仍有待执行 ToolCall；
- 首次定位、首次编辑和首次验证所在 Turn；
- 失败后恢复并最终解决的数量。

采纳规则：

1. Official Resolved 下降，拒绝。
2. Official Resolved 增加，检查无评测泄漏后采纳。
3. Official Resolved 持平，只有单位解题 Turn 或 Token 至少下降 15%，并且失败或重复
   ToolCall 至少下降 20%，才采纳。
4. 越权写入、审批绕过或重复状态变更操作必须为 0，否则直接拒绝。
5. 连续两轮未达到采纳门槛，停止优化。

每轮只维护一张结果表：

| 版本 | 第一瓶颈 | 最小改动 | Resolved | Patch / Eval | 失败 / 重复 ToolCall | Turn / Token per Resolved | 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R0 | 待实测 | 无 | 待实测 | 待实测 | 待实测 | 待实测 | 基线 |
| R1 | 来自 R0 Pareto | 待定 | 待实测 | 待实测 | 待实测 | 待实测 | 采纳或回滚 |
| R2 | 来自 R1 Pareto | 待定 | 待实测 | 待实测 | 待实测 | 待实测 | 采纳或回滚 |

## 6. 数量与费用

- E0：3 次；
- R0：10 次；
- 每个候选版本：先 4 次预检，通过后补到 10 次；
- 最终留出复核：R0 和最佳版本各 10 次。

使用 `deepseek-v4-flash` 时，Golden-10 一轮预计约 1.7-3.5 元；完整执行 R0、两轮优化
和留出复核通常约 10-20 元。设置 25 元总止损，任一 Golden-10 单轮超过 4 元先暂停排查。

10 题足够做低成本迭代和失败诊断，但不能代表 SWE-bench Verified 500 题总体成绩。
对外只报告 `X/10`、失败分布和版本变化，不写成排行榜百分比。

## 7. 面试讲述模板

> 功能集合完成后，我没有继续堆 Feature，而是冻结模型、任务、工具、Skill 和资源预算，
> 用覆盖六个仓库及五类典型路径的 Golden-10 建立 R0。然后从 Trace 和 Failure Taxonomy
> 统计失败 Pareto，每轮只解决影响最大的一个质量瓶颈。候选版本先过 4 题哨兵预检，
> 再补齐 10 题；正确性下降就回滚，正确性持平时必须显著改善单位解题成本才采纳。
> 最后用未参与调优的 10 题留出集复核。经过 N 轮，Resolved 从 X/10 变为 Y/10，
> Turn/Token per Resolved 从 A/B 变为 C/D。这个结果证明的是固定条件下的 Runtime
> 质量演进，不是把小样本包装成 SWE-bench 排行榜成绩。

这套讲法体现四点：指标有优先级、实验条件固定、改动有采纳门槛、结论有证据边界。

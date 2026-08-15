# 正式 Runtime Quality Golden-10

## 实验身份

- 日期：2026-08-11
- 问题：冻结 SWE-bench Case、模型、Tool、预算和 official evaluator 后，哪些最小 Runtime 调优能
  提高 `official resolved / planned`，同时不引入逐题语义或 correctness 回归？
- 模型：`deepseek/deepseek-v4-flash`，thinking enabled，reasoning high。
- 正式参考：Golden-10 R0；后续候选先经过 Sentinel，再决定是否扩跑。
- 运行配置：32 steps、49,152 prompt tokens、单题 $0.05 cap、单 Agent、Pass@1。

## 迭代结果

| 轮次 | 样本 | 单一主要变化 | Official 结果 | 决策 |
| --- | --- | --- | --- | --- |
| R0 | Golden-10 | 冻结参考协议 | 4 resolved / 3 unresolved / 3 empty | Reference |
| R1 | Sentinel-5 | 70% cost-aware convergence 临时控制消息 | 4 resolved / 1 unresolved | SymPy 退化为 scratch-only unresolved；拒绝 |
| R2 | Sentinel-4 | Skill v3.1 source-first / scratch exclusion | 3 resolved / 1 unresolved | SymPy 仍为 scratch-only unresolved；拒绝 |
| R3 | Sentinel-4 | SWE-bench task-aware 工具面移除 `create_file` | 2 resolved / 2 empty | 机制 55/55 命中，但 Sphinx 回归且 SymPy 仍无 Patch；拒绝 |

不同轮次的 planned 分母不同，上表不能按百分比横向排名；Sentinel 的用途是快速否决，不是替代
Golden-10 总体口径。

## 决策

**三个候选全部拒绝并回滚，不运行 R4。** 回滚提交：
`816560a5106015e585b3db7c8cbbd83046f35457`。测量完整性修复保留，候选 Runtime 改动不进入
stable default。

## 最重要的工程结论

- R1 证明一个候选即使增加部分 resolved，也会因语义回归被否决。
- R2 证明 Prompt/Skill 约束不一定改变底层行为。
- R3 证明 Trace 中 55/55 机制命中仍不能替代 official correctness。
- 因此发布门禁必须同时包含 mechanism activation、目标正向变化和 resolved anchor non-regression。

## 声明边界

- R0 `4/10` 是固定开发样本参考，不是完整 SWE-bench Verified 解决率。
- 每个正式版本每题一次，不声称统计显著性或确定性输出。
- `evaluated-patch acceptance`、Patch 数和 local validation 都不能替代 planned 分母。
- 该实验使用 DeepSeek provider；不能与后来的 OpenCode Go / GLM-5.2 实验直接作严格 A/B。

## 证据定位

- 历史实验总记录：`3a96f14403419a85d1662d501291c75c799237c6:benchmarks/runtime-quality/golden-10-v1.json`
- 文件 SHA-256：`17942805a56dc13d12566864d2ceb81ddb0342b598794ef9bf02c04fbf9918a9`
- 对应字段：`reference_metrics`、`iterations`、`rollback`

```bash
git show 3a96f14:benchmarks/runtime-quality/golden-10-v1.json \
  | jq '{reference_metrics, iterations, rollback}'
```

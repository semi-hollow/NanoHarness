# Runtime Preset 50×2 成对实验

## 实验身份

- 日期：2026-08-08
- 问题：在同一个 AgentLoop、模型、任务、预算和安全边界下，启用 task-aware Tool Routing 与内置
  Skill 的 Governed Runtime preset，是否优于暴露完整工具集且关闭 Skill 的 Minimal Control？
- 数据：SWE-bench Verified 固定 SHA-256 排名样本，50 个 Case。
- 模型：`deepseek/deepseek-v4-flash`，thinking enabled，reasoning effort max。
- 运行：每个 Case 每个 preset 一次，共 100 个计划槽位，100/100 completed；按 Case 交替 variant
  顺序，降低 provider 时间偏差。
- Source：`34cbe9113bccd62438065cc7ce99af3c653aad4e`。

这是 preset 级多因素比较，不是 Tool Routing 或 Skill 的单变量因果实验。

## 固定配置

- `max_steps=16`
- `max_prompt_tokens=32768`
- `max_context_chars=12000`
- `cost_budget_usd=0.05`
- 单 Agent、Pass@1、官方 evaluator、每个 infrastructure slot 最多一次有界重试

## 结果

| 指标 | Minimal Control | Governed Runtime |
| --- | ---: | ---: |
| Planned / completed | 50 / 50 | 50 / 50 |
| Candidate Patch | 32/50 | 27/50 |
| Official resolved / planned | **20/50 (40%)** | **14/50 (28%)** |
| Official evaluated | 32/50 | 27/50 |
| Failed tool calls | 36/993 (3.63%) | 14/973 (1.44%) |
| Total tokens | 8,308,931 | 8,078,883 |
| Execution estimated cost | $1.242115 | $1.124990 |
| Infrastructure failures | 0 | 1 |

在双方都有 official outcome 的 25 个配对 Case 中，Minimal Control 赢 4、Governed Runtime 赢 0、
其余 21 个持平。49 个可裁决配对中，Minimal Control 赢 6、Governed Runtime 赢 1、42 个持平；
另有 1 对因 infrastructure failure 排除。

## 决策

**拒绝当时的 Governed Runtime preset。** 它把 failed-tool rate 从 3.63% 降到 1.44%，Token 与成本
也略低，但 candidate reachability 和 official resolved 同时下降。过程效率不能覆盖正确性回归。

这轮也说明“开启治理功能”不是天然正向：Tool Routing 与 Skill 同时变化，只能评价整个 preset；
后续必须拆成单变量实验。

## 声明边界

- `20/50` 是固定、确定性选样上的一次历史观测，不是完整 500 题排行榜。
- 当时使用 16 steps 和单题 $0.05 cap，不代表当前质量优先配置的能力上限。
- 每题只运行一次，不估计模型运行间随机性。
- 该实验不能隔离底座模型贡献，也不能把差异归因于某一个治理组件。

## 证据定位

- 历史摘要：`1fd1ee3280a9a3b0fa2e3200a7fc3f12ab151f09:benchmarks/campaigns/swebench-verified-100-v1-a-flash-20260808/summary.json`
- 摘要 SHA-256：`d8b54bd85a1c5d7b9f733093047d076547ad2f4c861164861a5e24d6cdbc0e8b`
- 历史完整报告：`1fd1ee3280a9a3b0fa2e3200a7fc3f12ab151f09:benchmarks/campaigns/swebench-verified-100-v1-a-flash-20260808/README.md`
- 恢复命令：

```bash
git show 1fd1ee3:benchmarks/campaigns/swebench-verified-100-v1-a-flash-20260808/summary.json
```

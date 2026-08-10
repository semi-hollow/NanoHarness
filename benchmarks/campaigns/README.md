# Public Benchmark Campaigns

此目录只接收由 `forge bench campaign --publish` 生成的脱敏证据包。结果必须绑定固定 Case、
Runtime 配置、模型、预算和源码版本；这里的样本结果不冒充 SWE-bench 官方排行榜。

公开 bundle 包含：

- 固定 case、variant、重复次数和 source revision。
- 每个运行槽位的状态与 scorecard SHA-256。
- candidate、local、official 三层分母。
- token、cost、latency、tool failure 和 failure taxonomy 聚合。
- 每个完成槽位的脱敏 `scorecard.json` 与 `result.json`。

公开 bundle 不包含 API key、本机绝对路径、raw prompt、trace 内容、模型最终回答或
candidate patch 正文。

生成 candidate patch 或本地 Reviewer `PASS` 都不会被写成 solved。样本解决率使用全部预注册
Case 作为分母；“已评测补丁接受率”只描述进入 official evaluator 的候选，不能替代样本解决率。

## 当前固定样本结果

[`swebench-verified-100-v1-a-flash-20260808`](swebench-verified-100-v1-a-flash-20260808/README.md)
冻结了 Verified 100 题集合的 A 分片，使用 `deepseek-v4-flash` 跑 50 Case × 2 Runtime preset：

| 配置 | Official resolved / 50 | Candidate patch | 失败工具调用率 | Token | 实际执行成本 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Minimal Control | 20/50（40.0%） | 32/50 | 36/993（3.63%） | 8,308,931 | $1.242115 |
| Governed Runtime | 14/50（28.0%） | 27/50 | 14/973（1.44%） | 8,078,883 | $1.124990 |

- 100 个计划槽位全部结束；3 个基础设施异常各重试一次，1 个 provider error 仍未恢复。
- 原始执行绑定 clean revision `34cbe91`；后续统计审计没有改写单 Case scorecard。
- 排除该基础设施 pair 后，49 组配对为 Minimal 6 胜、Governed 1 胜、42 平。
- 治理配置降低工具失败率和 Token，但牺牲 patch reachability 与当前样本 correctness，因此决策是
  `reject` 整体 preset。当时计划在 B 分片拆分 Routing 和 Skills；该计划未执行，通用 Ablation
  入口后来也因偏离当前评测主线而删除。
- 包中 `manifest.json` 记录完整 50 题、源码 revision、配置 digest、尝试次数和每个 scorecard hash；
  后续扩到 100 题时只运行 B 分片，不重复 A 分片。

需要复核时，打开 Workbench 的“评测档案”；它只读回放已保存产物。未来扩容使用固定 cohort 的
B 分片，公开结果仍必须来自 clean revision。

```bash
forge bench campaign \
  --cohort-manifest benchmarks/cohorts/swebench-verified-100-v1.json \
  --cohort-shard b --repetitions 1 \
  --provider deepseek --model deepseek-v4-flash \
  --evaluate --publish
```

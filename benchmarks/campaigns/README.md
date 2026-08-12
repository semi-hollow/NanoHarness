# 公开 Benchmark Campaign 规则

此目录只接收由 `forge bench campaign --publish` 生成的脱敏 Evidence bundle。结果必须绑定固定 Case、
Runtime 配置、模型、资源预算和源码版本；样本结果不冒充 SWE-bench 官方排行榜。

当前质量主线不从此目录中挑选旧 bundle，而是由
[Canonical Showcase](../showcase/canonical-showcase-v1.json) 统一指向：

1. 先在已见 Golden-10 上按预注册规则选出 `showcase-quality-v1`。
2. 再对预封存的确定性 Canonical-50 执行一次 Pass@1。
3. 只有完整计划分母和 per-case official verdict 都齐全后，才发布“确定性 50 题样本上 `X/50`”。

当前两阶段均尚未完成，因此这里不发布新分数。早期低预算、50×2 preset 和被拒绝 Treatment
不再承担当前质量结论；它们的 Git 恢复点见[评测历史归档](../archive/README.md)。

## 公开 bundle 必须包含

- 固定 Case、variant、重复次数和 source revision。
- 每个运行槽位的状态与 scorecard SHA-256。
- candidate、local、official 三层分母。
- Token、成本、延迟、工具失败和 failure taxonomy 聚合。
- 每个完成槽位的脱敏 `scorecard.json` 与 `result.json`。

公开 bundle 不包含 API key、本机绝对路径、raw prompt、Trace 正文、模型最终回答或 candidate
Patch 正文。生成 candidate Patch 或本地 Reviewer `PASS` 都不会被写成 solved。

样本解决率始终使用全部预注册 Case 作为分母；“已评测补丁接受率”只描述进入 official evaluator
的候选，不能替代样本解决率。

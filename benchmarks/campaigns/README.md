# Public Benchmark Campaigns

此目录只接收由 `forge bench campaign --publish` 生成的脱敏证据包。

公开 bundle 包含：

- 固定 case、variant、重复次数和 source revision。
- 每个运行槽位的状态与 scorecard SHA-256。
- candidate、local、official 三层分母。
- token、cost、latency、tool failure 和 failure taxonomy 聚合。
- 每个完成槽位的脱敏 `scorecard.json` 与 `result.json`。

公开 bundle 不包含 API key、本机绝对路径、raw prompt、trace 内容、模型最终回答或
candidate patch 正文。

当前没有提交可形成 official correctness claim 的完整 campaign。生成 candidate patch
或本地 Reviewer `PASS` 都不会在这里被写成 solved；只有两侧都存在 official per-case
resolved/unresolved outcome 的 pair，才进入 correctness comparison。

```bash
forge bench campaign \
  --regression-set smoke-5 \
  --repetitions 3 \
  --evaluate \
  --publish
```

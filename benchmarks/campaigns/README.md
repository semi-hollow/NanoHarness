# Public Benchmark Campaigns

此目录只接收由 `forge bench campaign --publish` 生成的脱敏证据包。当前固定集合是从
SWE-bench Verified 500 题中分层选择的五题；它用于 Runtime 机制比较，不代表总体解决率。

公开 bundle 包含：

- 固定 case、variant、重复次数和 source revision。
- 每个运行槽位的状态与 scorecard SHA-256。
- candidate、local、official 三层分母。
- token、cost、latency、tool failure 和 failure taxonomy 聚合。
- 每个完成槽位的脱敏 `scorecard.json` 与 `result.json`。

公开 bundle 不包含 API key、本机绝对路径、raw prompt、trace 内容、模型最终回答或
candidate patch 正文。

当前没有提交可形成总体 correctness claim 的完整 Smoke-5 repeated campaign。生成 candidate
patch 或本地 Reviewer `PASS` 都不会被写成 solved；只有两侧都存在 official per-case
resolved/unresolved outcome 的 pair，才进入 correctness comparison。

## 当前 Commissioning Evidence

- [`verified-commissioning-2-20260726`](verified-commissioning-2-20260726/README.md)：
  Astropy、Django 两题，两个 Runtime preset，共 4 个 official runs。
- 四次均为 `official_resolved`，两个 preset 在 correctness 上是 2 个 tie；这不能证明
  governed preset 更优。
- 该包来自一次中断 Smoke-5 的前四个完成槽位，只有一次 repetition 且 source 为 dirty；
  它用于核验 end-to-end evidence pipeline，不是预注册通过率或总体性能结果。

本地先通过 PyCharm 运行 `NanoHarness Benchmark Smoke-5` 完成 10-slot 初始闭环。该入口允许
dirty source 以便开发迭代，但会如实记录 source digest，且不会自动发布。公开结果必须在 clean
revision 上使用下方三次重复命令生成。

```bash
forge bench campaign \
  --regression-set smoke-5 \
  --repetitions 3 \
  --evaluate \
  --publish
```

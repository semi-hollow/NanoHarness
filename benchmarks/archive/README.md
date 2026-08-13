# 历史评测归档

低预算实验、被拒绝的 Runtime Treatment 和旧 50×2 preset campaign 只属于研究历史，
不再承担当前产品质量结论。统一的人类可读时间线、逐实验决策和恢复入口见
[实验总览](../experiments/README.md)；大型原始 bundle 仍从以下 Git 历史恢复：

- pre-Canonical repository state: `f44b102df0c9889dbc853bd2d01504fbb5cade29`
- old public 50×2 campaign introduction: `3b9cb02b207eb0387125bca896621dee34be264a`
- old Golden/Runtime-quality summary introduction: `1fd1ee3280a9a3b0fa2e3200a7fc3f12ab151f09`
- rejected Ledger candidate was reverted by: `042846a`

当前仓库保留一份新的失败关闭事故记录：

- [`quality-selection-v1-fail-closed.json`](quality-selection-v1-fail-closed.json)：
  Golden-10 质量选型 v1 计划启动 20 个 case，20/20 都留下 finalized root artifacts；
  在最后 6 个全量污染槽位之前的 14 个槽位中，第 12–14 槽已经出现 shared rate limit，
  最后 6 个槽则全部在首个模型调用处被限流。
  Summarizer 因此以 exit 2 失败关闭，没有 winner，也没有正确性重跑。前 14 个槽位、
  11 个无 rate-limit 的槽位和任何分片子集都不得被用来倒推 winner。

该 JSON 只使用 run 根目录的 `results.json`、`scorecard.json`、`predictions.jsonl`、
case `usage.json` 与官方 aggregate 的安全计数字段；没有读取或固化 per-test report、
log、`test_output.txt`、`tests_status.json`、sealed dataset 内容或 gold。它可在 Workbench
的“历史归档”中下钻，但不进入 Canonical headline，也不修改当前选择模型。

上方列出的旧 Git 历史产物只在评审者深挖实验纪律与回滚证据时通过私有 Defense Pack
引用；它们不再填充当前 Workbench。失败关闭事故只进入 Workbench 的显式历史入口；两类
证据都不进入 README 的质量分数。

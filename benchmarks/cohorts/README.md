# Frozen Benchmark Cohorts

此目录保存大样本评测的**预注册分母**，不保存运行结果。

`swebench-verified-100-v1.json` 从 SWE-bench Verified `test` split 的 500 题中，按
`SHA-256(seed + ":" + instance_id)` 排序后取前 100 题。选择过程不读取 gold patch、
test patch 或历史成功结果，因此不会为了提高分数挑题。

- `a`：前 50 题，本轮执行。
- `b`：后 50 题，后续扩容时执行。
- 两个分片互斥，合并后恰好覆盖 100 题；CLI 会在付费调用前校验该契约。

单次结果应写成“在固定 50 题样本上 resolved X/50”，不能写成官方排行榜分数。

# NanoHarness Benchmarks

本目录只提供三个入口：

- [showcase/](showcase/README.md)：当前质量口径与 Mini-50 正式测量入口；
- [experiments/](experiments/README.md)：当前 Tool / ACI R1、R2 实验；
- [archive/](archive/README.md)：不进入当前叙事的历史实验与废弃预注册资产。

## 一个实验的文件链

`plan.json` 冻结 Case、模型、预算和变量；`*.execution.json` 索引每个运行目录及机器文件 hash；
`result.json` 保存确定性聚合；`report.md` 保存逐 Case 与过程指标；`README.md` 解释问题、代码
变化和决策。

这里曾使用 `cohort` 表示“固定的一组 Case”，使用 `regression` 表示“检查改动是否让原本通过的
Case 退化”。当前目录不再用这两个词做一级文件夹，避免和机器学习模型、回归算法混淆。

完整 Trace、Usage、candidate Patch 与 official evaluator 输出属于本机运行证据，位于
`.agent_forge/`，不会提交包含本地路径或大体积日志的副本。


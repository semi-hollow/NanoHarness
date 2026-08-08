# Governed Runtime 反向退化与调优记录

这份记录只保留会改变 Agent 行为、实验结论或系统设计的迭代。界面样式、脚本拼写和一次性
环境问题不进入这里。

## 1. 问题不是“工具越少越好”

固定 A 分片包含 50 个 SWE-bench Verified Case。每个 Case 分别运行 Minimal Control 和
Governed Runtime；两组使用相同 AgentLoop、模型、任务、预算、安全边界和执行环境。

| A 分片结果 | Minimal Control | Governed Runtime |
| --- | ---: | ---: |
| Official resolved | 20/50 (40%) | 14/50 (28%) |
| 生成 candidate patch | 32/50 | 27/50 |
| Official 接受 / 已生成 patch | 20/32 (62.5%) | 14/27 (51.9%) |
| 失败工具调用 | 36/993 (3.63%) | 14/973 (1.44%) |
| 模型估算成本 | $1.242115 | $1.124990 |

Governed Runtime 的失败工具调用更少、成本更低，却少解决 6 题。结论不是治理无效，而是
“少报错”只衡量运行整洁度，不等于“更会解决任务”。Runtime 可能通过限制模型行动空间，
同时压低错误率和任务完成率。

## 2. 从 Aggregate 下钻到行为差异

Trace 聚合显示了三个可行动机制：

1. Governed Runtime 每题自动激活 3 张 Skill；`targeted_code_edit` 命中 50/50，
   `test_failure_triage` 38/50，`docs_update` 33/50。Skill 卡平均占约 2,581 字符，接近静态
   Context 的一半，而且通用流程和 SWE-bench task 自带规则重复。
2. Governed Runtime 暴露 `grep` 与 `grep_search` 两个执行契约相同的 schema。它比 Minimal
   多调用 40 次 `grep`，说明工具可见性并没有真正做到去歧义。
3. SWE-bench 路由静态隐藏 `run_command`。`python_validation` 能覆盖标准 pytest/unittest，
   但旧 Django、SymPy 等仓库经常需要项目特定入口或参数。Minimal 的 15 次受限命令调用中，
   包含 `django__django-15375`、`django__django-11239` 等最终解决 Case。

还有一个结果层信号：Governed Runtime 在发生编辑时更早开始修改，但未产生改动的 Case 更多
（23 vs 18），总写调用更少（43 vs 67）。这说明它不是单纯“搜索太久”，而是更早收敛、但在
更多任务上没有形成可评测候选；已生成 patch 的接受率也更低，因此还存在 patch 质量问题。

以上是相关性证据。A 分片同时改变 Tool Routing 和 Skill，不能把 12 个百分点全部归因于任一
单点。

## 3. 方案比较与 v2 不变量

考虑过三个方向：

- **直接关闭 Skill**：最容易恢复基线，但无法验证 Skill 设计是否真的有价值。
- **增加更多 Skill/Tool**：会继续扩大选择空间，与当前证据相反。
- **收敛为一个任务工作流，并保留受限回退**：既减少提示冲突，又不把异构仓库能力静态剪掉。

v2 选择第三种，固定以下不变量：

1. 自动模式只激活一个主 Skill；多 Skill 组合必须由调用方显式声明。
2. SWE-bench 使用单一 `swebench_repair`，重点管理搜索停滞、剩余 turn 和验证收口，不重复权限
   与 Tool schema。
3. task-aware 视图只暴露 `grep_search`，兼容的 `grep` 仍保留在 Registry，但不再制造同义选择。
4. `python_validation` 仍是首选；只开放经过 CommandPolicy 白名单的 `run_command` 作为测试入口
   回退。它不能执行任意 shell，也不能绕过路径、权限和审批链。
5. pytest exit code 5 表示“未收集到测试”，记录为 validation blocked，而不是工具执行失败。

## 4. 迭代和验收协议

| 轮次 | 数据角色 | 主动变化 | 结果 | 决策 |
| --- | --- | --- | --- | --- |
| v1 / A | 开发证据 | 通用 3-Skill + 原 task-aware 路由 | Governed 14/50，Minimal 20/50 | 拒绝发布“治理提升正确性”结论 |
| v2 / A 子集 | 开发验证 | 单一 Skill、搜索去重、受限验证回退 | 待运行 | 只用于排查实现是否按预期生效 |
| v2 / B | 未见验收 | 冻结 v2 后盲跑 50×2 | 待运行 | 只以 B 的预注册分母判断是否泛化 |

A 可以反复用于定位问题，但不能一边查看结果一边宣称泛化。B 在代码和配置冻结后只运行一次。
如果 v2 在 B 仍未超过 Minimal，必须保留负结果；下一轮可以把 B 变成开发证据，但验收必须换用
未见 C 分片。反复刷同一 holdout 直到获胜会把 benchmark 变成训练集。

## 5. 可复述的工程结论

这次最重要的经验不是“加了 Skill 后分数提高”，而是：

> Harness 的治理目标不是最大限度减少工具和错误，而是在不损害任务可达性的前提下，减少无效
> 行动。Tool Routing 应去掉歧义，但要保留受控回退；Skill 应是一个有边界的工作流，而不是多个
> 相似 prompt 片段的叠加。优化必须同时观察 resolved rate、patch reachability、patch acceptance、
> 工具失败率和成本，任何单指标都会误导。

当前边界：一次 50 题固定样本、每题一次运行只能形成项目级实验结果，不是 SWE-bench 官方榜单，
也不能估计随机稳定性。

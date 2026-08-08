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
| v2 / A 高差异子集 | 开发验证 | 单一 Skill、搜索去重、受限验证回退 | Governed 由 1/7 回升到 3/7；Minimal 为 6/7 | 方向有效，但不足以冻结并盲跑 B |
| v3 / A 高差异子集 | 开发验证 | 预算阶段、最终轮零工具、创建文件能力、canonical schema | 待运行 | 先验证行为机制，再决定是否冻结 |
| v3 / B | 未见验收 | 冻结候选后盲跑 50×2 | 待运行 | 只以 B 的预注册分母判断是否泛化 |

A 可以反复用于定位问题，但不能一边查看结果一边宣称泛化。B 在代码和配置冻结后只运行一次。
如果 v2 在 B 仍未超过 Minimal，必须保留负结果；下一轮可以把 B 变成开发证据，但验收必须换用
未见 C 分片。反复刷同一 holdout 直到获胜会把 benchmark 变成训练集。

## 5. v2 为什么只恢复到 3/7

这 7 题是从 A 分片中挑出的高差异开发样本，不能当作总体解决率。它们的作用是快速判断修复是否
击中了机制：v1 Governed 只解决 1 题，v2 解决 3 题，而同批 Minimal 解决 6 题。

Trace 与 official report 将剩余问题收敛为三类：

1. **没有进入编辑**：`django-15375` 为 12 次 grep、9 次 read、0 次 write；
   `django-16082` 为 11 次 grep、10 次 read、0 次 write。模型已经命中相关文件，却没有在预算内
   从“继续找”切换到“形成假设并修改”。
2. **补丁不完整**：`sympy-12489` 修改了构造入口，却遗漏乘法、幂和求逆等内部调用点；
   `django-11239` 把 SSL 参数放进错误的调用契约。减少工具失败并没有改善语义影响面判断。
3. **候选正确但运行协议未收口**：`django-16560` 和 `django-13028` 已被 official evaluator 判定
   resolved，Runtime 却在第 16 步因 pending ToolCall 标记 blocked。候选正确性与运行终止语义被
   混为一个结论。

另外，v2 的 5 个候选补丁都没有得到有效的本地行为验证反馈，official 结果却为 3 resolved、
2 failed。这说明通用 `pytest/unittest` 门面还不能替代异构仓库的原生验证入口。

## 6. v3 选择的最小修复

v3 不继续堆 Prompt，而把已观测问题变成 Runtime 不变量：

1. 最终回答轮统一为零工具；structured ToolCall 与文本 ToolCall 使用同一 fail-closed 处理，
   provider 编码差异不能改变停止语义。
2. 预算末段由 Runtime 注入短控制消息；最后一个工具轮关闭目录级漫游，但保留 read/grep/edit/
   validate，使模型在验证失败后仍能读取报错源码，而不是被迫盲改。
3. 新增 `create_file`，只允许创建不存在的 workspace 文件；保留审批和沙箱，并继续隐藏能覆盖已有
   内容的 `write_file`。这是补齐任务可达性，不是放宽权限。
4. Registry 只向模型暴露 canonical `grep_search` schema，历史 `grep` 名称仍可由 Gateway 执行；
   `mode=all` 不再等于“把同义别名也发给模型”。
5. Cohort 声明的 Hugging Face revision 真正传入数据加载器，实验身份不再只是 metadata。

尚未在 v3 内扩大的能力边界：验证结果还需要从二值 `success` 演进为 `PASSED / FAILED /
UNAVAILABLE`；异构仓库更适合由受信任 `project_validation profile` 生成 argv，而不是继续增加 Shell
自由度。这两项先作为下一轮证据驱动改进，不在当前候选中混入大重构。

## 7. 可复述的工程结论

这次最重要的经验不是“加了 Skill 后分数提高”，而是：

> Harness 的治理目标不是最大限度减少工具和错误，而是在不损害任务可达性的前提下，减少无效
> 行动。Tool Routing 应去掉歧义，但要保留受控回退；Skill 应是一个有边界的工作流，而不是多个
> 相似 prompt 片段的叠加。优化必须同时观察 resolved rate、patch reachability、patch acceptance、
> 工具失败率和成本，任何单指标都会误导。

当前边界：一次 50 题固定样本、每题一次运行只能形成项目级实验结果，不是 SWE-bench 官方榜单，
也不能估计随机稳定性。

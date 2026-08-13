# NanoHarness 实验总览

本目录记录 NanoHarness 已经实际执行过、并对工程决策产生影响的评测实验。每个实验使用一个独立
目录，固定回答六件事：为什么做、比较什么、哪些条件保持不变、观察到什么、最终采用还是回滚、
原始证据如何恢复。

这里是实验演进索引，不是当前产品能力排行榜。不同实验的模型、Case、预算、样本性质和分母可能
不同；除非实验本身是成对设计，否则不能横向比较百分比，也不能选择最有利的一行当作总体成绩。

## 一页结论

| 日期 | 实验 | 样本与模型 | 关键结果 | 决策 |
| --- | --- | --- | --- | --- |
| 2026-08-08 | [Runtime preset 50×2](01-runtime-preset-50x2/README.md) | 固定 50 Case；DeepSeek V4 Flash；两个 preset 各跑一次 | Minimal Control `20/50`；Governed Runtime `14/50` | 拒绝当时的多因素 Governed preset |
| 2026-08-10—11 | [Context budget 探索](02-context-budget-exploration/README.md) | Golden-10 / Sentinel-4；DeepSeek V4 Flash | Patch 形成率和 Token 有变化，但 official 裁决不完整且出现语义退化 | 整体降级为探索证据 |
| 2026-08-11 | [正式 Runtime Golden-10](03-runtime-quality-golden-10/README.md) | Golden-10 + Sentinel；DeepSeek V4 Flash | 参考 R0 `4/10`；三个候选均未通过 correctness gate | 全部拒绝并回滚 |
| 2026-08-12 | [Operation Ledger one-shot replay](04-operation-ledger-replay/README.md) | Target、Guard、Golden-10；OpenCode Go / GLM-5.2 | Target `0/2→2/2`、Guard `3/3`；Golden-10 `5/10→4/10` | Case 机制成立，全局方案拒绝并回滚 |
| 2026-08-12 | [质量模型选型 v1](05-quality-selection-v1/README.md) | Golden-10 × V4 Pro / GLM-5.2；20 个计划槽位 | 9/20 槽位受 shared rate limit 影响 | 失败关闭，无 winner |
| 2026-08-13 | [Tool / ACI Golden-20](06-tool-aci-golden-20/README.md) | 固定 Golden-20；OpenCode Go / V4 Flash | Tool-R0 `14/20`；Tool-R1 `13/20` | Treatment 拒绝并回滚 |
| 2026-08-13—14 | [Tool / ACI R2 最小工具面](07-tool-aci-r2-minimal-surface/README.md) | 同一 Golden-20；OpenCode Go / V4 Flash | R0 `14/20`；R2 `14/20`；Token -33.6%、Tool -20.8% | 效率改善但 correctness 无净增，拒绝并回滚 |

## 这些实验共同说明什么

1. **先分证据层。** Candidate Patch、本地验证、official evaluator 和完整 planned 分母是不同层级；
   不能用 Patch 数或已评 Patch 接受率替代 `official resolved / planned`。
2. **机制命中不等于任务成功。** Operation Ledger 与 Tool/ACI 两轮都观察到直接激活，但扩大到固定
   开发集后没有通过 correctness/non-regression gate。
3. **过程效率不能覆盖正确性回归。** 50×2 和 Tool/ACI 都出现了工具失败、搜索量或成本变好，但
   official resolved 下降，因此没有采纳。
4. **失败也必须形成决策。** 三类失败分别触发了回滚、停止同方向调参、provider readiness gate 和
   更严格的 artifact/分母审计，而不是删除不理想的结果。
5. **小样本只用于工程选择。** Golden-10/20 是开发或回归集合；历史固定 50 Case 也只是一次固定
   样本观测。它们都不冒充完整 SWE-bench Verified 500 题排行榜。

## 推荐阅读顺序

技术交流时无需逐项复述所有数字，可以按下面顺序展开：

1. NanoHarness 已把 repository task、隔离 worktree、AgentLoop、candidate Patch、Trace/Usage 和
   official evaluator 串成可复核链路。
2. 先用 [Runtime preset 50×2](01-runtime-preset-50x2/README.md) 说明 Harness 能够运行固定分母、
   成对对照和 official 统计，并且会拒绝“效率提升但正确率下降”的方案。
3. 再用 [Operation Ledger](04-operation-ledger-replay/README.md) 说明如何从 Trace 定位具体状态机缺口、
   设计最小修复和 activation gate，以及为什么 Case-level 成功仍不足以全局采纳。
4. 最后用 [Tool / ACI Golden-20](06-tool-aci-golden-20/README.md) 说明当前的单变量意识、过程指标、
   paired transition 和回滚纪律。
   [R2 收缩实验](07-tool-aci-r2-minimal-surface/README.md)继续展示如何根据 Trace 缩小变量、恢复 R1
   丢失的一题，同时因为 `1 gain / 1 regression` 拒绝把效率收益包装成 correctness uplift。
5. 如果需要讨论实验可靠性，再展开[质量模型选型 v1](05-quality-selection-v1/README.md) 的失败关闭
   事件，说明为什么不从受限流污染的局部结果中挑 winner。

一句话概括这条演进：**NanoHarness 不只会跑 Benchmark，也会冻结比较条件、区分机制与正确性、
保存失败证据，并让发布决策受 non-regression gate 约束。**

## 数字口径速查

| 数字 | 准确含义 | 不能说成什么 |
| --- | --- | --- |
| `20/50` | 2026-08-08 低预算 Minimal Control 在固定 50 Case 上的一次 official observation | 当前最佳配置、完整 500 题成绩或 Harness 独立增益 |
| `4/10` | 2026-08-11 正式 Golden-10 参考 R0 的 `official resolved / planned` | 具有窄区间的总体解决率 |
| `5/10→4/10` | Ledger Treatment 在同一 Golden-10 reference/expansion 中的变化 | Target Case `0/2→2/2` 所代表的总体提升 |
| `14/20→13/20` | Tool/ACI bundle 在固定开发集上的成对 A/B | SWE-bench Verified 总体能力下降 5 个百分点 |
| `14/20→14/20` | Tool/ACI R2 在相同开发集上 correctness 持平，同时 Token/Tool 分别下降 33.6%/20.8% | 已证明解决率提升或该效率差异必然由单个组件造成 |
| `invalid_no_winner` | 模型选型 v1 被 shared rate limit 污染后失败关闭 | 任一候选模型胜出 |

## 仅有计划、尚无结果的资产

以下文件已经冻结或提供运行入口，但没有完成可发布的实验结果，因此不建立“已完成实验”目录：

- [`quality-selection-protocol-v2.json`](../showcase/quality-selection-protocol-v2.json)：fresh Golden-10 v2
  模型选型协议；尚未产生 winner。
- [`swebench-verified-mini-50-v1.json`](../showcase/swebench-verified-mini-50-v1.json)：Mini-50 绝对能力
  测量清单；尚未发布 `X/50`。
- [`canonical-50-v1.json`](../showcase/canonical-50-v1.json)：Canonical-50 确认样本；尚未发布结果。

## 归档规则

- 每个实验目录的 `README.md` 是该实验的稳定人类可读记录。
- 当前仍在仓库中的机器证据使用相对链接；已从主动树移除的大型历史 bundle 使用
  `commit:path` 和 SHA-256 定位，避免复制大量制品或改写冻结文件。
- `.agent_forge/` 中的完整 Trace、Usage、candidate 和 official evaluator 产物是本机证据；Git 中的
  摘要、commit 和 artifact hash 是长期恢复入口。
- 新实验只有在 denominator、official outcome 和决策齐全后才加入“一页结论”；仅预注册的计划保留
  在上一节。

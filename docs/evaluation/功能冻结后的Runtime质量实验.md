# 功能冻结后的 Runtime 质量实验

> 这是 Runtime 质量实验的唯一中文主文档。机器可读摘要、Workbench 和面试讲稿应以本文及
> [`golden-10-v1.json`](../../benchmarks/runtime-quality/golden-10-v1.json) 为同一事实源。

## 0. 最终结论先行

正式 R0 在固定 Golden-10 上的主口径是：

- `4/10 planned` official resolved；
- `3/10` official unresolved；
- `3/10` empty/skipped；
- official decided 覆盖 `7/10`；
- 7 题生成候选 Patch，Provider Usage 为 2,954,621 Token，估算成本 `$0.397210`。

随后只做了三轮单变量 Sentinel 调优。R1、R2、R3 都触发预注册拒绝门槛，已全部回滚，
`accepted_iteration=null`，不再运行 R4：

| 版本 | Cohort | official resolved / planned | 其他官方状态 | 关键结果 | 决策 |
| --- | --- | ---: | --- | --- | --- |
| R0 | Golden-10 | `4/10` | 3 unresolved，3 empty/skipped | 正式参考基线 | reference |
| R1 | Sentinel-5 | `4/5` | 1 unresolved | 两题 empty→resolved；SymPy resolved→scratch-only unresolved | 拒绝 |
| R2 | Sentinel-4 | `3/4` | 1 unresolved | 三个 guard 保持；SymPy 仍 scratch-only unresolved | 拒绝 |
| R3 | Sentinel-4 | `2/4` | 2 empty/skipped | 工具机制 55/55 通过；Sphinx 回归为空 Patch | 拒绝 |

这些百分比的分母不同。Sentinel 用来做 go/no-go，不得与 Golden-10 百分比或总 Token 横向
比较。R3 的 evaluated-patch acceptance 虽是 `2/2`，主指标仍是 `2/4 planned`；没有 Patch
的 empty/skipped 也不等于 official unresolved。

因此，本实验不能声称“优化后 official resolved 提升”。它能诚实证明的是：建立了可审计的
正式基线，从 Failure Pareto 提出三个有因果关系的最小假设，用逐题语义和正确性门槛拒绝
负优化，并把候选 Runtime 全部回滚。

## 1. 旧 R0–R2 为什么降级为 Pre-R0

旧对话中的 R0、R1、R2 统一重命名为 `historical_exploration / Pre-R0 / P0-P2`。它们仍是
有价值的探索证据，但不再承担正式基线或“已采纳优化”的角色：

| 新名称 | 旧名称 | 范围 / 变量 | 过程结果 | 当前用途 |
| --- | --- | --- | --- | --- |
| P0 | 旧 R0 | Golden-10，64K prompt | 5/10 candidate Patch；3,041,338 Token；`$0.409786` | 发现预算停止和工具失败 |
| P1 | 旧 R1 | Sentinel-4，32K prompt | Token 下降，但 SymPy 退化为临时测试 Patch | 保留负优化经验 |
| P2 | 旧 R2 | Golden-10，48K prompt | 8/10 candidate Patch；2,641,315 Token；`$0.351386` | 证明 Context 会影响 Patch 形成与效率 |

旧实验最主要的三个不足是：

1. **官方裁决不完整。** P2 当时只有 1 题 confirmed resolved、2 题 confirmed unresolved、
   7 题未裁决。`8/10 candidate Patch` 不能转换成解决率。
2. **复现与测量协议没有完全冻结。** Dataset 文件身份、requested Case 完整性、官方环境错误、
   Patch SHA 对齐和状态分类仍在补齐；不同测量状态不能当作同一质量实验比较。
3. **范围和语义证据混在聚合指标里。** Golden-10 与 Sentinel 的总量不可横比；P1 更省 Token，
   却把 SymPy 从源码修复退化为临时测试文件，证明单看 Patch 率、Token 或失败调用会选错方向。

所以，P0-P2 只能支持这句结论：**Context 预算会影响 Patch 形成率和执行效率。** 它们不能
支持“解决率提升”，旧 P2 的 accepted 标签也已经撤回。

## 2. 正式 R0 协议

### 2.1 固定变量

| 维度 | 正式固定值 | 复现边界 |
| --- | --- | --- |
| Dataset | SWE-bench Verified `test`，revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a` | Agent 与 official evaluator 使用同一冻结 JSON；SHA256 `f3eecbcd…d5889b5` |
| Case | Golden-10 的 10 个显式 ID | requested ID 缺失必须 fail-fast，不能静默缩小分母 |
| Provider / 模型 | DeepSeek `deepseek-v4-flash`，Thinking enabled，reasoning high | Provider revision 不可获得，是可变 alias |
| 采样 | temperature 记录为 `0` | Thinking enabled 时 adapter 不发送 temperature，因此不声称确定性采样 |
| Agent | single Agent | 不把多 Agent 当实验变量 |
| Context | `max_context_chars=12000`，`max_prompt_tokens=49152`，reserved output `4096` | 各候选轮保持不变 |
| 预算 | `max_steps=32`，每轮最多 4 个 ToolCall，单题成本预算 `$0.05`，timeout `900s` | 不靠增加 max_steps 获得更多机会 |
| Tool / Skill | task-aware ToolRouter；R0/R1 Trace 激活 `swebench_repair`；Memory recall 0 | 候选轮除预注册单变量外保持工具和 Skill 身份 |
| 隔离 | 独立 worktree，network deny | 被测仓库不继承 NanoHarness pytest 配置 |
| Official evaluator | `swebench 4.1.0`，git `f7bbbb2`；每 shard 1 worker | 每题每版本 1 次；正确性失败 0 次重跑；基础设施最多重试 1 次 |
| 调度 | generation 最多 3 shards 并发 | Sharding 只缩短墙钟时间，不是实验变量 |

这个协议足以定义“可复跑、可审计”的 R0，但不保证 bit-for-bit 输出一致：模型 alias 可变，
Thinking 模式也没有传输 temperature。面试时应区分“输入、预算、评测链路可复现”和“随机
模型输出完全确定”这两件事。

### 2.2 Golden-10 与 Sentinel 选择

| Case | R0 中承担的角色 | 为什么保留 |
| --- | --- | --- |
| `django__django-11451` | correctness anchor | 低成本、稳定 official resolved，检测明显回归 |
| `matplotlib__matplotlib-13989` | 跨仓库正向锚点 | 验证简单源码修复路径 |
| `scikit-learn__scikit-learn-14629` | 工具恢复 | 历史触发连续失败熔断 |
| `django__django-12209` | 长链收敛目标 | R0 在成本停止时仍为空 Patch |
| `sphinx-doc__sphinx-10323` | 长链收敛目标 / 后续 guard | R0 为空；R1 首次转为 resolved |
| `sympy__sympy-20590` | 语义哨兵 | 能区分源码修复、source+scratch、scratch-only 和 empty |
| `django__django-10097` | 错误 Patch | 证明有源码 Patch 仍可 official unresolved |
| `psf__requests-2317` | 错误 Patch / 临时文件污染 | 同时暴露 coverage omission 和 disposable validation |
| `matplotlib__matplotlib-22871` | 预算敏感错误 Patch | 失败工具多，且 candidate 未被官方接受 |
| `django__django-13028` | correctness anchor | 长链预算路径也能 official resolved |

R1 使用 Sentinel-5：`11451 / 12209 / 13028 / Sphinx / SymPy`。R2、R3 聚焦四个已经能形成
清晰 guard/target 对照的 Case：`11451 / 13028 / Sphinx / SymPy`。这不是为了提高百分比，
而是以更低成本回答“锚点是否退化、目标失败是否转正”。

### 2.3 指标与状态定义

主指标始终是：

```text
official_resolved_rate = official_resolved / planned
```

覆盖指标是：

```text
official_decided = official_resolved + official_unresolved
official_decided_coverage = official_decided / planned
```

状态边界：

- `official_resolved`：官方 Case 报告明确 `resolved=true`；
- `official_unresolved`：官方正确性评测完成且 `resolved=false`；
- `official_empty_or_skipped`：没有 Patch 可评，官方没有进行正确性裁决；
- `official_infrastructure_error`：评测环境在正确性裁决前失败。

后两者都不能冒充 unresolved，也不能冒充 resolved。`evaluated-patch acceptance = resolved /
(resolved + unresolved)` 只描述已被评测的非空 Patch；它会排除 empty，因此只作辅助指标。
Candidate Patch、本地验证、Tool 机制命中、Step、Token、成本也全部是次级证据。

### 2.4 运行与停止门槛

- 每轮只改变一个主要因子，先跑 3–5 个预注册 Sentinel；
- correctness anchor 必须继续 official resolved；
- SymPy 不得从 product-source 修复退化为 scratch/debug/test-only 或 empty；
- 至少一个目标 Case official 正向转移，且无锚点或语义回归，才允许补跑 Golden-10 其余题；
- 只有 candidate Patch、Tool 命中或效率改善，不允许扩跑；
- 任一锚点退化或语义哨兵退化，立即 reject；
- 同方向连续两轮没有新 official / 代表性 Case 信号即停止；
- 总候选轮上限为三轮，不通过增加 `max_steps` 换取机会。

## 3. 正式 R0：结果、Pareto 与逐 Case 归因

### 3.1 R0 总览

| planned | official resolved | official unresolved | empty/skipped | infra error | official decided | Patch | Step entries / LLM | ToolCall | Token / 成本 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 4 | 3 | 3 | 0 | 7 | 7 | 201 / 167 | 245（18 failed） | 2,954,621 / `$0.397210` |

原始 macOS arm64 空 namespace 路径在 SymPy 上尝试获取已经删除的 upstream `1.7` 分支，
错误发生在 Patch 应用之前。为了不把环境问题混成模型质量，只冻结 R0、R1、R2 的 SymPy
Patch，并用同一官方发布镜像各重评一次：R0 resolved，R1/R2 unresolved。R0 因此从原始
`3 resolved + 1 infra` 更新为权威口径 `4 resolved + 0 infra`；原始错误产物仍保留。

### 3.2 Failure Pareto

这些分类不是互斥总和；前四项以 Case 为单位，最后一项是过程事件：

| 优先级 | 失败模式 | 数量 | 证据与含义 |
| ---: | --- | ---: | --- |
| 1 | 成本预算停止 | 7/10 | 2 个仍为空 Patch，5 个已有 Patch；其中 2 resolved、3 unresolved。停止原因本身不等于正确性结果 |
| 2 | 有 Patch 但 official unresolved | 3/7 Patch | `django-10097`、Requests、`matplotlib-22871`；形成 Patch 不是核心瓶颈的充分解 |
| 3 | 无 Patch / official skipped | 3/10 | `django-12209`、Sphinx 因预算停止；scikit-learn 因连续失败 Tool 熔断 |
| 4 | 临时验证文件污染 | 2/7 Patch | R0 SymPy 与 Requests 都通过 `create_file` 混入 disposable validation 文件 |
| 5 | 失败 Tool / Validation | 18/245、9 次 | 检索、参数和验证失败消耗后续轮次，是次级过程瓶颈 |

这组 Pareto 把“成本停止”进一步拆成三种不同结局：已经正确、已经错误、尚未形成 Patch。
因此不能简单把预算加大，也不能把所有 `cost_budget_exceeded` 当作同一个 Failure Taxonomy。

### 3.3 R0 逐 Case

| Case | 权威 official 状态 | Patch 语义 | 停止 | 主要归因 |
| --- | --- | --- | --- | --- |
| `django__django-11451` | resolved | product source | final answer | 低成本正确锚点 |
| `matplotlib__matplotlib-13989` | resolved | product source | final answer | 跨仓库正确锚点 |
| `scikit-learn__scikit-learn-14629` | empty/skipped | empty | failed-tool circuit breaker | 连续 3 次失败工具，需恢复策略而非加预算 |
| `django__django-12209` | empty/skipped | empty | cost | 长链读取未收敛到 edit |
| `sphinx-doc__sphinx-10323` | empty/skipped | empty | cost | 最适合测试成本后段收敛提示 |
| `sympy__sympy-20590` | resolved | source + disposable test | cost | 官方正确，但候选语义不干净；后续必须设语义哨兵 |
| `django__django-10097` | unresolved | product source | cost | 错误 Patch，不是无 Patch 问题 |
| `psf__requests-2317` | unresolved | source + disposable validation | cost | 错误 Patch 叠加临时文件污染 |
| `matplotlib__matplotlib-22871` | unresolved | product source | cost | 5 次失败 Tool，候选仍错误 |
| `django__django-13028` | resolved | product source | cost | 说明预算停止不等于未解决 |

R0 原 shard-c 因 Case coverage 问题漏掉 Requests。在未知结果时只补齐该 planned Case 一次，
没有重复其他题；后来新增“requested ID 缺失即 fail-fast”的 evaluator 护栏，并在所有候选
Runtime 回滚后保留。这是测量完整性修复，不是 Agent 质量收益。

## 4. 三轮单变量调优

### 4.1 汇总

| 版本 | 单一主变量 | official 状态 | Step entries / LLM | ToolCall | Token / 成本 | Gate |
| --- | --- | --- | ---: | ---: | ---: | --- |
| R1 | 70% cost-aware convergence 临时控制消息 | 4 resolved，1 unresolved / 5 planned | 88 / 75 | 108（10 failed） | 1,617,372 / `$0.213731` | reject |
| R2 | SWE-bench Skill source-first / scratch 隔离 | 3 resolved，1 unresolved / 4 planned | **79 / 69** | 103（9 failed） | 1,151,911 / `$0.152209` | reject |
| R3 | task-aware SWE-bench 工具面移除 `create_file` | 2 resolved，2 empty/skipped / 4 planned | 61 / 55 | 78（7 failed） | 1,093,029 / `$0.148218` | reject |

R2 的 79 是四份 `usage.summary.steps` 之和；69 是 `llm_calls`。旧 protocol 中把 69 写作
steps 是测量标签错误，本文件和机器摘要统一使用 `runtime_step_entries=79`。

### 4.2 R1：70% 成本后收敛提示

**针对的失败：** R0 有 7 个成本停止，部分 Trace 在花掉 70% 预算后仍宽泛 read/search，
通常只剩 2–4 个模型轮。

**最小改动：** 在下一次模型调用前，如果累计 Provider 估算成本 / 单题预算 `>=0.70`，只对
当前轮临时追加控制消息：停止宽泛探索；证据足够时形成最小 source fix；已有 source Patch
时不要新增测试或临时文件，除非任务明确要求测试基础设施；最后只做定向验证和 diff。消息
不持久化，预算、max_steps、Tool schema、Skill 和 Context 都不变。

**结果：**

- `django-12209`：R0 empty → R1 official resolved；
- Sphinx：R0 empty → R1 official resolved；Trace 中提示首次在 step 8、spent ratio `0.7482`
  激活，source edit 在同一步发生；
- `django-11451`、`django-13028` 两个锚点保持 resolved；
- SymPy：R0 的 source+scratch official resolved → R1 的 scratch-only official unresolved。

**判定：reject。** 两个正向转移是真实结果，但只有一次随机样本，不能把同一步相关性写成
强因果；更重要的是，预注册 semantic veto 明确优先于聚合 `4/5`。R1 没有扩 Golden-10。

实施前还审查过“成本越界后仍执行已付费响应中的最后一次合法 ToolCall”。历史五个成本停止
Case 的末次动作全部是 read/grep/list；执行后又禁止下一次模型调用，Observation 无人消费，
无法形成 Patch 的完整因果链。因此这个想法在写代码前就被拒绝，避免了只改善过程计数的改动。

### 4.3 R2：Skill source-first 与 scratch 隔离

**针对的失败：** R1 的软收敛提示能把 Sphinx 推到 source edit，却没有阻止 SymPy 只交付
临时测试文件。

**最小改动：** 只修改 `swebench_repair` Skill 到 v3.1：在源码假设成立后优先形成最小
product-source 改动；scratch/debug/test-only diff 不算候选修复；除非任务明确要求测试基础
设施，不创建 disposable validation 文件。R1 的 70% 提示、工具、预算和 Runtime 保持不变。

**结果：** 三个 guard（`11451`、`13028`、Sphinx）继续 official resolved；SymPy 仍只新增
`sympy/core/tests/test_tmp_20590.py`，没有 product source change，官方发布镜像重评 unresolved。

**判定：reject。** R2 与同 cohort 的 R1 都是 3 resolved + 1 unresolved，没有新的任务结果
信号；Skill 文案没有改变 SymPy 的行为。没有扩 Golden-10，也不继续强化同一种 prompt。

### 4.4 R3：existing-file 工具面

**针对的失败：** 正式 R0-R2 共观测到 4 次 `create_file`，全部是 disposable validation
artifact；R1/R2 的软文案约束未能关闭动作路径。固定 Golden-10 的 Gold Patch 审计显示 10 题
都只修改既有文件，所以设计了一个严格限于本 cohort 的工具面实验。

**最小改动：** 只在 task-aware SWE-bench work/closeout 的 ToolRouter union 之后移除
`create_file`；普通非 SWE repair 和 `mode=all` 仍保留。R2 Skill 字节、70% 提示、其他工具、
预算与官方评测都不变。

**机制结果：** 55 次模型 Context 中 `create_file` 可见 0 次、dropped 55 次、实际动作 0 次，
所以 Router 机制 `55/55 passed`。

**任务结果：**

- `django-11451`、`django-13028` 继续 resolved；
- Sphinx 从 R2 resolved → empty/skipped，连续工具失败熔断；
- SymPy 从 scratch-only unresolved → empty/skipped，成本停止时仍无 source Patch。

R3 的 evaluated-patch acceptance 是 `2/2`，但那只是两个非空 Patch 都通过；主指标是
`2/4 planned`。把 empty 从分母删除会制造“100%”错觉。

**判定：reject。** 工具机制准确命中不等于任务质量改善；correctness guard 已回归，语义
目标也没有 resolved。该策略还会让真正需要新增 source/config/fixture/test 文件的外部任务
失去必要能力，不能从 Golden-10 的 Gold Patch 先验推广为通用 SWE-bench 规则。

R3 第一次启动误用了 `--skills auto`，Trace 实际激活 `test_failure_triage@1.0.0`，不再是
单变量实验。该启动在不知道官方结果时被中止，全部排除于 gate 和有效指标；只有两题发布
完整 Usage，确认 Token 下界 567,400、成本下界 `$0.074694`，另有未发布的部分 Django
Usage，所以这不是总成本。无效样本中的 SymPy 虽然生成了正确方向的 product-source Patch，
但固定 Skill 身份已经漂移；无论结果好坏都必须排除，不能因为结果看起来正向就事后放宽协议。
纠正为显式 `--skills swebench_repair` 后才产生上表有效 R3。

## 5. 成本与时间

### 5.1 估算公式

```text
有效运行成本 = Σ usage.summary.estimated_cost_usd
粗略预算上界 = planned × $0.05 + 末次已计费响应 / Provider 波动余量
墙钟时间 = protocol completed_at - started_at
```

`Σ llm_latency_ms` 只解释模型等待时间；由于三个 shard 可并行，不能直接把它当墙钟时间。
Official evaluator 的 Docker 构建、镜像缓存和 Case 环境差异是主要时间不确定性。

正式 R0 前预估 Sentinel-5 为 `$0.17–0.20`，Golden-10 为 `$0.35–0.42`，保守成本上限
`$0.60`。结合实际运行，今后同协议的 Sentinel-4/5 可按 `$0.15–0.22`、15–30 分钟规划，
Golden-10 可按 `$0.35–0.42`、60–75 分钟规划：

| 版本 | 范围 | 有效成本 | Provider Token | 观测墙钟 | 累计 LLM latency |
| --- | --- | ---: | ---: | ---: | ---: |
| R0 | Golden-10 | `$0.397210` | 2,954,621 | 70.2 min | 20.3 min |
| R1 | Sentinel-5 | `$0.213731` | 1,617,372 | 25.6 min | 12.8 min |
| R2 | Sentinel-4 | `$0.152209` | 1,151,911 | 17.1 min | 7.1 min |
| R3 valid | Sentinel-4 | `$0.148218` | 1,093,029 | 15.8 min | 6.9 min |

R1 比原预估略高，说明单题 `$0.05` 是 Runtime 触发阈值，不是 Provider 账单的硬截断：模型
响应返回后才知道本轮成本，少数 Case 可到 `$0.054–0.055`。R3 无效启动的已确认下界
`$0.074694` 不并入有效 R3，但应纳入真实实验花费复盘；有效+无效已知成本下界为
`$0.222912`。

## 6. 哪些数据复用，哪些必须重跑

可以复用：

- Golden-10 的选择理由、dataset revision 和冻结 JSON；
- P0-P2 Trace、Usage 与 Failure Taxonomy，用于提出正式假设；
- Case、dataset、Patch SHA、evaluator 身份完全一致的 official report；
- R0-R2 冻结 SymPy Patch 在同一官方发布镜像上的一次对称重评；
- 所有失败实验的 Trace 和 gate 决策，作为负优化证据。

必须重跑或重新生成：

- 旧 P0-P2 不能补写成正式结果，所以正式 R0 的 10 个 Case 必须按冻结协议重新生成、归档；
- 每个 treatment 的 Sentinel 必须重新生成，因为模型行为、Patch 和 Context 都可能改变；
- Patch SHA 不同就必须重新官方裁决，不能继承相同 Case 的旧 resolved。

明确排除：

- R2 首次 CLI 参数错误在 Case 创建前失败、没有模型调用；
- R2 本地 sandbox DNS 失败没有到达 DeepSeek，估算器产生的 token/cost 不是 Provider Usage；
- R3 `--skills auto` 无效启动因 Skill 身份漂移被整体排除；
- empty Patch 不进入 official evaluated Patch 分母，但仍留在 planned 分母。

Coverage repair 只允许在“执行完整性错误独立于结果、且遗漏 Case 尚未运行”时补齐一次：R0
补 Requests，R3 补首次 corrected shard 因默认 `limit=1` 遗漏的 `django-13028`。二者都没有
重复已知结果 Case。现在的 fail-fast 护栏应让今后直接失败，而不是事后发现分母缩小。

## 7. 停止、回滚与最终取舍

三轮候选路线形成了完整因果链：

1. R1 尝试在成本后段促成收敛，得到两个正向转移，却造成 SymPy 语义和 official 回归；
2. R2 直接强化 source-first 语义契约，SymPy 仍 scratch-only，没有新信号；
3. R3 关闭 create_file 动作路径，机制完全命中，却让 Sphinx guard 回归、SymPy 变 empty。

这已经满足“同方向连续两轮无目标新信号”和“三轮候选上限”。继续 R4 会变成针对四题的反复
试 prompt / tool schema，过拟合风险和学习成本都高于面试证据增量。因此：

- 不扩 Golden-10；
- 不增加 max_steps；
- 不运行 R4；
- 回滚 R1-R3 treatment；
- 保留 requested Case fail-fast 等测量卫生改动。

候选提交 `d582224`、`d36a6b4`、`d23e2c0` 已由 rollback commit
`816560a5106015e585b3db7c8cbbd83046f35457` 撤回；测量卫生提交 `55056b8` 保留。

## 8. 面试讲述与证据边界

### 8.1 90 秒主线

> 功能冻结后，我先把旧的 Context 实验降级为探索性证据，因为它只有 1 个确认 resolved、
> 2 个 unresolved、7 个未裁决，不能把 8/10 candidate Patch 当解决率。我冻结了十个
> SWE-bench Verified Case、dataset SHA、DeepSeek 配置、工具、预算和 official evaluator，
> 建立正式 R0：4/10 planned official resolved，3 unresolved，3 empty，裁决覆盖 7/10。
> Failure Pareto 显示 7 次成本停止、3 个错误 Patch、3 个 empty，以及两个临时验证文件污染。
> 我随后只跑 Sentinel，连续验证三个单变量假设。70% 收敛提示让 Django 和 Sphinx 两题从
> empty 转为 resolved，但 SymPy 从正确源码 Patch 退化为 scratch-only unresolved，所以我
> 拒绝；source-first Skill 仍没修好 SymPy；最后移除 create_file 的机制检查 55/55 通过，
> 但 Sphinx 回归为空 Patch。我没有用过程指标掩盖正确性，三轮都按预注册门槛拒绝、回滚，
> 也停止 R4。这个项目最强的证据不是“刷到更高分”，而是我能建立基线、从 Trace 归因、做
> 最小实验、识别负优化，并守住 official resolved 的证据边界。

### 8.2 最强可说结论

可以说：

- 在固定 Golden-10、冻结 dataset 与 official evaluator 上建立了 `4/10 planned` 的正式 R0；
- 用 official 三态、Patch SHA 和对称基础设施重评把正确性与环境失败分离；
- R1 得到两个代表性 empty→resolved，但因 SymPy resolved→unresolved 触发语义 veto；
- R3 证明 ToolRouter 机制命中 `55/55` 仍可能让任务结果退化；
- 三轮候选均按预注册门槛拒绝并回滚，没有为了数字继续刷实验。

不能说：

- “NanoHarness 解决率从 40% 提升到 80%”；
- “R1 的 4/5 优于 R0 的 4/10”，因为 cohort 不同；
- “R3 通过率 100%”，因为 `2/2` 只是在排除两个 empty 后的 evaluated-patch acceptance；
- “empty 就是 unresolved”；
- “工具机制通过证明 Runtime 调优有效”；
- “Golden-10 可以代表 SWE-bench Verified 总体”；
- “模型输出可完全确定复现”；
- “这些数字已经足以写成简历上的解决率提升”。

### 8.3 这次实验真正学到的可迁移经验

1. 先冻结 planned 分母和状态分类，再谈解决率；
2. Patch 形成、局部验证和机制命中都必须服从 official correctness；
3. 聚合正向时仍要看逐题 transition，尤其是 source→scratch、resolved→empty；
4. 单变量能改善归因，但一次随机样本只能支持工程决策，不能支持强统计因果；
5. 协议错误要按是否看过结果区分 coverage repair 和结果导向重跑；
6. 负优化不是失败材料：清楚写出假设、改动、gate、拒绝和回滚，反而更能说明 Agent Infra
   工程判断；
7. 连续无信号时停止，比继续堆 prompt、工具和 max_steps 更有价值。

原始正式协议与大体积 Trace 保存在 `.agent_forge/runtime-quality/formal/`，不作为重复叙事
提交到更多文档；Workbench 只读取机器摘要展示同一组 R0–R3 数据。

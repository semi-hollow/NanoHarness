# 功能冻结后的 Runtime 质量实验

> 这是 Runtime 质量实验的唯一中文主文档。机器可读摘要、Workbench 和面试讲稿应以本文及
> [`golden-10-v1.json`](../../benchmarks/runtime-quality/golden-10-v1.json) 为同一事实源。

## 0. 两阶段结论先行

### Phase 2：Target/Guard 有窄正向证据，但 Golden Gate 拒绝默认采纳

更换 Provider、模型和预算后，项目没有把旧 DeepSeek R0-R3 当作严格 A/B 基线，而是以
`opencode-go / glm-5.2` 建立独立的 Phase-2 R0：固定 Golden-10 为 `5/10 planned`
official resolved、`5/10` empty/skipped。随后从两次已完成的 Pytest 8399 失败运行中，事后识别出
一个 Operation Ledger 的通用恢复缺口：模型先完成 product-source 修改，验证失败后把目标还原到
precondition 以证明失败是预存的，再尝试精确重放原修改；旧 Runtime 把这个同 Run、同操作的重放
误判为 `stale_operation_record`，最终候选 Patch 丢失源码修改。

Treatment 没有放宽所有重试，而是加入“restored-precondition one-shot replay”：仅当同一 Run 内该
操作已恰好执行一次、pre/post/current 指纹完整且 `current == pre != post` 时，允许再执行一次，并记录
`replay_authorized_restored_precondition`。已经处于 post-state 仍复用旧 Observation；跨 Run、未知漂移、
指纹缺失和同一 operation 的第三次执行继续 fail closed。

结果分层如下：

- 历史 Target 两次均 official unresolved；Treatment 两次 fresh 独立生成均 official resolved，
  且机制标记 `2/2` 命中、候选/Prediction/official Patch 对齐；
- 三个冻结 Guard（Django 11451、Matplotlib 13989、Sphinx 10323）均保持 official resolved，
  标记 `0/3`，没有观察到无关路径误触发；
- Target + Guard 合计 `5/5` resolved，86 次 LLM call、777,258 Token、估算成本 `$0.905107`；
- 两次 Target 的本地验证仍失败；Trace 中还原前后均出现同类失败，只能说明该失败相对本次改动
  是预存证据，不能把 local FAIL 改写成 local PASS。正确性结论来自各自 official evaluator。
- 唯一一次 Golden-10 扩跑为 `4 resolved / 5 unresolved / 1 empty`，相对 Phase-2 R0 的
  `5 resolved / 0 unresolved / 5 empty` 净退化 1 题；原五个 resolved 只保留 `3/5`，另有一个
  Provider transport timeout 使冻结协议要求的“0 unresolved infra”不成立。

这是一个 **post-hoc selected、Case-level** 的正向复现，不是随机样本上的总体 uplift。Target 的
`0/2 -> 2/2` 与 Guard 的 `3/3` 支持“修复了一个可观察的恢复状态转移，且在预注册 Guard 范围内
未观察到退化”；但 Gate 4 的 Golden 扩跑失败，所以本 Goal 尚未完成，Treatment 的通用/默认采纳
被拒绝并要求回滚。它不能证明所有 SWE-bench、所有仓库或所有验证失败后的重放都会改善。

### Phase 1：正式 R0-R3 全部拒绝并回滚

第一阶段正式 R0 在固定 Golden-10 上的主口径为 `4/10 planned` official resolved、3 unresolved、
3 empty/skipped、official decided `7/10`；7 题生成候选 Patch，Provider Usage 为 2,954,621 Token，
估算成本 `$0.397210`。随后三轮单变量 Sentinel 调优都触发预注册拒绝门槛，已全部回滚，
`accepted_iteration=null`，没有运行 Phase-1 R4：

| 版本 | Cohort | official resolved / planned | 其他官方状态 | 关键结果 | 决策 |
| --- | --- | ---: | --- | --- | --- |
| R0 | Golden-10 | `4/10` | 3 unresolved，3 empty/skipped | 正式参考基线 | reference |
| R1 | Sentinel-5 | `4/5` | 1 unresolved | 两题 empty→resolved；SymPy resolved→scratch-only unresolved | 拒绝 |
| R2 | Sentinel-4 | `3/4` | 1 unresolved | 三个 guard 保持；SymPy 仍 scratch-only unresolved | 拒绝 |
| R3 | Sentinel-4 | `2/4` | 2 empty/skipped | 工具机制 55/55 通过；Sphinx 回归为空 Patch | 拒绝 |

这些百分比的分母不同。Sentinel 用来做 go/no-go，不得与 Golden-10 百分比或总 Token 横向
比较。R3 的 evaluated-patch acceptance 虽是 `2/2`，主指标仍是 `2/4 planned`；没有 Patch
的 empty/skipped 也不等于 official unresolved。

因此，Phase 1 不能声称“优化后 official resolved 提升”。它能诚实证明的是：建立了可审计的
正式基线，从 Failure Pareto 提出三个有因果关系的最小假设，用逐题语义和正确性门槛拒绝负优化，
并把候选 Runtime 全部回滚。Phase 2 是一条独立证据链，不能覆盖或重标 Phase 1。

## 1. Phase 2：Operation Ledger restored-precondition replay

### 1.1 从 Trace 到最小状态转移

两次历史 Pytest 8399 运行呈现同一结构：`A -> B` 的 product-source edit 已执行；定向验证失败；
Agent 用精确逆操作 `B -> A` 验证失败仍存在；随后精确重提 `A -> B`。旧 Ledger 看到“相同 operation
已有 executed record、当前却不等于 post-state”，只能按未知漂移停止。这个默认规则对跨 Run 恢复
是合理的，但遗漏了同一 Run 内由已记录逆操作恢复 precondition 的可证明状态。

Treatment 只补上这一个状态转移：

```text
same run + prior execution count == 1
+ complete pre/post/current fingerprints
+ current == pre != post
  -> authorize one replay and increment execution count

current == post
  -> return recorded observation without execution

cross-run / unknown drift / malformed fingerprints / execution count > 1
  -> fail closed
```

生产代码增加 68 行，候选 Treatment commit 为
`485ba920df516f0e5c6e5eefd10d8d5d9325ed9a`。行为契约覆盖触发、不触发、跨 Run、缺失指纹、
同一 operation 的第三次执行和 post-state 去重；实施前 Operation Ledger 定向测试 `12/12`、受影响测试 `76/76`、
全量 pytest `407/407`，Ruff 与 diff check 通过。代码与测试不含 Case ID、仓库名或目标路径。

### 1.2 五道 Gate 如何落到这轮实验

| Gate | 本轮验收 | 结论边界 |
| --- | --- | --- |
| 1 协议有效 | Target/Guard 的 Provider/model/Skill/Tool/预算/evaluator 固定且 Patch SHA 对齐；Golden 九个非空链对齐、空 Patch 为零字节，但有 1 次 Provider transport timeout | Target/Guard 身份通过；Golden 的“0 unresolved infra”失败，所以 comparison protocol-invalid |
| 2 机制与因果 | 两次 Treatment Trace 都在同一路径看到 marker，随后发生第二次 executing/executed；Guard marker 为 0 | marker 证明动作机会被恢复，不单独证明 official resolved 的全部因果 |
| 3 官方正向 | 历史 `0/2 unresolved` -> Treatment fresh `2/2 resolved` | local validation 仍失败；只按 official outcome 记正确性 |
| 4 不过拟合 | 预检查 Guard `3/3 resolved`、marker `0/3`；唯一 Golden 扩跑却为 `4/10`，原 resolved 仅保留 `3/5`，并有 1 次 Provider infra | **失败**；Guard 小样本不能覆盖冻结 Golden 的回归，不能默认采纳 |
| 5 复现闭环 | 机器摘要、Trace、Usage、official aggregate、Workbench 和文档可以统一核对 | 前四 Gate 未全过，因此 Goal/Gate 5 不能标 complete；记录拒绝并回滚 |

### 1.3 Target 与 Guard 结果

| Cohort | Case / 次数 | 对照结果 | Treatment official | 机制证据 |
| --- | --- | --- | --- | --- |
| Target | `pytest-dev__pytest-8399`，2 次独立开始 | 历史 `0/2` unresolved | `2/2` resolved | marker `2/2`；每次只授权一次 restored-precondition replay |
| Guard | `django__django-11451` | Phase-2 R0 resolved | resolved | marker 0 |
| Guard | `matplotlib__matplotlib-13989` | Phase-2 R0 resolved | resolved | marker 0 |
| Guard | `sphinx-doc__sphinx-10323` | Phase-2 R0 resolved | resolved | marker 0 |

两次 Target 的最终 Patch 字节不同，但都保留了 `src/_pytest/unittest.py` 的产品源码修改并通过各自
official evaluator。这里的路径只用于事后核对 Patch 语义；Runtime 规则和合成测试没有读取它。
同样，官方 resolved 不会把失败的本地 validation 改成成功，文档和 Workbench 必须并列展示两者。

### 1.4 唯一一次 Golden-10 扩跑

Phase-2 R0 的固定参考是 `5/10 planned` resolved、0 unresolved、5 empty/skipped；五个 resolved
Case 为 Django 11451、Matplotlib 13989、Matplotlib 22871、Sphinx 10323 和 SymPy 20590。
扩跑前冻结 Treatment commit、十个 Case、三条 shard command、十个 official image digest 和以下规则：

- `>=6/10` 且原五个 resolved 全保留，才是 Golden-10 净提升；
- `5/10` 且原五个全保留，只能叫 Golden-10 non-regression；
- `<5/10`、任一原 resolved 回归或持续 infra 不完整，Treatment 拒绝；
- 每题只生成一次，不按 outcome 手工重跑；Provider timeout、empty 与 official unresolved 分开。

实际 official artifact 分栏为：

| planned | resolved | explicit unresolved | official empty/skipped | official evaluator error/incomplete |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 4 | 5 | 1 | 0 |

需要单独保留一个协议层基础设施状态：`django__django-12209` 的 generation 连续两次内置 transport
attempt 都发生 read timeout，以 `invalid_llm_response / provider_transport_error` 停止并产生空 Patch；
没有人工重跑。official evaluator 自身没有 error/incomplete，但冻结协议要求“0 unresolved infra”，
因此 Golden comparison 仍是 **incomplete / protocol-invalid**，不能把上表写成完整匹配 A/B。

逐题 transition：

| Case | Phase-2 R0 | Treatment Golden | 说明 |
| --- | --- | --- | --- |
| `django__django-11451` | resolved | resolved | 原锚点保留 |
| `matplotlib__matplotlib-13989` | resolved | unresolved | correctness regression |
| `matplotlib__matplotlib-22871` | resolved | resolved | 原锚点保留 |
| `sphinx-doc__sphinx-10323` | resolved | resolved | 原锚点保留 |
| `sympy__sympy-20590` | resolved | unresolved | correctness regression |
| `django__django-13028` | empty | resolved | 单次新正向，不能覆盖预注册 veto |
| `scikit-learn__scikit-learn-14629` | empty | unresolved | 形成可评 Patch，但未解决 |
| `django__django-12209` | empty | empty | Provider transport timeout，无重跑 |
| `django__django-10097` | empty | unresolved | 形成可评 Patch，但未解决 |
| `psf__requests-2317` | empty | unresolved | 形成可评 Patch，但未解决 |

原五个 resolved 只保留 `3/5`；`django-13028` 的 `empty -> resolved` 是单次新正向，却不能用随机
单次结果抵消两个预注册锚点回归。九个非空 Case 的 candidate、Prediction、official Patch SHA
全部精确对齐；唯一 empty 的 candidate/Prediction 都是零字节。十题 marker 均为 expected 0 /
observed 0，没有发现 Treatment 在 Golden cohort 误触发。Usage 为 240 runtime step、205 次 LLM、
2,490,765 Token、`$3.224585`、210 次 ToolCall（23 failed）和 10 次 failed validation。

冻结决策是 **reject / rollback required**，而不是 Golden non-regression：Provider infra 已使 comparison
不完整；即使忽略 infra，`4 < 5` 与原 resolved 回归两题也分别足以拒绝。Target/Guard 的窄 Case-level
证据继续保留，但不能把这个 Treatment 作为 Operation Ledger 的通用默认行为，也不能完成本 Goal。

协议审计还保留了一次污染事件：一名审计 Agent 在 Target/Guard 完成后、扩跑协议生成前误打开冻结
dataset，看到部分 gold/test 文本。该 Agent 随后被限制为机械身份、bucket 与 SHA 核验，未参与
Treatment、命令、模型输入或结果解释；独立 Golden 审计保持不看 gold 和逐题官方日志。协议、镜像、
命令 manifest 的 SHA256 分别为 `d1ae4da1034ac67b9575549fba093b273a839a9d7a416d7dbcbb968d5b7530eb`、
`0c214b6f30be81d1fa8b5e329f20bd72d98f4cb2a4ec787059273a537faef6a8`、
`4e48bd09cbd66458a8aae50de1d4119084a93fca3b3229d398c6ebf7ee15264d`。

### 1.5 可迁移范围

可迁移的是问题拆分和验收方法，不是这个候选的默认实现：恢复逻辑应显式区分 post-state 已存在、
结果未知漂移，以及同一 Run 内被逆操作恢复到 precondition；但仅凭 fingerprint 可证明状态，不足以
证明开放重放在更宽任务集上无回归。候选只适用于能提供稳定 operation identity 与完整 pre/post
fingerprint 的文件状态变更；Golden 拒绝后仍回到保守 fail-closed 默认。不适用于无法观测副作用的
外部命令、支付/消息等分布式副作用，也不构成 exactly-once；后者仍需业务幂等键、事务或人工处置。

## 2. Phase 1：旧 R0–R2 为什么降级为 Pre-R0

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

## 3. Phase 1：正式 R0 协议

### 3.1 固定变量

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

### 3.2 Golden-10 与 Sentinel 选择

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

### 3.3 指标与状态定义

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

### 3.4 运行与停止门槛

- 每轮只改变一个主要因子，先跑 3–5 个预注册 Sentinel；
- correctness anchor 必须继续 official resolved；
- SymPy 不得从 product-source 修复退化为 scratch/debug/test-only 或 empty；
- 至少一个目标 Case official 正向转移，且无锚点或语义回归，才允许补跑 Golden-10 其余题；
- 只有 candidate Patch、Tool 命中或效率改善，不允许扩跑；
- 任一锚点退化或语义哨兵退化，立即 reject；
- 同方向连续两轮没有新 official / 代表性 Case 信号即停止；
- 总候选轮上限为三轮，不通过增加 `max_steps` 换取机会。

## 4. Phase 1：正式 R0 结果、Pareto 与逐 Case 归因

### 4.1 R0 总览

| planned | official resolved | official unresolved | empty/skipped | infra error | official decided | Patch | Step entries / LLM | ToolCall | Token / 成本 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 4 | 3 | 3 | 0 | 7 | 7 | 201 / 167 | 245（18 failed） | 2,954,621 / `$0.397210` |

原始 macOS arm64 空 namespace 路径在 SymPy 上尝试获取已经删除的 upstream `1.7` 分支，
错误发生在 Patch 应用之前。为了不把环境问题混成模型质量，只冻结 R0、R1、R2 的 SymPy
Patch，并用同一官方发布镜像各重评一次：R0 resolved，R1/R2 unresolved。R0 因此从原始
`3 resolved + 1 infra` 更新为权威口径 `4 resolved + 0 infra`；原始错误产物仍保留。

### 4.2 Failure Pareto

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

### 4.3 R0 逐 Case

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

## 5. Phase 1：三轮单变量调优

### 5.1 汇总

| 版本 | 单一主变量 | official 状态 | Step entries / LLM | ToolCall | Token / 成本 | Gate |
| --- | --- | --- | ---: | ---: | ---: | --- |
| R1 | 70% cost-aware convergence 临时控制消息 | 4 resolved，1 unresolved / 5 planned | 88 / 75 | 108（10 failed） | 1,617,372 / `$0.213731` | reject |
| R2 | SWE-bench Skill source-first / scratch 隔离 | 3 resolved，1 unresolved / 4 planned | **79 / 69** | 103（9 failed） | 1,151,911 / `$0.152209` | reject |
| R3 | task-aware SWE-bench 工具面移除 `create_file` | 2 resolved，2 empty/skipped / 4 planned | 61 / 55 | 78（7 failed） | 1,093,029 / `$0.148218` | reject |

R2 的 79 是四份 `usage.summary.steps` 之和；69 是 `llm_calls`。旧 protocol 中把 69 写作
steps 是测量标签错误，本文件和机器摘要统一使用 `runtime_step_entries=79`。

### 5.2 R1：70% 成本后收敛提示

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

### 5.3 R2：Skill source-first 与 scratch 隔离

**针对的失败：** R1 的软收敛提示能把 Sphinx 推到 source edit，却没有阻止 SymPy 只交付
临时测试文件。

**最小改动：** 只修改 `swebench_repair` Skill 到 v3.1：在源码假设成立后优先形成最小
product-source 改动；scratch/debug/test-only diff 不算候选修复；除非任务明确要求测试基础
设施，不创建 disposable validation 文件。R1 的 70% 提示、工具、预算和 Runtime 保持不变。

**结果：** 三个 guard（`11451`、`13028`、Sphinx）继续 official resolved；SymPy 仍只新增
`sympy/core/tests/test_tmp_20590.py`，没有 product source change，官方发布镜像重评 unresolved。

**判定：reject。** R2 与同 cohort 的 R1 都是 3 resolved + 1 unresolved，没有新的任务结果
信号；Skill 文案没有改变 SymPy 的行为。没有扩 Golden-10，也不继续强化同一种 prompt。

### 5.4 R3：existing-file 工具面

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

## 6. Phase 1：成本与时间

### 6.1 估算公式

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

## 7. Phase 1：哪些数据复用，哪些必须重跑

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

## 8. Phase 1：停止、回滚与最终取舍

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

## 9. 两阶段面试讲述与证据边界

### 9.1 90 秒主线

> 功能冻结后，我做了两阶段 Runtime 实验。第一阶段冻结 DeepSeek、Golden-10、预算和 official
> evaluator，R0 是 4/10 planned resolved。三个 prompt/tool-surface 候选虽然有局部正向或
> 55/55 的机制命中，但都造成语义或 correctness 回归，所以按 gate 拒绝并回滚。
>
> Provider 和模型更换后，我重新建立 Phase-2 R0，不拿两阶段做严格 A/B。随后从两次 Pytest 8399
> 失败 Trace 中识别到一个 Operation Ledger 状态缺口：Agent 已做 source edit，验证失败后用逆操作
> 证明失败预存，再精确重放原修改；旧 Runtime 因为操作曾 executed 且当前不是 post-state，就把它
> 当未知漂移停止。我的改动只在同一 Run、此前恰好执行一次、完整 fingerprint 证明当前恢复到
> precondition 时允许一次重放；跨 Run、未知漂移和同一 operation 的第三次执行仍 fail closed。
>
> 历史目标是 0/2 official unresolved，Treatment 两次 fresh 生成是 2/2 official resolved，Trace
> 两次都命中 marker；三个冻结 Guard 也先通过 3/3。可是唯一 Golden-10 扩跑只有 4/10 resolved，
> 比独立 R0 少 1，原五个 resolved 还回归两题；另有一题 Provider read timeout 让 comparison 不完整。
> 所以我按同一预注册规则拒绝默认采纳并回滚，不让一个 post-hoc Target 的漂亮 2/2 覆盖更宽 cohort
> 的失败。本地 validation 也始终保留为 FAIL。最后能留下的只是“该 Case 上恢复了一个可复现动作机会”
> 的机制/Case 证据，不是 Goal 完成、总体解决率提升或可默认上线的 Runtime 优化。

### 9.2 最强可说结论

可以说：

- Phase 1 建立 `4/10 planned` 的正式 R0，R1-R3 全部按预注册门槛拒绝并回滚；
- Phase 2 使用独立 Provider/model 基线，不能与 Phase 1 直接做百分比 A/B；
- Treatment 是 68 行通用状态转移，不包含 Case、仓库或路径特判；
- 历史 Pytest 8399 `0/2 unresolved` -> Treatment fresh `2/2 resolved`，marker `2/2`；
- 三个冻结 Guard `3/3 resolved`、marker `0/3`，Guard 范围内未观察到退化；
- Golden official artifact 为 `4 resolved / 5 unresolved / 1 empty`，原 resolved 只保留 `3/5`；
- Golden 另有 1 次 Provider transport timeout，comparison protocol-invalid；即使忽略它也按两个独立
  correctness veto 拒绝 Treatment，要求回滚，Goal/Gate 4 未完成；
- local validation 仍失败，官方正确性与本地过程证据被如实分栏。

不能说：

- “NanoHarness 总体解决率从 0% 提升到 100%”或“Golden-10 代表 SWE-bench Verified”；
- “Phase 2 比 Phase 1 更好”，因为 Provider、模型、预算和样本角色不同；
- “marker 命中单独证明了 official resolved 的全部因果”；
- “本地测试通过”或“验证失败都可以安全重放”；
- “Operation Ledger 实现 exactly-once”或能安全重放不可观测的外部副作用；
- “2/2 证明随机稳定性”，因为目标是事后选择且每个配置样本有限；
- “Treatment 已 accepted”“Golden non-regression”或“本 Goal 已完成”。

### 9.3 可迁移的工程经验

1. 先冻结 planned 分母、状态分类和 Patch SHA，再谈正向转移；
2. Provider/model 改变必须新建 baseline，不能继承旧 cohort 的因果口径；
3. Trace 机制命中、candidate patch、local validation 与 official correctness 必须分层；
4. 恢复策略要区分 post-state 去重、restored precondition、未知漂移和跨 Run；
5. 目标可以来自失败复盘，但 post-hoc 选择必须显式降低 claim scope；
6. Guard 通过只说明所测范围未观察到退化，仍需一次冻结 Golden 扩跑检查组合行为；
7. Provider infra 必须与 evaluator error 分层；不重跑也意味着 comparison 可以保持不完整；
8. Target 正向、Guard 通过仍不能覆盖 Golden 回归，所有 Treatment 都服从同一 gate。

Phase-1 原始协议与大体积 Trace 保存在 `.agent_forge/runtime-quality/formal/`；Phase-2 Case Study
保存在 `.agent_forge/runtime-quality/phase2/case-studies/`。它们不作为重复叙事提交，Workbench
只读取机器摘要，在同一页面分阶段展示且不混合分母。

# NanoHarness 工程演进史

本文只回答三个问题：项目按什么顺序形成、每次调整解决了什么问题、哪些同名能力后来才
真正成熟。记录范围为 2026-04-29 至 2026-07-19，基准提交是 `199d194`。

## 证据规则

提交标题不能单独证明能力已经完成。本文同时核对代码 diff、当时的数据结构和当前
[能力真实性矩阵](CAPABILITY_REALITY_MATRIX.md)：

- **首次进入主链**：可运行实现第一次进入当前 `master` 历史。
- **补强**：同名概念已存在，但持久化、隔离、恢复或验证随后才成立。
- **当前形态**：以当前代码与测试为准。

复核命令：

```bash
git show --stat <commit>
git show <commit> -- <path>
git log --follow -- <path>
```

早期标题中的 `Complete`、`Finalize production` 或 `readiness` 只是开发节点，不构成
production 声明。

## 里程碑时间线

| 阶段 | 时间 | 解决的问题 | 代表提交 |
| --- | --- | --- | --- |
| 最小内核 | 04-29 至 05-06 | 闭合 context -> model -> tool -> observation 循环 | `f0fcc2f`、`1ecc501`、`41f3b93` |
| 可审计 Run | 05-18 至 05-26 | 为一次运行增加身份、diff、report、usage 和工程场景 | `13444eb`、`292670e`、`46122d2` |
| Runtime 控制面 | 06-04 至 06-18 | 显式治理工具、策略、任务状态、checkpoint 和执行环境 | `4839b80`、`993926e`、`6ab6b7a` |
| SWE-bench 聚焦 | 06-22 至 07-08 | 删除分散能力，以真实仓库评测作为统一验收主线 | `adbe5e7`、`717138d`、`d33a6d3` |
| 证据与恢复纠偏 | 07-10 至 07-12 | 修正 solved 语义，补 durable approval、ledger、official eval、HITL 和隔离 fanout | `5c3c83c` 至 `fb89801` |
| 架构治理 | 07-12 至 07-18 | 拆解过长 AgentLoop，建立 Capability 内部职责与依赖约束 | `01b2e5c`、`3542457`、`d6a7724`、`6a2e994` |
| 框架式接入与重复实验 | 07-19 | 增加稳定 Public API、配置装配、run control、事件流和 repeated campaign | `b6cbc8e`、`0961f00`、`199d194` |

## 七个关键转折

### 1. 先做最小 AgentLoop

2026-04-30 的 `1ecc501` 已有 AgentLoop、上下文选择、工具、安全和 trace。但当时：

- `Memory` 只是最多保留 5 条文本的内存列表。
- `approval.py` 只返回 `auto=True/False`，不会持久停机或恢复。
- multi-agent 只是固定顺序调用 Planner、Coding、Tester、Reviewer。
- 没有 durable checkpoint 或 session state repository。

这一阶段只证明运行机制闭合，不代表 Harness 成熟。

### 2. 让一次运行留下可审计产物

`13444eb` 引入文件系统 `RunSession`、artifact 指针、diff/report、diagnostics 和 TaskGraph；
`292670e` 补 context strategy、token budget 和 failure control；`46122d2` 加入 Webhook
工程场景与 usage report。

这里的 `RunSession` 只是**一次运行的持久元数据**，不是聊天会话，也没有 `active_task`
切换。旧类后来被删除，当前 `AgentRunSession` 是另一种职责。

### 3. Checkpoint 首次进入主链

`4839b80` 先引入 ToolRouter、clarification、planning 和 memory policy。2026-06-10 的
`993926e` 才是 durable task checkpoint 的明确节点：`TaskStateStore` 保存状态、当前 step、
最近工具、stop reason 和 resume hint，并能表达 `waiting_approval`、`blocked`、`failed`
等状态。

此时仍没有 approval fingerprint、operation ledger、human response 和 stale recovery。

### 4. 主动删除宽而散的能力

`adbe5e7` 删除约一万行旧 workflow、通用 agent、静态 artifact 和分散 eval case，改用
SWE-bench-shaped runner、统一 CLI、benchmark report 与 wiring。项目主问题由“还能增加
什么功能”收窄为：

```text
同一个 repository task 是否产生 patch？
验证证据是什么？
失败属于模型、上下文、工具、环境还是评测？
Runtime 改动前后是否可比较？
```

`717138d` 随后建立 artifact-driven 的顺序 Implementer/Reviewer/Verifier coordinator，
并加入 single/multi comparison。它比最早固定角色循环更真实，但仍不是并发 fanout。

### 5. 用小修复收紧证据语义

2026-07-10 的提交链体现了真实纠错：

- `5c3c83c` 集中 failure taxonomy。
- `a324095` 区分 candidate、local verified、official resolved。
- `b0a1b3c` 修复 comparison 忽略 `model_patch`。
- `2630ab5` 修复 case study 在 final evaluation 前写入造成的 stale artifact。
- `587fec4`、`35faa0d` 继续收紧 verified 与 official outcome。

最终约束是：**生成 patch、Reviewer PASS 和 official solved 是三件不同的事。**

同批次的 `a842c29`、`0eb5c73`、`41bd42f` 又把恢复从“重新提供旧消息”推进为 durable
approval、operation ledger、target fingerprint 和 stale guard。

### 6. 将模拟能力替换为真实控制链

`2a7fd69` 加入 official per-case parser、scorecard、matched ablation 和 OCI execution。
2026-07-12 的 `fb89801` 完成两个关键替换：

1. 用 pending/responded/cancelled request、`waiting_human` checkpoint、`forge respond`
   与 resume 替换模拟 `ask_human`。
2. 用 disposable worktree 中的真实 AgentLoop worker、DAG、scope gate、patch integration
   与只读 finalizer 替换轻量 fanout primitive。

因此当前 HITL 和 live fanout 的成熟时间是 07-12，而不是最早出现同名工具的日期。

### 7. 复杂度出现后再分层和框架化

能力增长后，AgentLoop 一度同时负责模型、上下文、工具、审批、账本、checkpoint、HITL 和
终态。`3542457` 将其拆成 start、run preparation、turn preparation、tool execution、
lifecycle 和 stop；`d6a7724` 再把主要 Capability 按需拆成：

```text
presentation/API -> application -> domain
                         |
                       ports <- adapters
```

`6d4f696` 随后补 full-request context window、事务安全压缩和 evidence-backed long-term
memory；`6a2e994` 用 request object 与类型测试降低动态字段和长参数列表的理解成本。

内部边界稳定后，`b6cbc8e` 才增加 `Harness.run/resume`、稳定扩展协议、配置驱动装配、
Hook、pause/cancel/steer、事件流和模型能力协商。它采用框架式 API，但仍是 repository-task
Harness，不是通用 workflow engine。`0961f00` 最后用 repeated campaign 验证同配置能否
重复，而不再依赖一次成功 demo。

## 能力成熟链

| 能力 | 原型 | 关键补强 | 当前边界 |
| --- | --- | --- | --- |
| AgentLoop / Task | 04-30 最小循环 | 06-10 checkpoint；07-10 ledger；07-19 run control | 一次命令一个 run/task，无 active-task session |
| Memory | 04-30 最近 N 条文本 | 07-16 working、digest、long-term 与安全压缩 | lexical recall，不恢复 KV Cache |
| HITL | 04-30 auto approval placeholder | 07-10 durable approval；07-12 durable clarification | clarification 不授权副作用 |
| Multi-Agent | 04-30 固定角色顺序 | 07-04 artifact coordinator；07-12 隔离 DAG fanout | 本机 coordinator，不是 distributed swarm |
| Safety | 04-30 path/command/permission | 07-10 fingerprint/ledger；07-11 worktree/OCI | Local/OCI 都不夸大为 hostile multi-tenant isolation |
| Evaluation | 04-30 小型 fixture | 06-22 SWE-bench；07-11 official parser；07-19 campaign | Smoke-5 不代表 Lite 300 总体结果 |
| Architecture | 能力目录内职责混合 | 07-14 stages 与 Ports/Adapters；07-19 Public API | Capability-first，不是抽象通用框架 |

## 三个容易混淆的概念

| 名称 | 含义 | 不是什么 |
| --- | --- | --- |
| 早期 `RunSession` | 文件系统中的一次 run 元数据 | 多 Task 用户会话 |
| 当前 `AgentRunSession` | 进程内的一次 run aggregate | durable repository 或 active-task manager |
| `TaskCheckpoint` | continuation 所需的显式状态摘要 | 完整进程快照、KV Cache 或模型隐藏状态 |

## 当前明确没有实现

- 会话级 active-task switch、跨 run 任务队列和自动恢复旧 Task。
- 进程级强制抢占或已执行副作用的自动补偿事务。
- 自动 model-driven 任务拆解、任意冲突解决或 distributed worker service。
- Vector RAG、组织级长期记忆或模型自主写入权威事实。
- Hosted Agent 平台、完整 IDE、RL training platform 或 leaderboard。

这些是当前 scope，不应包装成已经完成的能力。

## 可审计的迭代证据

这条历史比“最终代码很多”更能说明工程过程：

1. Auto approval、模拟 `ask_human` 和固定角色循环都被后续实现替换，而非只做功能累加。
2. `adbe5e7` 主动做过大规模减法，将项目收窄到 repository task 与 evaluation evidence。
3. `model_patch` 漏判、case study stale 和 solved 语义都有独立修复与回归测试。
4. 架构分层发生在长 AgentLoop 与职责混合已经暴露之后，Public API 又晚于内部治理。
5. 验收从单次 patch 逐步升级到 official denominator、matched ablation 与 repeated campaign。

讲解项目时，优先讲三条因果链：

```text
不可恢复的 run -> checkpoint -> approval/ledger/stale guard -> continuation
单次 patch demo -> evidence levels -> official parser -> scorecard/campaign
能力增长 -> AgentLoop 过长 -> stages -> capability architecture -> public facade
```

每条链都要能指出失败现象、设计选择、当前入口、验证证据和剩余边界。

## Git 历史治理

公开 `master` 不为美化标题而重写。历史重写会改变后续 SHA，破坏 PR、链接、分支和外部
clone。本文是旧提交的权威语义索引；后续提交遵守：

- 标题只描述一个可观察行为，例如 `feat(runtime): persist waiting-human checkpoint`。
- 修复说明被纠正的语义，架构调整说明行为不变量。
- 避免 `complete`、`finalize`、`production-ready`、`readiness`、`polish`。
- 能力分阶段成熟时，在正文标明 `Introduces`、`Hardens` 或 `Replaces`。

当前能力、未来计划和版本变化分别见[能力真实性矩阵](CAPABILITY_REALITY_MATRIX.md)、
[Roadmap](ROADMAP.md)和[变更记录](../CHANGELOG.md)。

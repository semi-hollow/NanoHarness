# 技术文档规范

> 本文是 `docs/**/*.md` 的文档治理 Owner。NanoHarness 的公开技术文档采用 **design-review-first** 结构：先建立系统模型，再按真实运行顺序展开关键设计，最后用附录和 Source Anchors 支持深入审阅。

# 1. 文档目标与事实来源

公开文档只承担三件事：

```text
快速建立系统模型
→ 评审 Ownership / Trust Boundary / State Transition
→ 需要时准确下钻 canonical source owner
```

文档不是源码百科，也不是逐字段教程。

事实优先级：

```text
Current Source Code
>
Current Tests
>
Current Docs
>
Historical Docs / Branches
```

# 2. Canonical 文档

| 文档 | 唯一详细职责 |
|---|---|
| `架构导览.md` | 全局定位、核心能力、专题关系 |
| `单Agent运行链路.md` | Thread → Turn → Run → Model Step 生命周期 |
| `运行治理与副作用.md` | Tool batch、`_execute_call()`、Ledger、Approval、Observation |
| `上下文工程与长任务.md` | Stable/Dynamic Context、Prompt Window、Digest、LTM |
| `多Agent编排.md` | Planning、Worker、HARD/LIVE、Trusted Integration |
| `持久化与恢复.md` | Durable files、ID / pointer、Crash Resume、Worktree |
| `核心能力与代码入口.md` | Capability → canonical Owner → Source |
| `DOCUMENTATION_RULES.md` | 文档边界和验证规则 |

一个能力只有一个 detailed owner；其他文档只做短引用。

# 3. 所有专题采用“总—分—附”

## 总：第一屏先建立全局理解

开头先回答：

```text
这块解决什么问题？
整体流程是什么？
最重要的设计判断是什么？
```

第一屏只表达业务阶段 / Runtime role，不复制完整调用栈。

判断标准：

> 不放大页面，也能在 30–60 秒内理解整体流程。

避免第一屏堆：

```text
大量 private method
Repository / Adapter 细节
完整字段
十几个交叉节点
```

## 分：按真实运行顺序展开

正文优先：

```text
Input
→ Decision
→ State Transition
→ Execution
→ Durable Result
→ Failure / Recovery
```

相邻章节应该能自然回答：

```text
上一阶段产出了什么？
下一阶段为什么需要它？
```

不能按“想到哪个类就写哪个类”的顺序组织。

## 附：深挖和兜底

完整案例默认下沉正文后部。

附录适合：

```text
端到端案例
磁盘 / JSON / JSONL 示例
ID 关系
Crash window
复杂状态关系
Source Anchors
```

# 4. 单屏展示与滚动顺序

核心专题必须适合：

```text
打开一个文件
→ 自然向下滚动
→ 完成一个专题
→ 必要时切一次源码
→ 进入下一个专题
```

避免：

```text
Section 2 → Section 8 → Section 4
频繁在多个 Markdown 之间往返
依赖隐藏旁路材料
```

因此章节顺序本身就是推荐审阅顺序。

# 5. 文件粒度

文件数量不追求最少，而追求技术边界合理。

一个专题适合独立存在，当它：

```text
解决一个明确技术问题
+ 有独立主链
+ 可以从头到尾自然讲清楚
+ 与其他专题没有大面积重复
```

两个文件长期重复同一 Flow / Owner 时合并；一个主题内部已经形成独立技术问题时拆分。

# 6. 图示规范

优先用紧凑 text diagram 或 Mermaid。

高价值图：

```text
Execution Flow
Ownership / Dependency Injection
State Transition
Persistence Pointer
Crash Resume
Concurrency / Trust Boundary
```

信息密度分层：

```text
总图：只保留阶段
正文分图：加入关键 class / state
附录图：允许完整 ID / persistence relationship
```

禁止把完整实现调用树压进一张图。

# 7. 主动正文与实现细节的边界

正文优先展开真正影响系统判断的内容：

```text
Ownership
State machine
Trust boundary
Context lifecycle
Run governance
Side-effect idempotency
Crash recovery
Human authority
HARD / LIVE semantics
Trusted integration
```

默认下沉源码 / 附录：

```text
完整 dataclass 字段
全部 enum
private helper 清单
Python 序列化语法
lock primitive
CLI 参数到内部文件的具体映射
tuning constant
latest-N 数值
低价值辅助字段
```

原则：

> 必须掌握的实现细节，不等于必须主动展示的设计内容。

# 8. 示例 / Hero Case

案例用于解释系统，不用于覆盖所有分支。

优先使用一个能同时串起：

```text
identity
ownership
state transition
failure
recovery
```

的案例。

当前高价值案例：

```text
Run Governance
Assistant batch → Approval → Same-Turn Resume → finish original batch

Context
root task → human steer → validation FAIL → write → validation PASS

Multi-Agent
A/B parallel → strict frontier → LIVE freshness
```

完整案例默认位于附录。

# 9. Ownership / Dependency Injection

不要抽象罗列 Pattern。

应通过真实链路展示：

```text
payload 在谁手里
pointer 在谁手里
谁做状态转换
哪个 Repository 持久化
谁消费 durable state
```

例如：

```text
ConversationItem
→ TaskCheckpoint.pending_execution
→ OperationIntent
→ Approval / OperationRecord
```

这样读者可以直接理解为什么系统不是一个大 `AgentState`。

# 10. Run Governance

`运行治理与副作用.md` 必须直接映射 `_execute_call()`：

```text
Route / Guardrail
→ Special protocol / provenance
→ Build OperationIntent
→ Ledger replay / crash idempotency
→ Repeat guard
→ Authorization
→ Execute + durable result
```

文档解释设计语义；源码注释使用相同阶段命名。

# 11. 持久化专题

因为 Runtime 有多个 durable owner，Persistence 必须保持独立文档。

`持久化与恢复.md` 顶部固定有：

```text
磁盘总图
+ 每个文件一句角色说明
+ 对象粒度
```

正文再解释：

```text
Conversation
ContextState
TaskCheckpoint
Approval / HumanInput
Operation Ledger
Trace / Artifacts
Worktree
Crash Resume
```

其他专题只引用它，不重复完整文件树。

# 12. 亮点优先表达不变量

优先表达：

```text
Tool Observation durable 后，batch cursor 才推进。

Same-Turn Resume 不重新 discovery stable inputs。

ContextState 是 Context authority；
Checkpoint 只持久化 revision pointer。

executing outcome unknown 时不能自动重跑副作用。

Worker candidate 不能直接成为 trusted state。

LIVE 只放松 start barrier，不放松 final integration trust。
```

避免把核心能力写成纯功能列表。

# 13. Markdown 与源码是两层材料

```text
Markdown
= Architecture / Design Review View

Source Code
= Implementation Detail View
```

推荐：

```text
先在 Markdown 讲完整方案
→ 再切 1–2 个 canonical owner
→ 展示实现
→ 结束该专题
```

避免一个专题中频繁 Markdown ↔ Source 来回切换。

# 14. 关键源码注释与文档对齐

重要 owner 的内部注释应能一眼看出大阶段：

```text
ToolExecutionPipeline._execute_call()
RunPreparation.create_session()
PromptWindowManager.prepare()
_build_digest() / _merge_digest()
FanoutCoordinator._execute_plan()
FanoutCoordinator._integrate_candidate()
```

目的不是增加解释量，而是让源码结构与技术方案结构一致。

# 15. 正向技术叙事

公开文档只描述：

```text
当前系统解决什么
→ 当前设计怎么工作
→ 核心 contract / invariant
→ failure 怎么收口
```

不以反事实比较、问答清单或准备式话术作为章节骨架。

真正影响当前 contract 的限制，用简短“当前边界”说明。

# 16. Authority 与 Projection

固定区分：

```text
conversation.jsonl
= raw Conversation authority

ThreadContextState / ConversationHistoryDigest
= derived Context state

TaskCheckpoint
= Run recovery state / cursor

Operation Ledger
= side-effect execution state

trace.jsonl
= execution evidence

Workbench / report
= presentation projection
```

Provider `role=user` 不能替代 Runtime `human_authority`。

# 17. 生命周期术语

统一：

```text
ConversationThread → Turn → Run → Model Step
```

```text
Follow-up = same Thread + new Turn + new Run
Resume    = same Thread + same Turn + new Run
```

不得把 Model Step 写成 Turn。

# 18. Source Anchors

每篇专题末尾保留 5–10 个高价值入口：

```text
真实 path
+ canonical class / method
```

不维护行号，不枚举所有 helper。

# 19. 完成检查

## 连续审阅

```text
能否从第一行自然一路向下？
第一屏是否足够轻？
章节是否按真实流程排列？
是否突出真正有区分度的设计？
```

## 深入下钻

```text
追问时能否继续滚到案例 / ID / persistence？
能否快速定位 canonical source owner？
```

## 公开项目质量

```text
是否像设计文档而不是学习笔记？
是否百科全书式堆实现细节？
是否重复另一个专题？
```

# 20. 提交验证

```text
1. Source symbol / path 与当前代码一致
2. README / docs 无 stale link
3. 每个能力只有一个 detailed owner
4. 第一屏一眼可读
5. 章节按真实流程展开
6. 完整案例主要位于附录
7. Persistence 保持独立 owner
8. 文档治理测试通过
9. 公开语言保持项目化
10. 还能缩短但不损害 design completeness
```

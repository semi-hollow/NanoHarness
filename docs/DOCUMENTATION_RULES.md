# 技术文档规范

本文是 `docs/**/*.md` 的唯一文档规范 Owner。能力设计只在各自 canonical
文档展开；本文件只规定内容边界、命名和验证方式。

## 1. 事实来源

发生冲突时按以下优先级判断：

```text
Current Source Code
>
Current Tests
>
Current Docs
>
Historical Docs / Branches
```

文档描述当前 checkout 的真实能力。源码与旧文档不一致时修正文档，不修改源码去
适配历史叙事。

## 2. 内容深度

### Level 1 — 核心设计叙事（Core Design Narrative）

缺少后就无法建立完整系统模型的内容，必须进入正文：

```text
Input
→ Decision
→ Runtime strategy
→ Execution
→ Integration
→ Failure handling
→ Recovery / Replan
→ Next effective state
```

### Level 2 — 主要设计澄清（Primary Design Clarifications）

解释核心流程中最重要的相邻边界，例如两个策略的差异、可信状态由谁拥有、失败在
哪个边界收口。正文只保留影响正确理解的部分。

### Level 3 — 深层实现索引（Deep Implementation Reference）

私有字段、锁实现、计数器、辅助函数和完整 schema 默认不进入正文。需要继续下钻时，
通过文末的 `Source Anchors` 定位源码。

## 3. Canonical 文档职责

| 文档 | 唯一详细职责 |
|---|---|
| `架构导览.md` | 顶层 Ownership、Thread→Turn→Run→Model Step 主链、能力边界与文档导航 |
| `核心能力与代码入口.md` | 能力（Capability）→ canonical Owner → primary method → source file |
| `Agent运行数据结构与模型输入.md` | Thread/Turn/Run/Model Step 所有权及模型输入快照 |
| `上下文工程.md` | TurnContextSnapshot、动态 System Context，以及跨 Turn Memory 边界 |
| `上下文压缩与长任务设计.md` | raw Thread、Prompt Window、Conversation compaction 与继续执行 |
| `运行治理与工具执行.md` | 完整 assistant batch 到授权、副作用、唯一 Observation 与 crash resume |
| `运行产物与持久化契约.md` | Thread authority、Run recovery、Audit Evidence 与 Derived Presentation |
| `多Agent编排.md` | Planning、HARD/LIVE、Worker/Finalizer private Thread、Integration 与 Recovery |
| `生产化边界与扩展.md` | 跨能力区分当前简化实现、适用规模与可能的生产方向，不形成 Roadmap |

非 Owner 文档只能保留一句角色说明、一个链接和必要的 Source Anchor，不重复展开。

## 4. 高阶完整性

精简不能删除控制闭环。每篇能力文档至少回答：

```text
输入是什么？
谁做业务决定？
谁执行？
成功状态由谁确认？
失败在哪里停止或恢复？
下一状态如何产生？
```

Design completeness 不等于 implementation-detail completeness。完整描述控制闭环，
不逐项解释所有字段、枚举和 private helper。

## 5. 图示

只保留显著降低理解成本的图：

- Ownership / Dependency Injection：谁创建、谁持有、谁调用；
- Execution Sequence：关键方法的时间顺序；
- State Transition：状态变化与失败出口；
- 必要时补 Concurrency 或 Trust Boundary。

图中使用真实对象名，只画影响系统模型的节点，不复刻全部实现。

## 6. 真实源码名称

涉及数据结构、调用链、生命周期和字段流转时，使用当前 canonical class、method、
field 或 local variable 名称。业务概念首次出现时采用：

```text
业务含义（CodeSymbol）
```

方法链应支持直接用 IDE 或 `rg` 定位。不得为易读性创造源码中不存在的近义对象。

生命周期术语固定为：

```text
ConversationThread → Turn → Run → Model Step
```

`Turn` 只指一次顶层用户请求；AgentLoop 内一次 `llm.chat` 称为 Model Step。文档不得用
“下一 Turn”描述同一顶层请求内的下一次模型调用。

涉及 authority 时必须区分：`conversation.jsonl` 的 raw Conversation 是权威对话；
`trace.jsonl` 是执行证据；Prompt Window / Digest / Workbench 都是投影。Provider role
不能替代 `origin + human_authority`。

## 7. 跨文档重复

- 一个能力只有一个详细 Owner；
- 非 Owner 文档只保留一句角色说明、canonical 文档链接和必要的 Source Anchor；
- 不复制另一个文档已经完整表达的调用链、边界表或示例。

## 8. 历史内容

- 不在当前架构叙事中保留旧类名、旧 package 路径、旧角色流水线或版本演进叙事；
- 冻结 Evidence 所需旧字段只能标记为 read-only presentation compatibility，不能写成生产恢复契约；
- 当前未实现的能力只作为简短 Design Boundary，不形成版本演进计划；
- 实验事实与设计契约分开，不把相关性写成因果结论。

## 9. 源码入口（Source Anchors）

每篇核心文档末尾保留 5–8 个高价值入口。每项只包含真实路径、canonical symbol 和
一句用途，不维护易漂移行号，不枚举 private helper。

## 10. 中性技术语言

文档只描述架构、运行行为、设计边界、验证事实和源码入口。标题使用中文或
“中文（CodeSymbol）”形式；避免特定交流、考察、背诵或准备场景的表达。

## 11. 精简检查

提交前逐段判断：

1. 删除后是否损害核心控制闭环？
2. 是否重复另一个 canonical Owner？
3. 是否可以用表格、图或 invariant 代替重复段落？
4. 是否在解释私有字段、锁、计数器或辅助函数？
5. 是否可以改成一个 Source Anchor？

优先使用：图或表格 > 重复段落，Invariant > 多个相似例子，Source Anchor > 私有实现教程。

## 12. 验证检查

1. 关键 class、method 和 source path 在当前源码中存在；
2. 核心流程包含输入、决策、执行、集成、失败与恢复；
3. 每项能力只有一个详细文档 Owner；
4. 深层实现细节已删除或下沉到 Source Anchors；
5. 图中节点均为真实对象或明确标注的 Application composition boundary；
6. 文档没有历史实现和版本演进叙事；
7. 禁止措辞扫描为零；
8. 文档治理测试通过；
9. 内容还能缩短但不会损害设计完整性。

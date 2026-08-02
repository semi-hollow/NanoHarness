# 代码结构演进：从功能堆叠到可导航的 Harness

本文只讲 **NanoHarness 的代码结构为什么变化、每次解决了什么问题，以及这些选择如何在
面试中讲清楚**。功能加入顺序见[功能演进](../FEATURE_EVOLUTION.md)，当前依赖规则见
[架构契约](../ARCHITECTURE.md)。

## 1. 最准确的起点描述

项目早期不是“没有架构”，也不适合描述成“先写了一堆函数”。更准确的说法是：

> 早期采用 feature-first / capability-first 的模块化单体：先按 context、model、tool、
> safety、trace、benchmark 等能力拆文件，快速闭合 AgentLoop；但能力内部没有稳定的职责分层，
> 数据模型、流程编排、文件与进程操作、报告渲染经常位于同一个模块或类中。

这适合验证 MVP，但随着 checkpoint、HITL、operation ledger、multi-agent 和 evaluation 加入，
三个问题开始集中出现：

1. `AgentLoop` 同时知道模型、上下文、审批、工具、状态和落盘细节，修改一个分支容易影响全链路。
2. Application 直接依赖 JSON、文件系统、Git 和 subprocess，单测只能连同基础设施一起准备。
3. Python 的 `dict`、位置参数和泛化方法名让类型关系只能沿调用链反查，折叠代码后看不出主入口。

这属于 **模块化方向正确、边界治理不足造成的 architectural erosion**，不是必须引入微服务或
完整 DDD 才能解决的问题。

## 2. 演进阶段

| 阶段 | 主要变化 | 解决的问题 | 保留的取舍 |
| --- | --- | --- | --- |
| 1. 最小闭环 | 按能力组织 context、model、tool、safety、trace | 先证明模型能在真实仓库中观察、行动、回填 | 允许流程与基础设施暂时混合 |
| 2. 能力收敛 | 删除分散 workflow，以 repository task + SWE-bench evidence 为主线 | 防止项目“变宽但变散” | 不建设通用工作流平台 |
| 3. Runtime 分阶段 | 从长 `AgentLoop` 抽出 RunPreparation、TurnPreparation、ToolExecution、RunLifecycle | 让循环只负责阶段推进，每个状态只有明确 owner | 同一进程内的模块化单体，不拆微服务 |
| 4. 六边形边界 | 复杂能力内部建立 Domain、Application、Ports、Adapters | 业务规则不再直接依赖 Git、文件、网络、模型 SDK | 简单能力保持扁平，不机械分层 |
| 5. 统一装配与入口 | `Harness.run`、Request/Result、`runtime.wiring` composition root | 外部调用方不需要认识内部阶段和具体 Adapter | 内部路径可以继续演进，顶层 API 承诺稳定 |
| 6. 显式数据与状态 | dataclass、Enum、TypedDict、keyword-only、命名转换 | 调用处直接看出字段含义，非法状态更难表达 | 小型协议值对象仍可保持简洁，不为形式增加样板 |
| 7. 可导航主链 | `# 主要入口`、语义方法名、准备区/分支区/证据区可折叠 | Collapse All 后先读主链，需要时再下钻 | 注释解释 owner、边界和失败语义，不逐行复述代码 |
| 8. Evidence 与主流程分离 | 主流程调用 `_record_*`，原始 `EventSink.add` 字段放在证据叶子 | Trace 保持完整，但不再占据业务方法二三十行 | 不建立一个全知的 God Trace Service，事件语义仍归所属能力 |
| 9. 可执行架构治理 | AST 导航测试、mypy、架构依赖测试 | 防止后续提交重新引入模糊入口、位置构造和内联 Trace | 守卫只覆盖高风险 Core，不把代码风格变成机械 KPI |

代表节点包括：`adbe5e7` 的能力减法、`3542457` 的 Runtime 阶段拆分、`d6a7724` 的能力
边界重构、`6a2e994` 的类型契约、`b6cbc8e` 的框架式入口，以及后续 Public Facade、Run Story
和代码导航治理。

## 3. 当前各层到底是什么

用 Java 支付/结算项目类比最容易建立地图，但类比不是一一等价：

| NanoHarness | Java 类比 | 当前职责 | 类比的边界 |
| --- | --- | --- | --- |
| Domain | Entity、Value Object、领域状态机 | 定义 Task、Operation、Hook 决策和状态转换 | 不是要求聚合根、仓储等 DDD 套件全部出现 |
| Application | Application Service / Use Case | 编排一次 run、turn、授权、工具执行和评测 | 不负责具体 JSON、Git 或 HTTP 实现 |
| Port | Java interface / SPI | 声明 Application 真正需要的模型、存储、事件和环境能力 | 不为每个 helper 都建接口 |
| Adapter | Repository、Client、Gateway 实现 | JSON 落盘、LLM API、Git、subprocess、OCI | 不能决定 `official_resolved` 等业务结论 |
| Presentation | Controller、CLI、View | 解析输入、调用 Use Case、渲染 Read Model | 不从原始 JSON 重新编排 Agent |
| Wiring | Spring `@Configuration` / Composition Root | 选择 Adapter 并通过构造函数注入 | 不是 Service Locator，运行时不隐式全局取对象 |

依赖方向固定为：

```text
CLI / UI -> Public API -> Application -> Domain
                              |
                              v
                            Port <- Adapter
                              ^
                              |
                           Wiring 装配
```

六边形架构解决的是 **依赖方向与替换边界**，不是让目录显得高级。NanoHarness 仍是
Capability-first modular monolith：只有 Runtime、Evaluation、Multi-Agent 等复杂能力内部才
分层。

## 4. 具体做过哪些可读性整改

### 4.1 从长循环变成阶段编排

早期 `AgentLoop` 同时准备上下文、调用模型、检查工具、执行、恢复和落盘。当前首轮阅读只需看：

```text
AgentLoop.run
  -> RunPreparation.prepare_run
  -> TurnPreparation.prepare_turn
  -> ModelPort.chat
  -> ToolExecutionPipeline.execute_calls
  -> RunLifecycle.finalize_run
```

阶段类不是为了“多几个文件”，而是让 run、turn、tool、durable state 分别拥有自己的变化原因。

### 4.2 从动态字典变成显式记录

边界 JSON 仍然是字典，但进入 Domain/Application 后优先使用 dataclass、Enum 或 TypedDict。
对于字段多、同类型字段多或会影响状态/结论的记录，采用 `@dataclass(..., kw_only=True)`：

```python
FailureDiagnosis(
    failure_class="locally_verified_candidate",
    summary="...",
    evidence=evidence,
    next_actions=["..."],
    severity="low",
)
```

这里的规则不是“所有 Python 构造器都必须写关键字”，而是：**只要字段顺序本身不能表达业务
含义，就禁止位置构造**。这类似 Java 中宁愿使用有语义的 Builder/Factory，也不接受一串同为
`String` 的构造参数。

### 4.3 从泛化动词变成业务动作

`describe`、`handle`、`process`、`attach` 只有结合类名才勉强可懂。关键 Use Case 入口改成
`evaluate_task`、`build_operation_intent`、`enrich_result_with_failure_diagnosis` 等可独立阅读的
名字；Hook 则统一使用 `before_model / after_model / before_tool / after_tool / on_checkpoint /
on_stop`。

“一词一义”同样用于概念边界：

- `PythonValidationTool`：模型可调用的受限 compile/unittest/pytest 工具。
- `BenchFailureAnalyzer`：运行结束后根据事实做失败归因，不是 Tool。
- `TraceEvent`：追加事实；`TaskCheckpoint`：可恢复状态快照；`OperationLedger`：副作用幂等状态。

### 4.4 从字段拼装变成语义证据动作

以前业务方法中反复出现十几行 `trace.add(...)`，读者先看到 JSON 字段，后看到控制流。现在：

```python
self._record_tool_execution_started(session, step, tool_call)
tool_observation = self.tool_gateway.execute(...)
self._record_execution_evidence(...)
```

`_record_*` 只负责“这个事实以什么 schema 落入 EventSink”，集中放在可折叠的“证据记录器”区域。
这样没有丢掉可观测性，只是把 **业务决策** 与 **证据序列化** 分开。它类似支付流程先调用
`recordPaymentAuthorized(...)`，而不是在主交易方法里展开审计表每个字段。

### 4.5 从约定变成自动守卫

`tests/test_code_navigation.py` 现在自动检查：

- Core 阅读范围与规范入口存在；
- 关键 Application Service 不暴露模糊动词；
- 高歧义语义记录必须 keyword-only；
- Runtime 和 Live Fanout 的原始 `EventSink.add` 只能位于 `_record_*` 证据叶子；
- `ToolExecutionPipeline` 不保留无人调用的私有方法。

这相当于 Java 项目用 ArchUnit 保护分层依赖：文档说明意图，测试防止架构随提交慢慢失真。

### 4.6 从 Python 压缩表达式变成显式业务判断

Python 的 `next(generator, None)`、推导式、`any/all` 和海象运算符都是实际工程中会出现的正常
语言能力，维护者需要掌握，不能为了照顾 Java 背景把项目写成“Java 风格 Python”。终态质量门
的问题不是使用了 `next`，而是关键业务判断同时使用压缩表达式和 `denied/effective` 这类弱语义
变量，折叠后无法看出对象角色。

当前规则是：常用 Python 写法可以保留，首次遇到时应说明语法、输入输出和 Java 对照；当权限、
状态、恢复或评测结论因为表达式过密而隐藏业务意图时，再抽成具名方法或显式分支。变量名必须
同时说明对象和角色，例如 `blocking_hook_decision`、`final_stop_request`。已经类型化的
`RuntimeConfig` 仍应直接访问字段，不能用 `getattr(config, ..., default)` 隐藏契约。

静态导航测试不禁止某一种 Python 语法；它只保护能机械判断的结构风险：Core 不使用难以判断结合
顺序的嵌套三元表达式，Application 不使用裸 `effective/denied/lowered` 控制变量，类型化配置不
退回动态读取。语法学习与业务可读性都要保留。

### 4.7 从入口堆叠变成固定学习控制面

代码减重不等于删除真实能力。CLI、Adapter、完整 benchmark campaign 和跨平台安装脚本仍是可用
边界，但不应同时成为首次学习入口。项目现在只保留四个共享 PyCharm 配置：三个正式 Lab 分别
覆盖受治理运行、多 Agent 协同和评测改进闭环；Operator Console 只承担可选的真实模型体验。

三个 Lab 统一完成以下动作：固定输入，进入正式 Runtime，发布 Evidence，自动打开对应 Workbench
场景。Debugger 用于观察动态因果，Workbench 用于回放已经落盘的状态、Trace、Artifact 和评测
结论。这样审批、checkpoint、worker diff、finalizer 和 failure taxonomy 不再各自需要一套演示
脚本或文档。

Debug Lab 的运行说明、入口地图和脱稿验收也收敛到一个 README。评分卡、重复的 Run 配置目录和
不存在的旧按钮被删除；AST/回归测试精确锁定四个共享配置和三个自动打开 Workbench 的场景。
14 个 symbol 断点按 `7 + 5 + 2` 绑定各自场景，运行一个 Lab 不会再停进另一条主线。真实模型
交互和完整 benchmark 分别只有 Operator Console 与 campaign 一个操作 owner，不再从 Debug runner
暴露重复 wrapper。后续增加能力时，优先接入现有三条心智模型，不按 feature 新建 Lab。只有真实
能力被移除时才算缩小 scope；把高级能力从必读路径降级，只是在控制认知预算。

### 4.8 从“记得写清楚”变成可执行的核心入口契约

一次真实回退暴露了前几轮治理的盲区：Runtime 主链已有阶段注释，但
`LocalAgentWorkerAdapter.run_worker` 作为多 Agent Worker 的关键入口仍然只有参数和实现。原因不是
这个方法不重要，而是检查规则只覆盖了 AgentLoop、Trace owner 和 Workbench 入口；规范存在于对话
和人的记忆里，没有覆盖 Context、ToolRouter、Multi-Agent、Benchmark 与 Evaluation 的完整主链。

这就是局部 Architectural Erosion：大部分代码看起来已经整齐，新能力却会从未受保护的边界重新
长出同类问题。解决方式不是给每个函数写长注释，也不是设置全仓注释行数，而是维护一份核心工作流
入口清单，并对每个长流程执行三项约束：

1. docstring 说明上游、职责和关键输出；
2. 使用 2 至 5 个可折叠阶段说明“准备、决策、副作用、收口”等业务阶段；
3. AST 导航测试校验入口仍有 docstring、阶段成对且数量没有退化。

短委托、序列化、字段映射和纯 renderer 不强制分区，避免注释比代码更长。Checkpoint 展示也使用
同样原则：Trace 保存完整快照，Workbench 对相邻快照做状态差分，显示“进入审批等待、审批后恢复、
保存工具结果”等原因，而不是平铺多个同名 `Checkpoint`。不同运行类型则必须显示 Case、Run ID 和
证据路径，不能把 Fanout 通过与另一条 SWE-bench Case 阻塞投影成同一个结论。

这可以类比 Java 的 ArchUnit：代码评审负责判断表达是否清楚，自动测试负责阻止已经确认的架构规则
随下一次提交悄悄消失。它保护的是核心阅读路径和证据语义，而不是机械追求注释覆盖率。

### 4.9 从双重状态和隐式 `None` 变成单一 owner 与显式结果

重复 ToolCall 曾同时由 `session.tool_history` 和 `StepController.tool_counts` 判断：一份看最近历史，
一份看全 run 累计，Trace 甚至会出现 Guardrail 失败但工具继续执行。现在由 `StepController` 唯一维护
连续规范化调用；“副作用是否执行”则只由 Operation Ledger 判断，两个问题不再共享状态。

`_execute_call`、重复分支和 `_run_tool` 原先都返回 `StopRequest | None`，其中 `None` 可能表示执行
成功、执行失败、主动跳过或只是继续。当前内部统一返回
`ToolCallOutcome(EXECUTED/SKIPPED/FAILED/STOPPED)`，外围只读取显式 `stop_request`。核心
`Message/Observation` 也改用关键字参数，AST 测试防止字段顺序、重复 owner 和模糊局部名回退。
这类结构守卫保护业务边界，不禁止正常的生成器、推导式等 Python 惯用写法。

## 5. 为什么没有走两个极端

### 没有继续把所有逻辑放回 AgentLoop

优点是文件少，缺点是状态、错误和副作用 owner 不清晰。HITL、恢复和审批一旦增长，单类很快
重新变成不可测试的 God Object。

### 没有做完整 DDD 或每层一个接口

Agent Runtime 的核心复杂度在循环、状态、边界和证据，不在庞大的业务实体关系。为纯函数、唯一
实现或私有 helper 创建 Port，只会增加跳转成本。因此项目只在外部变化点和可替换边界使用 Port。

### 没有把 Trace 全部集中到一个万能 Service

万能记录器会知道所有 capability 的字段，形成新的耦合中心。当前由各能力拥有事件语义，统一
`EventSink` 只负责追加和发布；主流程通过语义化 `_record_*` 隐藏载荷细节。

## 6. 面试时的 90 秒讲法

> 项目最早是 capability-first 的 MVP，我先闭合 context-model-tool-observation 循环。随着
> checkpoint、HITL、operation ledger、multi-agent 和 evaluation 加入，AgentLoop 开始同时承担
> 流程、状态和基础设施，出现了典型的 architectural erosion。我的处理不是直接套完整 DDD，
> 而是先删除偏离 repository task 的能力，再把 Runtime 拆成 run preparation、turn preparation、
> tool execution 和 lifecycle；复杂 capability 内部采用 Application、Domain、Port、Adapter，
> 由 composition root 统一装配，对外只暴露 Harness Request/Result。随后我又处理了动态语言的
> 阅读成本：高歧义记录 keyword-only、关键入口使用业务动词、主流程的 trace 字段下沉到语义
> recorder。一次多 Agent Worker 入口的注释遗漏让我确认，规范只写在文档里仍会继续退化，所以我
> 把 Harness、Runtime、Context、ToolRouter、Multi-Agent、Benchmark 和 Evaluation 的核心长流程
> 列为可执行契约，最后用类似 ArchUnit 的 AST 测试和 mypy 自动保护。结果不是目录更多，而是
> 主链可折叠阅读、外部副作用可替换、状态与 evidence 有唯一 owner。

被追问时应落到一个具体例子：

```text
模型提出 replace_text
-> ToolExecutionPipeline 生成 OperationIntent
-> OperationTracker 建立 operation key 与 fingerprint
-> ToolAuthorizationGate 合并 Hook/approval
-> ToolGateway 执行
-> Observation、Ledger、Checkpoint、Trace 分别由自己的 owner 更新
```

## 7. 可以诚实承认的不足

- 这是单机模块化单体，不是分布式 worker platform。
- Port 的稳定性由项目测试验证，尚未经过多个外部团队长期兼容性检验。
- Python 类型和 AST 守卫降低了理解成本，但不能达到 Java 编译器对所有运行时字典的约束强度。
- 一些 Adapter、renderer 和 benchmark preparation 仍然代码较长；它们被降为下钻细节，不应再
  侵入黄金主链。后续优化优先合并重复 owner，而不是继续增加层。

这组边界不会削弱项目，反而说明架构选择由实际复杂度驱动，而不是为了包装成“自研框架”。

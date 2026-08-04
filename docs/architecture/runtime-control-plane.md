# Runtime 控制面：工具、暂停、人工输入与恢复

> 定位：这是深入追查和架构审计材料，不是项目首轮学习必读。首轮只看 README 的运行主链；需要
> 解释工具检索与路由、审批、恢复、重复副作用或 Ledger 时再进入本文。

本文只解释控制语义和现场展示。公开动作以 `forge run / resume / demo / inspect` 为准；不会再为
answer、approval 或展示维护第二套 CLI。

## 0. 先分清工具链上的五个概念

```text
RepositoryContextAssembler                      先给模型仓库地图和少量相关文件预览
        ↓
ToolRegistry / MCP tools/list                   建立“系统有什么工具”的候选目录
        ↓
ToolRouter.route                                生成“本 turn 模型能看见什么”的工具视图
        ↓
LLM 从可见 schema 中返回 ToolCall              模型选择工具，不直接访问 Python 或文件系统
        ↓
ToolExecutionPipeline -> ToolGateway.execute    Runtime 复核、授权、执行并回填 Observation
```

| 概念 | 回答的问题 | 当前实现 | 不是什么 |
| --- | --- | --- | --- |
| Repo Map | 仓库大致有哪些文件 | 过滤生成物后的相对文件路径清单 | 文件内容索引、调用图或向量库 |
| Tool Registry | Runtime 已装配哪些工具 | 内置工具显式注册；MCP 通过 `initialize + tools/list` 注册 | 当前 task 的规划结果 |
| Tool Router | 当前 turn 给模型哪些 schema | task、Skill、模式和关键词的确定性可见性规则 | 服务注册中心、工具执行器或权限最终裁决者 |
| Tool Gateway | Application 如何统一查询和调用工具 | `schemas/get/execute` Port；默认实现是 `ToolRegistry` | 智能选工具的模型 |
| ToolCall | 模型希望 Runtime 执行什么 | 工具名加结构化参数 | 已执行事实；仍须经过路由和治理复核 |

### 工具怎样与模型交换数据

工具协议类似一组很小的 OpenAPI：Runtime 先给模型 schema，模型返回结构化调用意图，工具再返回
统一 Observation。内部使用 Python dataclass，经过模型 Provider 时转换成 JSON。

```text
ToolSchema（给模型）
{"name": "read_file", "arguments": {"path": "str", "offset": "any", "limit": "any"}}

ToolCall（模型返回）
{"id": "call-1", "name": "read_file", "arguments": {"path": "pricing.py", "offset": 1, "limit": 80}}

Observation（Runtime 回填）
{"tool_name": "read_file", "success": true, "content": "path=pricing.py ..."}

下一 turn
Message(role="tool", name="read_file", tool_call_id="call-1", content="...")
```

内置工具保持为解决代码任务所需的最小集合：

| 阶段 | 工具 | 主要参数 | 返回事实与当前边界 |
| --- | --- | --- | --- |
| 定位 | `list_files` | 可选 `path` | 递归返回最多 200 个相对文件路径 |
| 定位 | `grep` / `grep_search` | `keyword` | 扫描 `*.py`，大小写敏感子串匹配，最多 50 条；两者当前实现相同 |
| 阅读 | `read_file` | `path`，可选 `offset/limit` | 默认 120 行、最多 240 行，并把总文本限制在约 5000 字符 |
| 编辑 | `replace_text` | `path/old/new` | 只替换唯一命中的旧文本，属于写副作用 |
| 编辑 | `write_file` | `path/content` | 创建或覆盖整文件，属于写副作用 |
| 验证 | `python_validation` | `check_type/validation_target` | 固定支持 compile、unittest、pytest，不接受任意 shell 文本 |
| 检查 | `git_status` / `git_diff` | 无 | 返回工作区状态或最多约 6000 字符的候选 Diff |
| 逃生口 | `run_command` | `command` | 经过命令策略和执行环境控制；按副作用操作治理 |
| 人工控制 | `ask_human` | `question`，可选 `choices` | 执行管线持久化问题并暂停；Tool 本体直接调用时 fail closed |

`grep_search` 目前只是 `GrepTool` 的同实现别名，不应讲成独立的语义搜索能力。`run_command` 是受控
逃生口而不是默认检索方式；常见代码阅读主链仍是 `list_files -> grep -> read_file`。

### Repo Map、主动上下文与 Agent 读取

用户不需要手工指定首轮文件，但也不能说“所有文件都等模型调用 `read_file` 后才进入 Context”。
每个 turn 的 `RepositoryContextAssembler` 会先自动：

```text
扫描 workspace
-> 忽略 .git/.venv/.agent_forge、构建目录和生成物
-> 生成按路径排序的 Repo Map
-> 根据 task 对路径和文件内容做关键词计分
-> 选择前 8 个文件，读取前 4 个文件的有界预览
-> 与 task、Memory、Skill、权限摘要一起放进 system context
```

Repo Map 只列文件，不单独列目录；目录通过 `agent_forge/tools/read_file.py` 这样的路径隐式体现。
因此它更接近“过滤并排序后的递归 `find`”，不是只看当前目录的 `ls`，也不是符号调用图。

文件预览不是固定截取前 N 行。Runtime 最多选择 8 个文件，只预览前 4 个；预算按字符分配。文件超过
预算时通过 `truncate_middle` 保留前半段和后半段，在中间插入 `middle truncated` 标记。这能同时看到
imports/定义开头与文件末尾入口，但可能漏掉恰好位于中间的核心方法。

模型得到这份启动上下文后，仍可用 `list_files / grep / read_file` 主动检索。工具结果成为
`Observation`，进入下一 turn。前者是 Runtime 主动提供的 lexical context selection；后者是模型
驱动的 agentic retrieval。

项目存在名为 `rag.py` 的关键词 Top-K helper，但没有 embedding、向量库、chunk index 或语义
reranker。准确口径是：**使用 Repo Map、lexical ranking、文件预览和 agentic retrieval，不声称
Vector RAG**。小型代码仓中，路径、符号和 grep 通常更直接可审计；大型 monorepo 会暴露语义
错配、Repo Map 截断、重复读取和 token/turn 增长，届时再引入符号索引、BM25 与向量 rerank 的
混合检索，而不是默认把整套 Vector RAG 加进 Runtime。

模型反复 `grep/read_file` 的具体成因和当前处理如下：

| 成因 | 当前处理 | 仍然存在的边界 |
| --- | --- | --- |
| Repo Map 只有路径，无法表达符号和调用关系 | task 关键词 ranking + 4 个文件预览 | 同义词、跨文件调用链可能选错 |
| `grep` 只支持 Python 文件和精确子串 | 最多返回 50 条，防止 Context 爆炸 | 不支持 regex、glob、大小写策略和多语言源码 |
| 大文件不能一次完整读取 | `offset/limit` 分页，Observation 回填下一 turn | 模型必须主动决定下一窗口，增加 turn |
| 模型重复完全相同的观察动作 | 连续重复读取达到上限后跳过执行并要求换方向 | 参数略有变化的近似重复仍可能发生 |

低成本的后续演进顺序应是：先把搜索后端换成结构化 `rg`（query、glob、case、limit），再给 Repo Map
增加符号/import 摘要和 Observation 缓存；只有仓库规模和语义错配证据足够时，才增加向量 rerank。

### 工具发现与每轮可见性不是一件事

内置工具没有使用 Consul 式服务发现，而是在 composition root 显式注册。MCP stdio server 才通过
协议执行 `initialize -> tools/list`，再经过 allowlist 包装为本地 `Tool`。这一步回答“系统有什么”。

`ToolRouter.route` 随后根据 task 文本、Skill 和模式，从候选 schema 中生成 `allowed_names` 与
`dropped_names`。模型只在可见 schema 中选择 ToolCall，执行管线还会再次检查工具是否已注册且在
本轮 allowlist 中。因此准确术语是：**工具注册/MCP discovery + turn-level visibility routing +
LLM tool selection + governed execution**。

当前 `task-aware` Router 是关键词启发式，不是模型规划器或语义路由器。例如修复词扩展编辑和
验证工具，只读词移除写工具，SWE-bench 再隐藏自由命令和整文件覆盖。这种实现确定、可测试、
可解释，但不能包装成智能服务发现。候选工具很多时，成熟方案应优先使用角色/场景 preset、结构化
capability/effect metadata 和 fail-closed policy；目录继续变大时再增加 Tool Search 或语义召回。

### “副作用”是一种操作性质，不是三个组件的统称

```text
read_file("pricing.py")
-> 返回文本，pricing.py 没变：没有持久副作用

replace_text("pricing.py", old, new)
-> 即使忽略返回值，pricing.py 也已经改变：产生持久副作用
```

所以副作用不是某个类或文件，而是“调用结束后，外部可观察状态是否发生变化”这个属性。广义上
写文件、改数据库、发消息、扣款、启动可能改变环境的命令都属于副作用。NanoHarness 的
`OperationIntent.side_effect` 使用更窄的 durable 定义：`write_file`、`replace_text` 和
`run_command` 进入副作用协议；读取、搜索和可丢弃验证缓存不进入。

`operation key + fingerprint + Operation Ledger` 是保护副作用的三种机制，不是副作用本身：

- operation key：标识“工具、参数、workspace 和 action 相同的这次操作意图”；
- fingerprint：记录执行/批准时目标文件的版本，类似乐观锁；
- Ledger：记录 planned/pending/approved/executing/executed/failed，类似支付幂等状态表。

检测发生在 `ToolExecutionPipeline._execute_call` 内：`OperationTracker` 先按工具名映射 action，再用
`action in {write, run_command}` 得到 `side_effect`。它是执行前的静态分类，不是执行后猜测文件是否
变化。分类结果决定是否查 Ledger、绑定审批、记录 fingerprint、在真实调用前写 `executing`，以及
重复/恢复时能否重放。

这里的 Ledger 协议只治理**模型请求的工具副作用**。LLM API 成本、Trace/Checkpoint 落盘、benchmark
checkout 和 workspace 创建也属于广义基础设施副作用，但由各自 Adapter/Lifecycle 管理，不进入
Operation Ledger。当前还存在一个必须诚实说明的边界：未知 MCP 工具默认映射为 `read`，因此不能
声称任意外部写工具都已获得副作用治理；生产化应强制外部工具声明 effect/risk/approval metadata，
未知 effect 默认 ASK 或 DENY。

## 1. 当前任务模型

NanoHarness 仍是 **one command, one run, one task**。一次 run 通过 `AgentLoop` 推进，并把可恢复
状态写入 `TaskCheckpoint`：

```text
forge run -> Harness.run -> AgentLoop.run
  -> running
  -> completed / blocked / failed
  -> waiting_human / waiting_approval / paused / cancelled
```

`RunController` 支持协作式 pause、cancel、steer，但只在 turn 开始、模型返回、工具执行前后等
safe boundary 消费信号；它不强杀正在执行的 HTTP/进程，也不回滚已经完成的副作用。项目没有
session `active_task` pointer、任务队列或“暂停 A 后自动切换 B”的产品边界。

## 2. 四种控制不能混为一谈

| 控制 | 作用对象 | 当前入口 | 不包含什么 |
| --- | --- | --- | --- |
| 回答问题 | 一条 pending human-input request | `forge resume <run> --answer ...` | 不授权副作用 |
| 审批副作用 | 一个带 fingerprint 的 operation | `forge resume <run> --decision approved\|rejected` | 不授予永久写权限 |
| pause/cancel/steer | 当前嵌入式 run | `RunController` | 不做进程抢占、全局调度或自动回滚 |
| active-task switch | 会话中的多个任务 | 未实现 | 没有隐藏 task queue 或自动恢复旧任务 |

`resume` 先原子记录 answer/decision，再从 durable checkpoint 创建一条新的 continuation run。
它不会恢复 Python stack、HTTP connection、provider KV Cache 或模型隐藏状态。

写工具没有绕过审批。`PermissionPolicy` 对 `write` 始终返回 `ASK`；随后有两种决定来源：

- `auto_approve_writes=false`：写入 pending approval 和 `WAITING_APPROVAL` checkpoint，等待人批准。
- `auto_approve_writes=true`：Runtime 记录 `auto_approved` 后继续，主要用于受控 Lab 和测试。

Lab 2 使用第二种配置，使多 Agent 编排演示不被人工输入打断；Approval/HITL Demo 使用第一种。
两条路径都经过同一 `ToolAuthorizationGate` 和 Operation Ledger。

### 副作用为何需要独立账本

1. 尚未执行：approval/HITL barrier 保证工具不启动。
2. 正在执行：只能在工具返回后观察确定状态，当前不会中途强杀。
3. 已经执行：`OperationTracker` 保留 operation key 与 pre/post fingerprint，恢复时防重放。
4. 目标已改变：旧 approval 变 stale，不能靠历史 `approved=true` 继续执行。

因此 cancelled/blocked 描述的是 run 状态，不等于事务已经补偿。Operation ledger 是幂等审计边界，
不是 distributed transaction log。

## 3. ToolExecutionPipeline 阅读地图

```text
execute_calls
  -> _select_calls_for_turn       数量上限 + HITL barrier
  -> 对每个 ToolCall 调用 _execute_call
       -> guardrail / 路由检查
       -> ask_human 协议分支
       -> OperationTracker        identity + replay/stale
       -> 连续重复策略             first attempt + one retry
       -> ToolAuthorizationGate   allow / deny / ask
       -> 仅当 proceed=True 时调用 _run_tool
            -> 最后一次 pause / cancel 检查
            -> Ledger 进入 executing
            -> ToolGateway.execute
            -> after_tool Hook
            -> Observation / evidence / checkpoint
```

首轮只读 `execute_calls`、`OperationTracker` 和 `ToolAuthorizationGate` 的合同。`_` 方法是主链内部
分支，不是外部 facade。尤其注意：`_run_tool` 是 `_execute_call` 授权通过后的子分支，不是与
`_execute_call` 平级的第二个入口；只有排查具体 failure 时才展开。

单个调用离开主链时使用显式 `ToolCallOutcome`，而不是让 `None` 同时表示多种结果：

| 结果 | 含义 | 是否调用真实工具 |
| --- | --- | --- |
| `EXECUTED` | 已越过治理边界并取得工具结果 | 是 |
| `SKIPPED` | Ledger 已有确定事实，或无需 durable side-effect Ledger 的相同调用达到上限 | 否 |
| `FAILED` | 路由、参数、授权或工具结果失败，但 run 可继续 | 视分支而定 |
| `STOPPED` | 需要人工介入或无法安全继续 | 否或此前结果不确定 |

### “重复请求”和“已经执行”不是同一判断

| 判断 | Owner | 回答的问题 | 当前策略 |
| --- | --- | --- | --- |
| 连续重复 | `StepController` | 模型是否连续给出完全相同的工具名与规范化参数、没有推进 | 允许首次尝试和一次重试；第三次连续相同调用进入重复分支；不同动作立即重置 |
| 已经执行 | `OperationLedger` | 该副作用是否已有 durable `executed` 记录，且当前目标仍匹配执行后 fingerprint | 不再执行工具，只把上次 Observation 作为确定事实回填 |

因此一次调用可能“重复但从未执行”，例如前两次都被权限拒绝；也可能“以前执行过但不是当前连续
重复”，例如 continuation 再次生成同一写操作。副作用先查 Ledger，再执行重复策略，避免把已经成功
的写操作误判成普通模型循环。

“重放事实”不是重放副作用：Runtime 不再调用 `ToolGateway.execute`，而是把 Ledger 保存的上次
Observation 组装成成功 Tool Message，让下一轮模型知道该动作已经完成。这类似支付重试命中幂等
订单后返回原交易结果，而不是再次扣款。

### Hook 和 Trace 也不是同一机制

Hook 类似 Java Interceptor、Listener 或 AOP 切点：Runtime 到达模型前后、工具前后、Checkpoint 后
或停止前时，允许外部策略介入。它可以 `ALLOW/DENY/ASK`、改写模型响应、脱敏 Observation 或阻止
完成。Trace 类似审计流水，只记录 Hook 及其他阶段已经发生的事实，不参与决策。项目会把 Hook 决定
写入 Trace，但“Hook 被记录”不等于“Hook 就是 Trace”。

## 4. Trace、Checkpoint、Ledger 和 Artifact 的边界

这四个概念都可能落成 JSON 文件，但它们不是同一层数据。可以用支付系统类比：

| 概念 | Java / 支付类比 | 回答的问题 | 不是什么 |
| --- | --- | --- | --- |
| `TraceEvent` | 审计事件流或分布式 Trace span | 某个 step 发生过什么、顺序和结果是什么 | 不能单独恢复运行，也不直接下 solved 结论 |
| `TaskCheckpoint` | durable workflow 状态快照 | 从哪个状态、step、消息摘要继续 | 不是数据库 savepoint、进程镜像或模型 KV Cache |
| `OperationLedger` | 支付订单/幂等操作状态表 | 这个副作用是否 planned、approved、executing、executed，能否重放 | 不是普通日志，也不保存完整会话 |
| `Artifact` | 对账文件、回执或构建产物 | 哪个消费者可读取的结果文件由谁产生、证明什么 | 文件存在不等于结果正确或 official resolved |
| 普通 Log | 应用排障日志 | 开发者当时想看的诊断文本 | 不是稳定机器契约，不应驱动报告结论 |

Trace 的真实链路是：

```text
Application 中的语义动作
-> _record_model_started / _record_tool_execution_started / _record_...
-> EventSink.add(统一事件 envelope + capability payload)
-> JSON Trace Adapter 持久化 trace.json
-> Run Story Read Model 按阶段投影
-> forge inspect / Workbench 分层展示
```

`EventSink.add` 像审计 SDK 的底层写接口；Application 主流程不应直接展开十几行字段。当前原始
payload 只保留在可折叠的 `_record_*` 证据叶子，并由 `tests/test_code_navigation.py` 静态检查。
这样折叠代码时先看到业务控制流，排障或修改 schema 时才展开证据字段。

一次 turn 也不保证事件数完全相同：只读工具、写工具、审批、失败恢复和最终回答经过的条件分支
不同。人类应记住四段稳定主链，而不是背事件数量：

```text
准备输入 -> 模型决定 -> 治理并执行 -> 回填并持久化
```

原始事件是机器审计粒度；四段 Run Story 是操作和学习粒度；Failure Taxonomy 与 Evaluation 是
运行结束后的结论粒度。三层不能相互替代。

## 5. 两分钟现场展示

Approval 是默认 scenario：

```bash
forge demo
```

HITL 使用同一个命令形态：

```bash
forge demo --scenario hitl --answer "Python 3.11"
```

一个命令内部依次运行 waiting phase、记录确定性人工决定、创建 continuation，并打印可直接交给
`forge inspect` 的目标。现场只指出：ToolCall、operation/request identity、durable checkpoint、
continuation、ledger stale/replay check 和最终受治理副作用。

Demo 使用确定性 `ModelPort` 固定 tool call，但复用正式 `Harness`、`AgentLoop`、repositories、
operation ledger、checkpoint、trace 和 tool。它证明控制面接线，不证明在线模型推理、pytest 通过
或 official resolved；Demo 的 local/official 状态应分别是 Not Run / Not Evaluated。

真实任务被阻断时才使用 `forge resume`。Demo 已把两阶段收在一个命令里，不要求记忆旧的
`showcase start/continue` 命令。

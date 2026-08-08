# 失败驱动的 Runtime 设计经验

## 这份文档保留什么

这不是完整开发流水账，而是一组经过筛选的真实故障案例。每个案例都必须说明：

1. Runtime 在什么场景下产生了错误行为。
2. 为什么表面修补不能解决根因。
3. 最终建立了什么可复用的不变量。
4. 哪段代码和哪类回归证据支撑这个结论。
5. 当前方案仍有什么边界。

Git 历史继续保存完整演进过程；当前文档只保留能解释 AgentLoop、工具治理、
Context、Memory、HITL、恢复、多 Agent 和评测闭环的代表性案例。

## 收录与淘汰规则

新增案例必须同时满足以下条件：

- 改变了 Runtime 状态机、权限边界、执行语义、恢复语义或评测结论。
- 有可复现的 failure scenario，而不只是“代码看起来可以更好”。
- 修复形成了可迁移的工程规则，并有测试或真实运行证据。
- 不能被现有案例中的同一条设计原则完整解释。

以下内容不再单独收录：

- Workbench 排版、窄屏溢出、导航和浏览器打开方式。
- PyCharm、断点、绿色按钮和本机解释器配置。
- Windows/macOS/WSL 安装脚本与一次性环境问题。
- Docker daemon、镜像架构和依赖安装等基础设施操作问题。
- 文档命名、页面措辞、代码格式和纯可读性调整。
- 同一根因在不同页面或入口上的重复症状。

## 优先阅读顺序

| 优先级 | 主题 | 必须能回答的问题 |
|---|---|---|
| P0 | 循环保护 | 相同 ToolCall 与连续失败有什么区别，分别何时停止？ |
| P0 | 工具路由 | 工具如何进入本轮模型上下文，为什么可见不等于获准执行？ |
| P0 | Context 与 Memory | 长会话怎样压缩，哪些信息不能被摘要破坏？ |
| P0 | HITL 与恢复 | 人工回答或审批如何落盘，为什么进程退出后还能继续？ |
| P0 | 副作用治理 | operation key、fingerprint 和 Ledger 分别解决什么问题？ |
| P1 | 多 Agent | 并发的正确性边界在哪里，冲突为何要 fail closed？ |
| P1 | 失败归因 | 为什么规则分类可以稳定复现，但 official resolved 不能猜？ |
| P1 | 评测闭环 | 一次改进怎样从 badcase 走到配对实验和采纳决策？ |
| P2 | Provider 适配 | 半包、坏 ToolCall 和 context overflow 为什么不能盲重试？ |

## 核心案例

### 1. 相同 ToolCall 反复出现，AgentLoop 没有形成新进展

**现象**：模型连续使用相同工具和规范化参数。早期实现要么无限重试，要么把正常的
重复读取和危险的重复写入一起 hard block。

**根因**：系统混淆了两件事：模型是否原地打转，以及某个副作用是否已经执行。前者是
AgentLoop 进展判断，后者属于 Operation Ledger。

**当前规则**：首次调用和一次同动作重试被允许；第三次连续相同调用触发上限。无 durable
副作用的读取/观察调用不再执行，Runtime 回填“换工具或参数”的 Observation 后继续；写入、
命令等副作用调用直接阻断，避免重复改变外部状态。

**另一条独立保护**：连续工具故障达到 3 次时熔断；参数可以不同。一次成功会清零计数，
而测试正常执行后的断言失败属于业务反馈，不累计工具故障。

**代码与证据**：[`StepController`](../../agent_forge/runtime/control.py)、
[`ToolExecutionPipeline`](../../agent_forge/runtime/application/tool_execution.py)、
[`test_agent_loop_policy.py`](../../tests/test_agent_loop_policy.py)。

**边界**：阈值是显式运行策略，不是普遍最优常数；应根据重复率、恢复成功率、额外成本和
误杀率校准。

### 2. ToolRouter 把局部限制误判成全局只读

**现象**：任务要求“不要修改测试”，模型却看不到源码写入和验证工具，只能读取文件或把
验证工作转交给人。

**根因**：关键词规则先命中 `do not modify`，没有识别限制对象是 tests，而不是整个任务。
Tool Router 与 Skill 选择还曾各自维护一套判断，导致同一任务得到不同能力视图。

**当前规则**：只读判定由一个 owner 负责，先剥离“不要修改测试”这类局部约束，再判断是否
全局只读。`ToolRegistry/ToolGateway` 回答系统注册了什么；`ToolRouter` 回答本轮给模型看什么；
执行管线再次校验 `allowed_names`，所以隐藏工具不能靠伪造 ToolCall 绕过。

**代码与证据**：[`tool_router.py`](../../agent_forge/tools/tool_router.py)、
[`test_tool_router.py`](../../tests/test_tool_router.py)。

**工程结论**：工具可见性是模型能力面，权限策略是副作用边界。可见不等于授权，两者不能由
同一个模糊关键词判断代替。

### 3. 工具越多，模型反而越容易走错路径

**现象**：SWE-bench 修复中，模型使用自由命令读取源码、写临时脚本，或用 `write_file`
绕开精确编辑；同时 `read_file` 不支持常见的 `offset/limit`，导致模型不断重读文件开头。

**根因**：工具集合按“系统能做什么”暴露，而不是按“当前 workflow 最小需要什么”收敛；
schema 又只符合普通 Python 函数习惯，没有吸收模型常见调用形态。

**当前规则**：修复流程优先暴露发现、读取、搜索、精确替换、Python 验证和 Git Diff；自由
命令和整文件写入只在确有需要时出现。读取工具支持带行号窗口；验证参数区分路径、node id、
命令 flag 和环境不可用。

**代码与证据**：[`ToolRouter.route`](../../agent_forge/tools/tool_router.py)、工具实现与
[`test_tool_router.py`](../../tests/test_tool_router.py)。

**工程结论**：Agent Tool 是模型面对的协议，不只是普通函数。最小工具面降低选择熵，贴合模型
习惯的 schema 减少无效步数，但任何收敛都必须保留真实任务所需的完整闭环。

### 4. 模型仍想调用工具，却被当成已经完成

**现象**：最终输出包含 raw tool-call markup，或在最后一轮仍返回 ToolCall。旧流程把这类响应
当作 final answer，Coordinator 继续发布不完整 artifact。

**根因**：把“模型返回了一段文本”误认为“任务已完成”，没有检查模型是否仍有未执行动作。

**当前规则**：最终轮存在未执行 ToolCall 时记录 `pending_tool_call_rejected`，Run 以
`pending_tool_call_at_stop` 阻断；模型文本、候选改动和完成状态分别建模。

**代码与证据**：[`AgentLoop`](../../agent_forge/runtime/application/agent_loop.py)、
[`test_agent_loop_policy.py`](../../tests/test_agent_loop_policy.py)。

**工程结论**：final answer 不是天然可信的终态。Runtime 必须根据动作协议、预算和状态机决定
是否允许发布结果。

### 5. Provider 半包与异常 ToolCall 被错误归因成 Agent 能力失败

**现象**：HTTP `IncompleteRead`、timeout、429、5xx、非 JSON 参数或文本中的 ToolCall 曾直接
穿透 AgentLoop，或在输入完全不变时盲目重试。

**根因**：transport failure、model protocol failure、context overflow 和 tool failure 没有
分层；“再试一次”也没有要求输入或 provider 条件发生变化。

**当前规则**：HTTP 边界先分类；ToolCall 只做确定性、低歧义的格式修复，未知工具不会被猜测提升；
context overflow 只有在结构化压缩确实降低估算 token 后才重试；实际 fallback provider/model
进入 Usage 证据。

**代码与证据**：Model Gateway、[`StepController.model_failure`](../../agent_forge/runtime/control.py)
和 `tests/test_model_adaptation.py`。

**工程结论**：重试必须改变失败条件。不能改变输入、provider 或状态的重试只会增加成本和噪音。

### 6. 验证失败、工具失败和环境不可用被混为一种错误

**现象**：pytest 正常执行但断言失败、参数格式错误、缺依赖、进程启动失败都曾返回相同的
`success=false`，最终共同消耗连续工具失败预算。

**根因**：Observation 只表达成功或失败，没有表达执行器是否正常运行以及失败属于哪一层。

**当前规则**：测试断言失败是可恢复的业务证据，不累计工具故障；非法参数要求修正 schema；
依赖缺失归类为 `validation_environment_unavailable`；真正的网关、命令或工具异常才进入工具
故障预算。

**代码与证据**：[`StepController.classify_observation`](../../agent_forge/runtime/control.py)、
Diagnostics/Validation Tool、Failure Taxonomy 及其测试。

**工程结论**：失败处理首先要定位 failure domain。错误归因会让 Runtime 惩罚正确探索，或让
环境问题被误讲成模型推理问题。

### 7. 文件 Context 有预算，但完整会话仍然无限增长

**现象**：repository preview 已受限，完整模型请求仍被历史消息、ToolCall/Observation 和工具
schema 撑爆。简单删除旧消息后，模型又会忘记已经失败过的动作。

**根因**：预算只作用于 system context，没有治理真正发送给模型的完整 request；同时 working
memory、checkpoint、session digest 和 long-term memory 被混成一个“Memory”概念。

**当前规则**：`ContextWindowManager` 统一估算 system、history、tools 和输出预留。超过软阈值时，
在安全边界把旧历史压成 `SessionDigest`，保留最新原文；system message 必须保留，assistant
ToolCall 与对应 Observation 不得拆分，失败事实和 source hash 不得丢失，原始 Trace 不删除。

`4 chars/token` 只是调用前近似值，provider usage 才是事后权威数据；无法安全缩小时明确返回
`no_safe_compaction_boundary`，不能伪称已经满足窗口。

**代码与证据**：[`compaction.py`](../../agent_forge/context/application/compaction.py)、
[`test_context_window.py`](../../tests/test_context_window.py)。

### 8. 长期记忆有很多字段，却没有清楚的写入权限

**现象**：早期设计允许模型提议、证据绑定、评分和晋升长期记忆，但短任务很少走完生命周期，
操作者也无法确认是谁授权错误事实进入后续 Run。

**根因**：在稳定授权入口之前先做自动筛选，把“发现候选事实”和“允许跨 Run 生效”混在一起。

**当前规则**：长期记忆只接受用户显式 `remember/forget/list`；支持 user/project scope 和同 key
revision，项目值覆盖用户默认值。每个 Run 只在开始时召回一次并冻结快照，模型不能自动写入。
Working Memory 只属于当前 Run，SessionDigest 只是压缩视图，二者都不是长期事实源。

**代码与证据**：[`LongTermMemoryService`](../../agent_forge/context/application/memory_service.py)、
[`JsonLongTermMemoryRepository`](../../agent_forge/context/adapters/memory_json.py)、
[`test_long_term_memory.py`](../../tests/test_long_term_memory.py)。

**工程结论**：长期记忆首先是授权、作用域和可复现性问题，相关性评分是后续优化，而不是写入权。

### 9. `ask_human` 看起来存在，运行却没有真正暂停

**现象**：早期 `ask_human` 直接返回模拟 Observation，模型可在无人回答时继续执行。同一模型响应
如果同时包含写操作和 `ask_human`，写操作还可能先发生。

**根因**：把人工交互实现成普通工具，而不是 control-plane state transition。

**当前规则**：只要本轮存在 `ask_human`，它成为同轮屏障，其他 ToolCall 不执行。问题、状态和
Checkpoint 原子落盘，Run 进入 `WAITING_HUMAN` 并退出；回答通过 continuation 注入模型可见任务，
再由新的 Run 恢复。Informational response 与 side-effect approval 使用不同 store。

**代码与证据**：[`RunLifecycle`](../../agent_forge/runtime/application/run_lifecycle.py)、
Human Input Domain/Adapter、[`test_human_input.py`](../../tests/test_human_input.py)。

**边界**：当前是安全边界上的协作式暂停，不会强制中断正在进行的 HTTP 请求。

### 10. 人已经批准，恢复后却可能执行另一项操作

**现象**：批准前后的工具名相同，但参数、workspace 或目标文件状态可能已经变化。若只保存
`approved=true`，旧批准可能被套到新的写入上。

**根因**：审批缺少稳定的操作身份和目标状态绑定。

**当前规则**：

- `operation key` 标识“本次准备执行的工具、规范化参数、动作和 workspace”是否仍是批准时的
  同一项操作。
- `pre-execution fingerprint` 标识目标文件内容与元数据是否仍是批准时看到的状态。
- 任一不一致都 fail closed，要求重新检查和批准。

**代码与证据**：[`OperationTracker.build_operation_intent`](../../agent_forge/runtime/application/operation_tracker.py)、
Approval Domain/Adapter、[`test_human_approval.py`](../../tests/test_human_approval.py)。

**工程结论**：审批授权的是一个确定动作作用于一个确定状态，不是永久开放某个工具名。

### 11. 写操作发生后进程崩溃，Resume 无法判断是否应该重放

**现象**：文件已经修改，但进程在工具返回与 Ledger 记录完成之间退出。若恢复逻辑把“没有
executed”理解成“没有执行”，同一副作用会再次发生。

**根因**：副作用不是数据库内的一次原子事务。文件写入、外部命令或 API 调用与本地 Ledger
落盘之间存在无法消除的崩溃窗口。

**当前规则**：Operation Ledger 在越过真实工具边界前先记录 `executing`。恢复看到
`executing` 时返回 `operation_outcome_unknown` 并阻断，不猜测、不自动重放；只有状态和
post-fingerprint 都匹配的 `executed` 事实才可直接回放为成功 Observation。

**代码与证据**：[`OperationTracker.resolve_existing_operation`](../../agent_forge/runtime/application/operation_tracker.py)、
Operation Ledger Adapter、[`test_operation_ledger.py`](../../tests/test_operation_ledger.py)。

**边界**：这是保守的 at-most-once 倾向，不是 exactly-once。跨系统副作用仍需要目标系统幂等键、
事务或补偿机制。

### 12. 只有最终 Summary，进程硬中断后无法恢复已完成工作

**现象**：Fanout 中 task A 已完成、task B 未完成，但进程在最终 summary 前退出；恢复时无法确认
A 的 patch 是否完整、是否仍对应同一个 plan/base commit。

**根因**：Checkpoint 只在终态生成，而且没有 artifact integrity 和执行身份。

**当前规则**：运行前和每个 batch 后原子更新 Checkpoint，记录 plan digest、base commit、已接受
task、artifact 路径和 SHA-256。恢复先在 fresh validation worktree 验证并重放已完成 artifact，
再只运行未完成 task；身份或 hash 不匹配立即拒绝。

**代码与证据**：Live Fanout、Fanout Files Adapter、[`test_live_fanout.py`](../../tests/test_live_fanout.py)。

**工程结论**：可恢复的最小单元是“可验证 artifact + checkpoint”，不是一个 `resume=true` 参数。

### 13. 有线程池和回调，却没有真实 Multi-Agent

**现象**：早期 Fanout 只能调度 callback，没有独立模型上下文、工具策略、Trace、Usage、Workspace
和 candidate diff；多个 worker 也可能读写同一目录。

**根因**：只实现了并发调度，没有接入真实 AgentLoop 和副作用隔离。

**当前规则**：每个 worker 使用独立 worktree、ModelPort、filtered tools、AgentLoop、Trace、Usage
和 artifact。Plan 在执行前校验依赖、write scope、工具和预算；执行后再用真实 touched files
核对声明范围。声明重叠会被串行化，未声明的真实越界或冲突 fail closed。

候选改动的定义由共享 Git workspace 逻辑统一，tracked 和 untracked 文件都必须进入 scope、merge
和最终 diff。

**代码与证据**：Live Fanout Coordinator、Multi-Agent Domain/Adapter、
[`test_live_fanout.py`](../../tests/test_live_fanout.py)。

**边界**：当前主链接收显式 FanoutPlan；自动 LLM 拆解和自动冲突消解不是已实现能力。

### 14. Worker 都完成了，最终结果仍可能不可信

**现象**：各 worker 分别通过，但合并后可能产生语义冲突；Finalizer 在错误 workspace 运行时看不到
integrated diff，或验证过程自身污染候选状态。Verifier 的 `PASS` 还曾覆盖“仅候选改动”的结论边界。

**根因**：把局部完成等同于整体正确，并把 Finalizer 当作报告附属步骤，而不是独立的 correctness gate。

**当前规则**：候选改动合并后，由隔离 Finalizer 只读检查 integrated workspace；运行前后比较完整
diff，Finalizer 发生改动则阻断。它的模型调用、耗时和成本进入总 Usage。Local PASS、candidate
patch 和 official resolved 分别报告。

**代码与证据**：Multi-Agent Coordinator、Live Fanout 与
[`test_multi_agent_coordinator.py`](../../tests/test_multi_agent_coordinator.py)。

**工程结论**：Multi-Agent 的吞吐优化可以启发式，最终合并、范围校验和结果发布必须是确定性的
correctness gate。

### 15. Failure Taxonomy 先命中宽泛症状，优化方向被带偏

**现象**：真实 blocker 是 pending ToolCall、provider transport failure 或验证环境不可用，旧分类
却因为 `selected_files=0` 报成 context retrieval miss。

**根因**：分类顺序没有表达证据权威性和具体性；展示层又把标签当成事实，而没有记录命中规则。

**当前规则**：先处理 official evaluator、runner/environment 和 local/candidate evidence，再分析
Runtime 协议、Context 与工具症状；第一条被证据满足的规则产生唯一分类，并记录 taxonomy version、
rule id、source 和人工复核边界。

**代码与证据**：[`classify_case_result`](../../agent_forge/bench/domain/failure_taxonomy.py)、
[`test_failure_taxonomy.py`](../../tests/test_failure_taxonomy.py)。

**工程结论**：Taxonomy 可以用确定性规则，因为它是在既有结构化证据上做可复现归类；
`official_resolved` 必须来自官方 per-case artifact，不能由规则、Reviewer 或 LLM 猜测。

### 16. 进程返回 0，却不代表 Agent 已经完成任务

**现象**：CLI 正常退出并写出 artifact，但 Run 可能处于 blocked、waiting、pending ToolCall 或只有
未验证 candidate patch。只检查 shell exit code 会形成假阳性。

**根因**：process success、Runtime completion、candidate validation 和 official evaluation
被压成一个“成功”。

**当前规则**：自动化门禁读取本次 Run 的 machine-readable state、Trace、Usage、candidate diff、
local validation 和 official report；每层证据独立，不允许用较弱层级替代较强结论。

固定参考案例 `astropy__astropy-12907` 曾产生 506 字符 candidate diff，Reviewer/Verifier PASS，
但 official evaluation 未运行时只能称为 candidate。后续 official resolved 只能由实际 SWE-bench
per-case report 提升。

**代码与证据**：Bench Report、Case Study、Failure Taxonomy 与相应测试。

### 17. Usage 只记录成功调用，成本与预算被系统性低估

**现象**：provider error、overflow、repair retry 和 Finalizer 调用曾不进入 Usage；Run 成本还会被
最后一次调用覆盖，导致预算已经超限仍接受最终答案。

**根因**：Telemetry 放在成功分支之后，预算被当成单次调用属性而不是跨 Turn 累计状态。

**当前规则**：每次模型边界返回后立即记录 provider、model、tokens、latency、cost 和 error；成功、
失败、fallback、repair 与 Finalizer 都进入累计 Usage。每次调用后先检查预算，再决定继续执行工具、
重试或接收 final answer。

**代码与证据**：Observability Usage、Model Gateway、AgentLoop policy tests。

**工程结论**：失败调用也是实际资源消耗。成本、延迟和错误率必须覆盖完整运行角色，不能只统计
最后一次成功响应。

### 18. 收集 badcase 不等于形成数据飞轮

**现象**：系统能保存 Trace、Failure Class 和 regression tests，却回答不了“为什么采纳这次改进，
它改善了什么，又牺牲了什么”。单次数字还容易被写成普遍收益。

**根因**：反馈闭环停在收集和分类，没有显式记录改进假设、对照条件、结果差值和人工采纳决定。

**当前规则**：`improvement_record` 串联 source evidence、problem、diagnosis provenance、hypothesis、
change、paired regression cases、before/after 和 decision rationale。配对运行必须固定任务、模型、
预算、安全边界、Skill/Memory snapshot 等实验身份，只允许声明因素变化。

Smoke-5 是从 SWE-bench Verified 中分层选择的低成本机制回归集，用于发现 Harness 行为回归；它不是
随机样本，也不能作为 500 题总体解决率。少量 commissioning run 只能说明已运行槽位的事实。

**代码与证据**：Benchmark Campaign、Evaluation Comparison、
[`test_benchmark_campaign.py`](../../tests/test_benchmark_campaign.py) 和
[`test_evaluation_comparison.py`](../../tests/test_evaluation_comparison.py)。

**工程结论**：数据飞轮的最小闭环是“问题 -> 可复核诊断 -> 改进假设 -> 配对实验 -> 人工决策”，
不是“每次失败新增一个 UT”。

### 19. Trace 中出现了能力名，但它没有改变任何 Runtime 行为

**现象**：早期 `SimplePlanner` 会写入 `react/plan_execute` 事件，但计划既不进入模型上下文，也不
限制工具、更新状态或改变终止条件；页面看起来具备 Planning，删除它却不影响运行结果。

**根因**：把描述性事件当成实际能力，形成装饰性 observability。

**当前规则**：删除无行为 owner 的 Planning 标签。Single Agent 明确是受治理 ReAct loop；复杂并行
只展示真实 FanoutPlan 及其 dependency、scope、budget、digest 和恢复证据。

**工程结论**：Trace 只能记录已经发生的事实。一个能力若不改变输入、状态、权限、执行或结果，
就不能仅凭事件名称对外宣称存在。

### 20. Campaign 完成不等于所有槽位都产生了可裁决证据

**现象**：50×2 实跑中，provider transport 和 official evaluator 环境错误被保存成普通
`completed` 槽位；报告又用 `resolved / official_evaluated` 展示“Official resolved”，容易把
补丁接受率误读成整个固定样本的解决率。

**根因**：Campaign 只区分“runner 是否返回”，没有区分稳定 Agent 结果与基础设施失败；同一个字段
还承担了两个不同分母。

**当前规则**：provider、official evaluator 或 runner 基础设施错误最多自动重试一次，并保存首次尝试；
重试耗尽后仍保留在样本中，但从可裁决 pair 中单独排除。主指标固定为 `resolved / 全部预注册 Case`，
`resolved / 已得到 official verdict 的 patch` 只能称为“已评测补丁接受率”。

**真实证据**：A 分片共 100 个槽位，3 个基础设施异常触发重试，1 个 provider error 在第二次仍失败；
最终只裁决 49 个 pair。总估算执行成本包含首次失败尝试，而不是只统计最终槽位。

**工程结论**：评测系统必须把任务失败、基础设施失败和缺失证据分开；分母命名错误会比计算错误更容易
制造误导性结论。

### 21. 本地测试通过不一定存在“已验证候选”

**现象**：一个 Case 没有生成 Diff，但原仓 local validation 通过，旧规则将它分类为
`locally_verified_candidate`；另一个 Case 已被 official evaluator 拒绝，却因日志中有
`missing dependency` 被更早归类为本地环境不可用。

**根因**：分类顺序没有真正落实“权威结果优先”，且 local pass 分支没有检查 candidate 是否存在。

**当前规则**：official resolved/error/failed 先于 provider、runner 和 local environment；只有
`patch_chars > 0` 时 local pass 才能形成 `locally_verified_candidate`。未改代码但测试通过单独归为
`local_validation_passed_without_patch`。

**工程结论**：Taxonomy 是有序裁决规则，不是关键词标签集合。每个成功语义都必须同时满足它所声称的
证据前提。

### 22. Tool/Skill 治理让运行更整洁，却降低了任务解决率

**现象**：固定 A 分片 50×2 中，Governed Runtime 的失败工具调用从 36/993 降到 14/973，成本也
更低，但 official resolved 从 Minimal 的 20/50 降到 14/50；candidate patch 从 32 降到 27。

**最初误判**：只看失败工具调用率，会认为 task-aware routing 与 Skill 已经改善 Runtime。Official
结果排除了这个判断：治理减少了错误动作，也可能剪掉必要能力或使模型过早收敛。

**根因证据**：每题自动叠加 3 张通用 Skill，平均占约 2,581 字符，`docs_update` 还误入 33/50；
`grep`/`grep_search` 是同义工具；SWE-bench 路由隐藏了异构仓库需要的受限验证回退。Governed 在
真正编辑的 Case 中更早修改，但 no-patch Case 更多，已生成 patch 的 official 接受率也更低。

**v2 结果**：在 7 个高差异 A 分片开发样本上，Governed 从 1/7 回升到 3/7，但仍低于 Minimal
的 6/7。剩余失败包括命中目标文件却不编辑、语义影响面漏改，以及 official 已 resolved 但 Runtime
仍以 pending ToolCall 阻断。方向有效，但不能据此冻结候选并盲跑 B。

**当前不变量**：自动选择一个主 Skill；Skill 只提供短认知策略，不拥有工具、权限或预算；模型目录
只暴露 canonical schema；`create_file` 只补齐新文件能力，不能覆盖已有内容；最后一个工具轮仍保留
读取错误源码的恢复能力，最终回答轮则对所有配置严格零工具。structured/text ToolCall 使用同一停止
语义，Cohort 的 dataset revision 必须进入真实加载调用。

**验收边界**：A 是开发证据；冻结候选后只用未见 B 分片做一次 50×2 验收。若 B 仍失败，保留负
结果并换新 holdout，而不是反复调同一批题直到获胜。完整分析见
[`governed-runtime-optimization.md`](governed-runtime-optimization.md)。

**工程结论**：Harness 不能把“工具失败更少”当成“任务完成得更好”。治理必须同时守住任务可达性，
并联合观察 resolved、candidate reachability、patch acceptance、失败工具调用和成本。

## 统一复盘模板

以后出现新问题，只使用下面六问判断是否值得进入本文件：

1. **触发现象**：用户或 Runtime 实际看到了什么错误行为？
2. **错误归因**：最初最容易误判成什么，哪条证据排除了它？
3. **根因边界**：问题属于模型、Context、Tool、Policy、State、Environment 还是 Evaluation？
4. **系统不变量**：修复后哪条规则必须始终成立？
5. **回归证据**：哪个最小测试或真实 Run 能证明故障不会回来？
6. **当前边界**：方案没有解决什么，下一层生产化需要什么？

如果一个问题只能回答“页面更好看了”“脚本能启动了”或“名称更清楚了”，它不属于这份文档。

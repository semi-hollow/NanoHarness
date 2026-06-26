# Agent Engineer 面经逐题复习卡

用途：把常见高压问题压缩成可背、可展开、可映射到 Agent Forge 的回答框架。

回答总原则：

1. 先讲工程风险：为什么这个问题在真实系统里重要。
2. 再讲设计取舍：为什么不是全靠 prompt，也不是无脑堆 Agent。
3. 然后映射项目：说出 Agent Forge 里对应模块。
4. 最后讲边界：哪些是本项目已实现，哪些是平台化生产系统才会完整做。

## 项目覆盖与必要补充

| 类型 | 状态 | 应对方式 |
| --- | --- | --- |
| AgentLoop、上下文工程、工具调用、安全边界、trace/usage | 已实现 | 作为项目主线重点讲。 |
| SWE-bench 闭环、失败归因、报告卡 | 已实现 | 用来证明不是纯 demo。 |
| Skill 版本、依赖、权限、回滚元数据 | 已补基础实现 | 讲 `SkillRegistry`，边界是还没有线上 canary 服务。 |
| 稳定 JSON 输出、schema 校验、repair prompt | 已补基础实现 | 讲 `StructuredOutputParser`，边界是后续可接 provider 原生 structured output。 |
| 长任务恢复、idempotency、防重复副作用 | 部分实现 | 有 task state/trace，完整事务补偿仍是生产化扩展。 |
| 多 Agent 分布式协作 | 文档应对 | 本项目不把它作为主线，避免 toy 多 Agent。 |
| 自演进、离线 RL、在线优化 | 文档应对 | 讲安全闭环，不把训练系统硬塞进 CodingAgent harness。 |
| C 端搜索、客服、多租户平台 | 系统设计准备 | 适合面试系统设计，不适合全部塞进本 repo。 |

## 一、Agent 框架与架构

### Agent Loop 的具体职责？

- 核心回复：AgentLoop 是 runtime 控制平面，负责上下文组装、模型调用、tool call 解析、权限检查、工具执行、observation 写回、trace/usage、停止条件。
- 面试官想听：模型只负责提出动作，runtime 负责可控性、审计、失败恢复和停止。
- 项目映射：`agent_forge/runtime/agent_loop.py`、`control.py`、`observability/trace.py`。

### 为什么要多 Agent？单 Agent + Skills 不够吗？

- 核心回复：多 Agent 用于角色隔离、并行探索、互相验证和上下文隔离；但如果任务主链路是代码修复，单 Agent + 工具治理通常更可控。
- 面试官想听：不要为了概念而多 Agent，要讲收益大于通信成本时才引入。
- 项目映射：Agent Forge 主线故意用单 Agent harness，避免线性假多 Agent；多 Agent 作为设计边界讲。

### 什么任务做成 Chatbot，什么做成 Workflow，什么必须做成 Agent？

- 核心回复：Chatbot 适合问答；Workflow 适合路径确定的流程；Agent 适合目标明确但路径不确定、需要探索和工具调用的任务。
- 面试官想听：确定性节点优先 workflow，开放式局部节点才用 Agent。
- 项目映射：SWE-bench 修复属于 Agent，因为需要读代码、定位、修改、验证。

### 复杂 Agent 为什么要把复杂度从 prompt 转移到 runtime？

- 核心回复：prompt 是软约束，runtime 是硬边界；安全、预算、超时、权限、审计、回放必须由代码保证。
- 面试官想听：不能靠一句“不要删除文件”防生产事故。
- 项目映射：`WorkspaceSandbox`、`CommandPolicy`、`PermissionPolicy`、`TraceRecorder`。

### 多步任务如何保存中间状态、处理用户打断和任务恢复？

- 核心回复：拆成 conversation state、task state、artifact state、audit state；每一步写 checkpoint 和 trace。
- 面试官想听：恢复时要带 run_id、step、工具副作用记录和幂等标记，不能重复执行危险操作。
- 项目映射：`.agent_forge/runs/`、`runtime/task_state.py`、`trace.json`；完整断点续跑仍是扩展。

### 长链路任务失败后，如何恢复现场并避免重复执行？

- 核心回复：失败分类、保存 observation、记录已执行 tool call、对非幂等操作使用 idempotency key 或人工确认。
- 面试官想听：恢复不是重新跑一遍，而是从安全 checkpoint 继续。
- 项目映射：trace/usage/failure diagnosis 已有；事务补偿未完全实现。

### Agent Loop 什么时候应该停止？由模型判断还是 runtime 判断？

- 核心回复：共同决定。模型可以给 final answer，但 runtime 必须检查 max steps、timeout、重复调用、权限阻断、预算和连续失败。
- 面试官想听：停止条件不能交给模型自觉。
- 项目映射：`runtime/stop_condition.py`、`runtime/control.py`。

### 框架中怎么处理记忆与上下文？用户教 Agent 知识，Agent 怎么记住？

- 核心回复：短期消息窗口、任务摘要、长期记忆和检索上下文分层；写长期记忆要带来源、时间、置信度和冲突策略。
- 面试官想听：不是把所有历史塞进 prompt，而是按任务相关性和权限加载。
- 项目映射：`context/memory.py`、`memory_policy.py`、`context_builder.py`；长期记忆治理仍是扩展。

### 记忆的优先级是什么？用户纠正和系统知识冲突怎么办？

- 核心回复：system policy > business rule > 当前用户指令 > 用户纠正 > task state > 长期记忆 > 检索知识 > 模型先验。
- 面试官想听：用户纠正可以覆盖旧记忆，但不能覆盖安全/合规规则。
- 项目映射：文档和 memory policy 可解释，完整冲突仲裁未产品化。

### 上下文压缩策略分几层？压缩后丢了关键信息怎么办？

- 核心回复：滑动窗口保近期交互，摘要保决策，检索保事实，artifact 保关键文件和工具结果；压缩后要保留不可丢的 anchors。
- 面试官想听：压缩是可审计策略，不是简单截断。
- 项目映射：`context_strategy.py`、`token_budget.py`。

### 记忆之间怎么分层？

- 核心回复：session memory、task memory、user profile memory、domain knowledge、tool observation cache 分层隔离。
- 面试官想听：不同生命周期、权限和可信度的记忆不能混在一起。
- 项目映射：基础 memory/context 已有，分布式 memory store 未实现。

### 你们怎么做 compact？不同模型之间如何做适配？上下文缓存怎么控？

- 核心回复：稳定 system prefix、动态任务摘要、文件片段检索、tool schema 路由；不同模型按 context window、tool schema 支持和价格配置 profile。
- 面试官想听：prefix 稳定利于缓存，动态部分尽量后置。
- 项目映射：`models/profile.py`、`context/token_budget.py`、`usage_report.py`。

## 二、Tool Calling 与 Skill

### 大模型推理时，tool 是怎么表示的？

- 核心回复：tool 是结构化 schema：name、description、arguments、required；模型输出 tool_call，runtime 校验再执行。
- 面试官想听：tool call 是协议边界，不是自然语言字符串。
- 项目映射：`tools/base.py`、`tools/registry.py`、`runtime/tool_call.py`。

### tool call 失败、超时或返回异常，框架怎么处理？

- 核心回复：先分类：参数错、未知工具、权限拒绝、命令失败、外部超时、重复调用、非幂等副作用；不同失败走不同恢复策略。
- 面试官想听：不是统一 retry，尤其非幂等操作不能自动重放。
- 项目映射：`runtime/control.py`、`safety/permission.py`、`tools/registry.py`。

### Skill 的版本升级、依赖管理和回滚怎么做？

- 核心回复：Skill manifest 记录 name/version/schema/permissions/dependencies/owner/rollback_to；发布前跑 regression，线上 canary，指标差回滚。
- 面试官想听：Skill 是受治理的产品能力，不只是函数。
- 项目映射：`agent_forge/skills/registry.py`、`skill_registry.example.json`。

### Skill 自演进更新后，怎么保证新版本不会比旧版本更差？

- 核心回复：固定回归集、shadow/canary、关键指标对比、失败归因和自动回滚阈值。
- 面试官想听：自演进必须被 benchmark 和发布系统约束。
- 项目映射：SWE-bench regression 已有；Skill 自动 canary 未实现。

## 三、自演进与评测

### Agent 自演进如何实现？框架的自演进能力具体体现在哪？

- 核心回复：trace/badcase -> 失败归因 -> 候选 prompt/tool/context 策略 -> regression -> canary -> rollback。
- 面试官想听：安全自演进不是让 Agent 线上直接改自己。
- 项目映射：failure diagnosis、regression set、usage report 已有；自动生成策略未实现。

### 竞品的自演进能力和你们差在哪？

- 核心回复：成熟平台通常有在线反馈、自动标注、回归趋势、灰度发布；本项目聚焦 CodingAgent runtime 和 benchmark 闭环。
- 面试官想听：知道差距，不硬吹。
- 项目映射：可用 SWE-bench 讲闭环，用文档讲平台化扩展。

### 离线 RL 怎么做？数据怎么来的？在线优化时怎么打分？

- 核心回复：数据来自成功/失败 trajectory、人工标注、工具结果、用户反馈；离线优化 reward model 或 policy；在线用任务成功率、成本、延迟、安全违规率打分。
- 面试官想听：训练数据要可追溯，不能直接把 noisy trace 喂进去。
- 项目映射：trace 可提供数据源；训练系统不纳入本项目。

### 如果在线优化效果不好，怎么做到回退？

- 核心回复：版本化 prompt/skill/tool policy，灰度发布，保留基线指标和回滚开关。
- 面试官想听：每次优化都要可对比、可回滚。
- 项目映射：Skill rollback 元数据已补；线上流量回滚未实现。

### 自演进有没有失控风险？如何做版本管理和 regression benchmark？

- 核心回复：有。必须限制改动范围、人工审批高风险策略、固定 regression、输出 diff 和指标对比。
- 面试官想听：自演进的核心是治理，不是“自动变聪明”。
- 项目映射：`forge bench swebench --regression-set core`。

### 如何做评测集？如何做提示词自优化？

- 核心回复：按真实场景收集任务，覆盖成功、失败、边界、安全；提示词优化必须先在固定集上 A/B，再进灰度。
- 面试官想听：评测集要防数据泄漏和只优化单一指标。
- 项目映射：SWE-bench Lite/core set 已作为外部闭环。

### Agent 的评测为什么不能只看最终成功率？

- 核心回复：还要看工具成功率、重复调用率、上下文命中、成本、延迟、安全违规、失败可诊断性。
- 面试官想听：Agent 是过程型系统，trajectory 很重要。
- 项目映射：`usage_report.py`、`trace.json`、`diagnostics.py`。

### trajectory 包含哪些信息？怎么用它做归因和优化？

- 核心回复：包含 step、prompt/context、tool call、observation、latency、tokens、error、final status；用来定位是检索错、模型错、工具错还是验证错。
- 面试官想听：trajectory 是优化数据，不只是日志。
- 项目映射：`.agent_forge/runs/*/trace.json`。

### 如何评估 AI 对话/AI 搜索产品的答案质量？

- 核心回复：事实正确性、引用支持、相关性、完整性、时效性、安全性、用户满意度和后续行为。
- 面试官想听：搜索/对话不能只看 BLEU 或主观好不好。
- 项目映射：本项目是 CodingAgent，不实现 C 端搜索评测；作为系统设计题准备。

## 四、多 Agent 协作

### 多 Agent 的通信范式是什么？怎么实现的？

- 核心回复：常见是 shared state、message passing、blackboard、supervisor-worker；消息要有 schema、任务 id、权限和上下文裁剪。
- 面试官想听：通信是协议，不是几个 prompt 互相说话。
- 项目映射：本项目不把多 Agent 作为主线。

### 多 Agent 有没有考虑通信成本和决策成本？

- 核心回复：要考虑。多 Agent 会增加 token、延迟、状态一致性和冲突解决成本。
- 面试官想听：多 Agent 不是免费提升效果。
- 项目映射：解释为什么主线选择单 Agent + 工具治理。

### 你们的多 Agent 是否支持分布式？

- 核心回复：如果做分布式，需要队列、状态存储、租户隔离、幂等、超时和 tracing；本项目不做分布式。
- 面试官想听：不要假装本地 demo 是分布式系统。
- 项目映射：文档应对。

### 多 Agent 协作时，如何避免互相抢状态死锁或重复执行？

- 核心回复：任务 owner、状态锁、lease、幂等 key、冲突检测、supervisor 仲裁和超时回收。
- 面试官想听：状态一致性比角色 prompt 更关键。
- 项目映射：本项目可借 task_state/trace 思路回答，未实现分布式锁。

## 五、性能与成本优化

### Agent 的性能调优怎么做？性能问题具体是什么？怎么定位和解决？

- 核心回复：拆解模型调用、检索、工具执行、外部服务、编排开销；用 trace/usage 找慢 step，再优化 context、并行工具、缓存和模型选择。
- 面试官想听：先观测再优化。
- 项目映射：usage report、timeline、latency 统计。

### Agent 哪个环节算力消耗最大？吞吐瓶颈在哪？

- 核心回复：通常模型推理和长上下文最贵；高频工具场景外部 API/IO 也可能是瓶颈。
- 面试官想听：不同场景瓶颈不同，要用数据说话。
- 项目映射：usage/cost/context breakdown。

### 如何控制大模型调用成本和延迟？

- 核心回复：模型路由、上下文裁剪、稳定 prefix 缓存、工具路由、早停、小模型预判、批量/并行外部调用。
- 面试官想听：不是只换便宜模型。
- 项目映射：`ToolRouter`、`ContextStrategy`、`ModelGateway`、`usage_report.py`。

### 如果模型效果提升和算力成本上升同时发生，怎么判断方案是否值得上线？

- 核心回复：看单位成本收益：成功率提升、人工节省、风险下降、延迟影响、用户体验和业务价值。
- 面试官想听：用 ROI 和 guardrail 指标决策。
- 项目映射：benchmark result card 可作为基础。

### system prompt 有没有考虑前缀稳定？为什么要前缀稳定？怎么做？

- 核心回复：稳定 prefix 有利于 prompt cache、降低成本并减少行为漂移；把系统规则和 JSON 指令固定，动态任务放后面。
- 面试官想听：了解缓存和 prompt 版本治理。
- 项目映射：`PromptRegistry`、`StructuredOutputParser.json_instructions()`。

## 六、安全与风控

### 安全护栏机制怎么设计？如何避免 Agent 跑偏、卡死或无限循环？

- 核心回复：输入/输出 guardrail、工具权限、sandbox、命令策略、最大步数、重复调用检测、预算和 human-in-the-loop。
- 面试官想听：安全是 runtime 多层防线。
- 项目映射：`safety/*`、`runtime/control.py`。

### System prompt 和 runtime guardrail 的边界在哪里？

- 核心回复：prompt 表达行为偏好，runtime enforce 硬约束。
- 面试官想听：关键约束必须靠代码。
- 项目映射：`CommandPolicy` 不依赖模型自觉。

### 哪些约束必须靠代码实现，不能只靠提示词？

- 核心回复：权限、路径隔离、危险命令、成本预算、超时、审计、非幂等操作审批、数据脱敏。
- 面试官想听：能列出真实事故边界。
- 项目映射：`WorkspaceSandbox`、`PermissionPolicy`。

### 如何防止 Agent 越权调用工具、泄露用户数据或执行高风险操作？

- 核心回复：工具白名单、schema 校验、权限 scope、sandbox、敏感路径拦截、审批和审计。
- 面试官想听：工具调用前必须过 policy。
- 项目映射：`ToolRegistry`、`PermissionPolicy`、`CommandPolicy`。

### 如何避免 AI 回答中的幻觉、过时信息和引用错误？

- 核心回复：检索增强、引用证据、生成后校验、反问澄清、不确定性表达、版本/时效元数据。
- 面试官想听：RAG 不能单独解决幻觉，还要 verification。
- 项目映射：CodingAgent 场景用文件 evidence、工具结果和测试验证。

## 七、Agent 落地场景

### 怎么理解智能客服里的 AI Agent？和传统 FAQ / 机器人客服有什么区别？

- 核心回复：FAQ 是检索答案，Agent 能理解意图、查订单、执行流程、处理异常并转人工。
- 面试官想听：Agent 的价值在端到端任务闭环。
- 项目映射：Skill Registry 示例用订单查询解释。

### 客户项目的端到端链路是什么？

- 核心回复：需求澄清 -> 数据/系统接入 -> 意图/流程设计 -> 工具治理 -> 评测集 -> 灰度 -> 监控 -> badcase 迭代。
- 面试官想听：懂交付闭环。
- 项目映射：本项目可类比 SWE-bench 闭环。

### 用户中途切换意图、补充信息或取消任务，系统如何处理？

- 核心回复：意图重判、计划修正、取消未执行子任务、保留可复用上下文、对副作用操作确认。
- 面试官想听：状态机和 Agent 计划要能重入。
- 项目映射：clarification/task_state 基础存在。

### 意图识别准确率大幅提升，你做了哪些关键改动？

- 核心回复：标签体系重构、样本清洗、难例补充、上下文特征、规则兜底、模型/提示词 A/B 和线上反馈。
- 面试官想听：不仅是换模型。
- 项目映射：非本项目主线，可作为业务系统经验回答。

## 八、系统设计与架构

### 面向 C 端用户的 AI 搜索/对话产品，端到端链路怎么设计？

- 核心回复：请求理解 -> 查询改写 -> 混合检索 -> rerank -> 证据聚合 -> 生成 -> 引用校验 -> 安全过滤 -> 反馈闭环。
- 面试官想听：搜索链路和对话状态都要讲。
- 项目映射：文档准备，不硬塞进 CodingAgent。

### AI 搜索助手和传统搜索产品的核心差异是什么？

- 核心回复：传统搜索返回链接，AI 搜索合成答案并承担事实性风险。
- 面试官想听：引用、可追溯和时效性是关键。
- 项目映射：类似 trace/evidence 思路。

### 用户问“帮我做一份三日游攻略”，系统应该如何拆解任务？

- 核心回复：澄清偏好/预算/城市 -> 搜索景点/交通/天气 -> 约束规划 -> 输出行程 -> 支持调整。
- 面试官想听：开放式任务先澄清，再分解，再工具执行。
- 项目映射：Plan-and-Execute 思路。

### 设计一个海外外卖业务的智能客服系统，整体架构怎么拆？

- 核心回复：渠道层、会话状态、意图识别、知识库、订单/支付/配送工具、Workflow、Agent 异常处理、人工转接、质检。
- 面试官想听：业务系统和 Agent runtime 分层。
- 项目映射：Skill Registry 可解释工具能力治理。

### 如何设计客服会话的状态存储？

- 核心回复：session、user profile、order context、workflow state、tool audit、人工接管状态分表/分层。
- 面试官想听：状态有生命周期和权限边界。
- 项目映射：task_state/trace 思路可复用。

### 流式对话场景下，SSE 和 WebSocket 怎么选？

- 核心回复：单向 token 流用 SSE 简单可靠；双向实时协作、多端控制用 WebSocket。
- 面试官想听：按通信方向和复杂度选型。
- 项目映射：UI 未实现流式，这是产品层扩展。

### DeepResearch 任务耗时很长，前后端、任务队列和模型调用如何协同？

- 核心回复：前端提交任务拿 job_id，后端队列异步执行，状态/进度可查，结果增量写入，失败可恢复。
- 面试官想听：长任务不能阻塞 HTTP 请求。
- 项目映射：本项目长任务由 run directory/trace 承载，未接队列。

### 如何设计 Trace，排查一次 AI 对话链路里的慢请求？

- 核心回复：每个 step 记录模型延迟、检索延迟、工具延迟、外部 API、token、cache、error code 和 correlation id。
- 面试官想听：trace 要能归因，不只是打印日志。
- 项目映射：`TraceRecorder`、usage report。

### 高峰期大量用户同时咨询，系统如何做限流、降级和容量规划？

- 核心回复：租户/用户限流、队列削峰、模型降级、缓存、非核心工具关闭、容量压测和 SLO。
- 面试官想听：效果、成本、稳定性一起考虑。
- 项目映射：平台化题，项目不实现。

### 线上 badcase 如何流转到标注、修复、验证和发布流程？

- 核心回复：badcase 采集 -> 脱敏 -> 分类 -> 标注 -> 加入回归集 -> 修策略 -> 评测 -> 灰度发布。
- 面试官想听：badcase 是系统资产。
- 项目映射：failure diagnosis + regression set。

### 发布新版本时，如何保证存量业务不受影响？

- 核心回复：版本化、回归测试、灰度、shadow、监控、自动回滚、向后兼容。
- 面试官想听：发布工程意识。
- 项目映射：Skill version 基础已补。

## 九、平台化与生产化

### 框架接入多个业务线，平台能力和业务定制能力怎么拆？

- 核心回复：平台提供 runtime、tool gateway、trace、权限、评测、发布；业务提供 prompts、skills、workflow、知识库和策略。
- 面试官想听：边界清晰，避免平台变成大杂烩。
- 项目映射：Agent Forge 是 runtime/harness 层。

### Tool、Skill、Workflow 如何注册、隔离和复用？

- 核心回复：Tool 是底层动作，Skill 是版本化能力，Workflow 是流程编排；用 registry 管理 schema、权限、owner、版本。
- 面试官想听：三者不是一个东西。
- 项目映射：`ToolRegistry`、`SkillRegistry`。

### 多租户场景下，如何做权限、配额、审计和数据隔离？

- 核心回复：tenant id 全链路透传，RBAC/ABAC、quota、独立密钥、数据分区、审计日志和脱敏。
- 面试官想听：多租户是安全边界，不只是字段过滤。
- 项目映射：不实现，系统设计回答。

### 如何设计一个 Agent 应用从开发、测试、灰度到发布的完整流程？

- 核心回复：本地开发 -> manifest/version -> regression -> shadow/canary -> 监控 -> rollback -> badcase 回流。
- 面试官想听：Agent 也需要软件工程发布链路。
- 项目映射：manifest/regression 已有基础。

### 大流量场景下，Agent 效果指标和系统指标分别怎么设计？

- 核心回复：效果看完成率、事实性、工具成功、安全违规；系统看 QPS、P95/P99、成本、错误率、队列堆积。
- 面试官想听：业务效果和系统稳定分开看。
- 项目映射：usage/report 可支撑局部。

### 如何把用户反馈、人工审核、工具执行结果转成可用训练或评测数据？

- 核心回复：脱敏、结构化、质量过滤、标注一致性、去重、版本绑定、训练/评测隔离。
- 面试官想听：数据治理意识。
- 项目映射：trace 是原始材料，训练数据管线未实现。

## 十、客户现场与商业化

### 讲一次到客户现场解决问题的经历。

- 核心回复：按“背景-冲突-定位-方案-结果-沉淀”讲，重点讲你如何把现场问题转成可复用能力。
- 面试官想听：不只是救火，而是沉淀平台能力。
- 项目映射：可用“从 toy demo 转为 SWE-bench 闭环”作为项目故事。

### 现场问题中怎么化解 gap 和矛盾？

- 核心回复：先确认业务目标和约束，再拆可交付版本，明确不能做的风险，给数据验证。
- 面试官想听：沟通和工程取舍能力。
- 项目映射：文档应对。

### Agent 商业化最大的难点是效果、成本、交付，还是客户认知？

- 核心回复：四者都有，但最大难点常是“稳定可控地达到业务效果”，随后才是成本和交付复制。
- 面试官想听：知道 Agent 商业化不是 demo 炫技。
- 项目映射：闭环评测是项目核心故事。

### 如果前期强依赖定制交付，后续怎么沉淀成可复制的平台能力？

- 核心回复：抽象公共 runtime、tool/skill registry、评测模板、权限审计、部署模板，业务差异留在配置层。
- 面试官想听：定制到平台的抽象能力。
- 项目映射：SkillRegistry 和 harness 分层。

## 十一、开源项目与质量保障

### 开源社区如何维护测试版本质量？

- 核心回复：CI、最小回归集、release checklist、兼容性说明、issue 模板、版本标签。
- 面试官想听：开源质量靠流程和自动化。
- 项目映射：verify/benchmark/doctor。

### 如何用 AI 辅助功能迭代和版本维护？

- 核心回复：AI 辅助生成 patch、更新文档、分析 badcase，但合并前必须跑回归和人工 review。
- 面试官想听：AI 提效不等于放弃工程准入。
- 项目映射：本项目本身就是 CodingAgent harness。

### 如何做 release 前的测试准入？如何设计回归测试？

- 核心回复：按风险分层：单测、集成、benchmark、兼容性、安全、成本/延迟；关键路径必须固定回归。
- 面试官想听：测试不是数量，而是覆盖核心风险。
- 项目映射：SWE-bench regression set。

### 开源社区里外部贡献的 PR 如何做质量把关？

- 核心回复：CLA/许可检查、CI、代码 review、安全扫描、行为变更说明、文档更新。
- 面试官想听：开源维护者视角。
- 项目映射：文档应对。

### 如果用户反馈某个版本 SDK 行为变化，你会怎么复现和定位？

- 核心回复：确认版本/环境/最小复现，比较 trace/log，git bisect 或回归集定位，给 workaround 和修复计划。
- 面试官想听：定位路径清晰。
- 项目映射：trace/replay/report。

### 开源策略是什么？全部开源还是核心闭源？

- 核心回复：基础 runtime、协议、SDK 可开源；业务数据、私有工具、评测集敏感部分可闭源或脱敏。
- 面试官想听：理解生态和商业边界。
- 项目映射：Agent Forge 可作为开源 runtime/harness。

## 十二、团队协作与行为题

### 团队有多少人？有比你资深的吗？你怎么推动/协调？

- 核心回复：讲清角色、你负责的模块、如何对齐设计、用数据/原型推动决策。
- 面试官想听：协作成熟度，不抢功。
- 项目映射：用项目模块拆分讲 ownership。

### 你怎么让更资深的同事接受你的技术判断？

- 核心回复：先理解对方顾虑，用小实验、指标和风险清单证明，不靠争论。
- 面试官想听：影响力和工程判断。
- 项目映射：用 benchmark/trace 说服。

### 多人协作怎么保持架构一致？多人 AI Coding 怎么保证架构稳定？

- 核心回复：架构边界文档、代码地图、PR checklist、模块 owner、回归集和自动化检查。
- 面试官想听：AI Coding 时代更需要架构守门。
- 项目映射：`agent_forge/README.md`、technical-defense docs。

### 介绍一个你主导的项目。

- 核心回复：一句话定位、问题、架构、关键设计、结果指标、反思。
- 面试官想听：你能把复杂项目讲成清晰闭环。
- 项目映射：Agent Forge 项目故事。

### 项目中遇到过什么比较难的问题？你怎么定位和解决？

- 核心回复：选上下文工程、工具失败恢复、SWE-bench 闭环或 DeepSeek 接入问题讲；展示 trace 证据。
- 面试官想听：真实排障过程。
- 项目映射：trace/usage/failure diagnosis。

### 你在项目中承担的职责和主要贡献是什么？

- 核心回复：主导 runtime 架构、上下文工程、工具治理、安全边界、评测闭环和文档体系。
- 面试官想听：贡献具体，不泛泛说参与。
- 项目映射：按模块讲。

### Release 时间很紧，你如何取舍？

- 核心回复：保核心链路和安全边界，砍非核心 UI/美化/低风险扩展；用灰度降低风险。
- 面试官想听：能做优先级判断。
- 项目映射：本项目保留轻 UI，重点放 benchmark/runtime。

### 遇到和同事意见不一致时怎么处理？

- 核心回复：先对齐目标和约束，列方案 tradeoff，用数据或小实验裁决，保留回滚。
- 面试官想听：冲突处理成熟。
- 项目映射：文档应对。

### 为什么换工作？为什么考虑这家？未来三到五年？

- 核心回复：围绕技术成长、Agent 工程化、业务落地影响力，不说负面情绪。
- 面试官想听：动机稳定、方向匹配。
- 项目映射：把 Agent Forge 作为主动学习和开源投入。

### 你怎么看 Agent 从工具到助手的长期演进？

- 核心回复：从单点工具调用走向长期状态、主动规划、可控执行和多系统协作；核心瓶颈是可靠性和成本。
- 面试官想听：趋势判断不空泛。
- 项目映射：runtime/trace/safety 是演进基础。

### 你怎么看开源生态和商业化能力之间的边界？

- 核心回复：协议/runtime/SDK 开源促进生态，业务数据、模型策略、行业交付闭源形成商业壁垒。
- 面试官想听：商业意识。
- 项目映射：Agent Forge 适合开源 runtime。

### ToB 场景和 ToC 场景最大的差异是什么？

- 核心回复：ToB 重权限、审计、确定性、交付和集成；ToC 重体验、规模、成本和个性化。
- 面试官想听：知道落地约束不同。
- 项目映射：本项目更偏 ToB/开发者工具。

## 十三、手撕代码

### 算法题怎么准备？

- 核心回复：反转链表、合并链表、最大子数组、无重复最长子串、LRU、相邻重复删除、最小覆盖子串都要能 15-25 分钟写完并解释复杂度。
- 面试官想听：基础编码稳定，不被 Agent 项目掩盖基本功。
- 项目映射：不放进项目，单独刷题。

### 用 unittest 写测试框架，对比两个文件夹差异？

- 核心回复：递归遍历相对路径，比较新增/删除/修改，文件内容可 hash；unittest 断言 diff 为空或输出报告。
- 面试官想听：工程题要注意二进制、大文件、忽略规则。
- 项目映射：可借 `git_diff` 和工具设计思想。

### 写 Shell 脚本统计目录下最大 10 个文件？

- 核心回复：`find DIR -type f -print0 | xargs -0 du -h | sort -hr | head -10`。
- 面试官想听：处理空格路径，别用脆弱循环。
- 项目映射：不放进项目。

### 写 Shell 脚本检测进程是否存活，不存活则拉起？

- 核心回复：`pgrep -f` 检测，失败后启动并记录日志；生产用 systemd/supervisor 更合适。
- 面试官想听：知道脚本和进程管理系统的边界。
- 项目映射：不放进项目。

### 递归比较两个目录，输出新增、删除、修改文件？

- 核心回复：构建相对路径集合，集合差得新增/删除，交集比较 hash/mtime/size 得修改。
- 面试官想听：复杂度和边界条件。
- 项目映射：可作为工具题练习。

### 手写一个查数据库的 Skill？

- 核心回复：定义 SkillSpec、输入 schema、权限、只读查询、超时、错误分类、脱敏输出。
- 面试官想听：DB Skill 不是直接拼 SQL，必须治理权限和参数。
- 项目映射：`skill_registry.example.json` 的 `customer_order_lookup`。

### 实现一个 Tool Router，根据工具名和参数分发调用？

- 核心回复：registry 保存 name->tool，router 根据任务/权限/风险筛选，调用前 schema 校验，失败返回 observation。
- 面试官想听：工具选择和工具执行是两层。
- 项目映射：`tools/tool_router.py`、`tools/registry.py`。

### 如何让大模型稳定返回 JSON？如果不合法如何校验、修复、重试？

- 核心回复：优先 tool call/structured output；其次固定 JSON schema 指令；runtime 抽取 JSON、schema 校验，失败生成 repair prompt，限制重试次数，仍失败返回结构化错误。
- 面试官想听：稳定 JSON 靠协议和校验，不靠祈祷模型听话。
- 项目映射：`runtime/structured_output.py`。

## 最值得优先补的能力清单

已在本轮补充：

- `SkillRegistry`：支撑 Skill 版本、权限、依赖、owner、rollback 的面试回答。
- `StructuredOutputParser`：支撑稳定 JSON、schema 校验、repair prompt 的工程题回答。
- `interview-question-bank-zh.md`：把问题、关键点和项目映射合在一起，方便复习。

后续如果继续补，优先级建议：

1. 长任务 checkpoint resume：让 `.agent_forge/runs/<run-id>` 真正支持从某一步继续。
2. 幂等与副作用治理：给写操作增加 operation_id、dry-run、approval、replay safety。
3. 回归趋势看板：把多次 SWE-bench regression 的通过率、成本、失败分类做趋势比较。
4. Structured output 接到 AgentLoop 的 plan/evaluator 节点：让 parser 参与真实运行链路。

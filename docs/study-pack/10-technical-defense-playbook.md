# 10 Technical Defense Playbook

这个文件是现场追问的回答骨架。回答时不要背定义，要绑定项目代码和运行证据。

## 项目介绍

可以这样开场：

> Agent Forge 是一个轻量 CodingAgent Harness。模型不是核心卖点，核心是让 LLM 在受控 runtime 里完成代码任务：ContextStrategy 决定模型看什么，ModelGateway 接真实模型，AgentLoop 做 ReAct 循环，ToolRegistry 管工具协议，Safety 层管权限和沙箱，Trace/Eval/UsageReport 把过程变成可审计证据。

主动引导到两个亮点：

- 上下文工程：不是把仓库全塞给模型，而是 repo map、检索、rank、文件预览、memory、topic shift 和 token budget。
- 执行控制：不是让模型自由执行，而是 StepController、ToolRouter、PermissionPolicy、CommandPolicy、EvidenceLedger 共同约束。

## 高频追问回答

**为什么用 ReAct？**

Coding 任务需要观察驱动：读文件后才能 patch，patch 失败后要 reread，测试失败后要根据 output 修复。ReAct 把 reasoning/action/observation 拆开，让 runtime 在每一步插入权限、沙箱、trace 和 recovery。

**什么时候不用 ReAct？**

固定链路用 workflow；复杂任务可先 plan_execute；纯解释问题可以 answer_only。代码里 `PlanningModePolicy` 记录这个判断，当前主循环仍以 ReAct 执行为主。

**用户意图模糊怎么办？**

`ClarificationPolicy` 先判断是否有不可 grounding 的指代，比如“按老样子处理一下”。如果缺目标就 ask，不让模型猜。普通 `fix` 不会过度澄清，因为 CodingAgent 可以通过 repo/tests 探索目标。

**上百个工具怎么选？**

工具不能全量塞进 prompt。`ToolRouter` 给工具打 capability/risk/latency/mode 标签，根据 task 选候选工具，并把 dropped tools 写进 trace。真实生产里可以把这个 router 接 BM25/embedding/规则/权限系统。

**tool schema 怎么减少误调用？**

Schema 要短、具体、参数名稳定；Registry 在执行前做 required/type validation，错误变成 Observation，StepController 决定是否可恢复。

**Memory 怎么设计？**

短期 observation 给下一轮用；summary 压缩旧观察；session seed 只在 topic 连续时继承。`MemoryRecord` 额外有 scope/confidence/TTL/source/agent_name，解决多用户、多 session、多 Agent 隔离。

**Prompt 存在哪里？**

不应该散落在代码里。项目用 `PromptRegistry` 管 name/version/purpose/content，ContextBuilder 引用 `agent_system@2026-06-core`。生产可替换成 prompt platform。

**如何防止模型幻觉说测试通过？**

OutputGuardrail 要求 final answer 包含未验证项；EvidenceLedger 收集 run_command/read_file/apply_patch 证据；usage report 展示 Evidence。没有测试 observation，就不能声称测试通过。

**Multi-Agent 怎么拆？**

Supervisor 负责阶段和质量门禁，子 Agent 负责局部任务。子 Agent 不直接面向用户，它们输出 artifact，Supervisor 根据测试、review、trace、ownership 决定 retry/done。

**子 Agent 幻觉怎么办？**

不信字符串，信证据。Tester 必须跑验证命令，Reviewer 看 diff，Supervisor 看 artifact + metrics + failed_tool_call_count。

**评测体系怎么做？**

不只看 pass rate。EvalRunner 输出 task_success/test_pass/safety_violation/tool count/trace count；Flywheel 按 capability 聚合 context/safety/tool/orchestration/coding_benchmark，并生成 badcase recommended action。

**数据飞轮怎么讲？**

线上 badcase -> 标注能力类别 -> 加 eval case -> 修 runtime/prompt/tool schema -> 跑 eval history 对比 -> 再进入回归集。项目里 `eval/flywheel.py` 是这个闭环的最小实现。

**怎么讲真实工程经验？**

打开 `docs/run-artifacts/webhook-deepseek/usage_report.md`，讲一次真实 run：LLM calls、input/output tokens、cache hit/miss、cost、latency、tool success rate、context breakdown。

**为什么不接 LangChain？**

本项目目标是学习 Harness 核心，不是调框架 API。自己实现最小 runtime 能把 tool protocol、context、permission、trace、eval 的边界讲清楚；生产里可以复用 LangGraph/CrewAI/OpenAI Agents SDK，但核心概念不依赖它们。

**RAG 怎么回答？**

当前项目是代码仓库检索，采用 repo_map + lexical retrieval + file_ranker + selected file preview。大规模文档时再扩展混合检索、父子 chunk、metadata、版本和有效期。

**并发和延迟怎么回答？**

当前本地 harness 是单任务可审计优先。生产优化路径是：read-only 工具并行、repo_map/file preview 缓存、工具网关限流、LLM streaming、长任务异步化、按风险分层审批。

**模型选型怎么回答？**

Mock 用于离线确定性验证，DeepSeek 用于个人真实模型跑通，OpenAI-compatible 接公司网关。ModelGateway 统一 retry/fallback/usage，业务代码不绑定具体供应商。

## 低频方向的边界回答

- 训练/SFT/RL：知道数据构造和 reward 方向，但当前项目是 Harness。
- 多模态：可扩展为多模态 tool 和异步任务，但当前聚焦代码文本。
- 端云协议：可讲本地隐私/低延迟与云端强模型权衡，但不在代码里做。
- 用户增长指标：可以讲 badcase 和反馈闭环，不编造 DAU。
- 手撕题：单独刷，不放进项目。

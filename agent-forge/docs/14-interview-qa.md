# 14-interview-qa

每条都按四段准备：面试官想看什么、推荐回答、本项目里的证据、可能追问。

### Q1：这个项目是什么？
**面试官想看什么：** 你是否能一句话讲清边界。  
**推荐回答：** Agent Forge 是一个标准库优先的 Agent Harness，重点是 loop、tool、safety、trace、eval，而不是模型训练。  
**本项目里的证据：** `agent_forge/runtime/agent_loop.py`、`agent_forge/tools/`、`agent_forge/eval/`。  
**可能追问：** 和 LangChain/LangGraph 有什么区别？

### Q2：V1 到 V2 最大变化是什么？
**面试官想看什么：** 你是否能讲项目演进。  
**推荐回答：** V1 证明可运行，V2 增加 real LLM 可选接入、context engineering、tool adapter、metrics、eval 扩展和面试文档。  
**本项目里的证据：** README V1/V2 matrix、`docs/20-resume-bullet-and-project-script.md`。  
**可能追问：** 为什么不是直接重写？

### Q3：为什么默认用 MockLLM？
**面试官想看什么：** 是否理解确定性测试。  
**推荐回答：** MockLLM 保证 demo、unittest、eval 离线稳定，真实 LLM 作为可选路径接入。  
**本项目里的证据：** `MockLLMClient`、CLI 默认 `--llm mock`。  
**可能追问：** 真实模型行为不稳定怎么办？

### Q4：OpenAI-compatible client 做了什么？
**面试官想看什么：** 真实 LLM 接入能力。  
**推荐回答：** 它从环境变量读取 base_url/api_key/model，用标准库发 chat completions 请求，并解析 content 和 tool_calls。  
**本项目里的证据：** `OpenAICompatibleLLMClient`、`docs/18-openai-compatible-client.md`。  
**可能追问：** 为什么不用官方 SDK？

### Q5：invalid response 如何处理？
**面试官想看什么：** 异常恢复设计。  
**推荐回答：** 不抛出到顶层，而是返回 `AgentResponse(error={"type":"invalid_response", ...})`，便于 fallback 和 trace。  
**本项目里的证据：** `tests/test_openai_compatible_llm.py`。  
**可能追问：** 如果 arguments 不是 JSON 怎么办？

### Q6：tool call parsing 支持哪些形态？
**面试官想看什么：** 兼容性。  
**推荐回答：** 支持 `message.tool_calls[].function` 和 legacy `message.function_call`，arguments 可为 JSON 字符串或对象。  
**本项目里的证据：** `OpenAICompatibleLLMClient._parse_tool_calls`。  
**可能追问：** 多 provider schema 不一致怎么抽象？

### Q7：AgentLoop 的核心流程是什么？
**面试官想看什么：** 基础架构理解。  
**推荐回答：** input guardrail -> LLM -> permission -> tool execution -> observation -> loop -> output guardrail -> final answer。  
**本项目里的证据：** `agent_forge/runtime/agent_loop.py`。  
**可能追问：** max_steps 有什么用？

### Q8：为什么需要 Observation？
**面试官想看什么：** 工具结果契约。  
**推荐回答：** Observation 统一封装 tool_name、success、content，让 agent loop 不依赖具体工具返回类型。  
**本项目里的证据：** `agent_forge/runtime/observation.py`。  
**可能追问：** Observation 是否应该结构化更多字段？

### Q9：ToolRegistry 的职责是什么？
**面试官想看什么：** 模块边界。  
**推荐回答：** 它负责注册工具、暴露 schema、按名字执行工具，并把未知工具转成失败 Observation。  
**本项目里的证据：** `agent_forge/tools/registry.py`、case_007。  
**可能追问：** 工具重名怎么办？

### Q10：unknown tool 怎么恢复？
**面试官想看什么：** tool hallucination 处理。  
**推荐回答：** Registry 不崩溃，返回 `unknown tool` Observation，LLM 可以据此重新规划。  
**本项目里的证据：** `eval_cases/case_007_unknown_tool_recovery/verify.py`。  
**可能追问：** 是否要把 unknown tool 计入 metrics？

### Q11：invalid tool arguments 怎么处理？
**面试官想看什么：** schema validation 意识。  
**推荐回答：** V2 adapter 对 required 字段做最小校验，缺失时返回失败 Observation。  
**本项目里的证据：** `MCPStyleToolAdapter.execute`、case_008。  
**可能追问：** 是否需要 JSON Schema 完整校验？

### Q12：为什么加 MCP-style adapter？
**面试官想看什么：** 扩展外部工具的思路。  
**推荐回答：** 它证明外部工具可以转成 Agent Forge 的 tool schema 和 Observation contract，而不改 agent loop。  
**本项目里的证据：** `agent_forge/tools/adapters/mcp_style_adapter.py`。  
**可能追问：** 和完整 MCP 协议差什么？

### Q13：这是不是完整 MCP？
**面试官想看什么：** 不夸大项目能力。  
**推荐回答：** 不是。V2 只做本地 adapter，不含 transport、JSON-RPC session、capability negotiation。  
**本项目里的证据：** `docs/21-mcp-style-tool-adapter.md`。  
**可能追问：** 后续怎么接真正 MCP server？

### Q14：工具安全最重要的点是什么？
**面试官想看什么：** production 风险意识。  
**推荐回答：** Agent 的风险集中在 tool execution，所以要有 permission、sandbox、command policy 和 approval。  
**本项目里的证据：** `agent_forge/safety/`。  
**可能追问：** prompt guardrail 为什么不够？

### Q15：sandbox 阻止了什么？
**面试官想看什么：** 文件边界。  
**推荐回答：** 路径必须在 workspace 内，且 `.env`、pem、key、credentials、secrets 等敏感路径拒绝。  
**本项目里的证据：** `WorkspaceSandbox.ensure_safe_path`、case_010。  
**可能追问：** symlink 怎么处理？

### Q16：command policy 为什么禁网络？
**面试官想看什么：** CI/PR bot 安全。  
**推荐回答：** curl/wget/ssh/scp 可能外传代码或密钥，默认禁止，生产中应走受控代理和审计。  
**本项目里的证据：** `command_policy.py`、case_011。  
**可能追问：** 依赖安装怎么办？

### Q17：为什么不用 shell=True？
**面试官想看什么：** 命令注入意识。  
**推荐回答：** shell=True 会扩大注入面；项目用 shlex split 后以 argv 方式运行。  
**本项目里的证据：** `RunCommandTool.execute`。  
**可能追问：** 管道命令怎么处理？

### Q18：human approval 在哪里体现？
**面试官想看什么：** 人机协作边界。  
**推荐回答：** 写入和 patch 可根据 policy 要求审批，trace 会记录 human_approval。  
**本项目里的证据：** `PermissionPolicy`、`AgentLoop`。  
**可能追问：** PR bot 中谁审批？

### Q19：output guardrail 防什么？
**面试官想看什么：** 防止最终答案幻觉。  
**推荐回答：** 防止没跑测试却声称测试通过，也要求说明未验证点。  
**本项目里的证据：** `output_guardrail`、case_009。  
**可能追问：** 如何检测更复杂的虚假声明？

### Q20：input guardrail 的局限是什么？
**面试官想看什么：** 是否知道规则法边界。  
**推荐回答：** 关键词规则可解释但覆盖有限，适合第一道门，不能替代权限和 sandbox。  
**本项目里的证据：** `input_guardrail`。  
**可能追问：** 能否接分类模型？

### Q21：Context Engineering V2 包含什么？
**面试官想看什么：** 不只是 RAG。  
**推荐回答：** repo_map、retrieved_docs、memory、selected_files、total_chars、truncated。  
**本项目里的证据：** `ContextBuildReport`。  
**可能追问：** token budget 怎么估？

### Q22：为什么不能把全仓库塞进 prompt？
**面试官想看什么：** 上下文预算意识。  
**推荐回答：** 成本高、噪音大、易截断；需要检索、排序和预算报告。  
**本项目里的证据：** `file_ranker.py`、`context_builder.py`。  
**可能追问：** 大仓库怎么办？

### Q23：repo_map 有什么价值？
**面试官想看什么：** 粗粒度上下文。  
**推荐回答：** 它给 agent 一个仓库导航图，先知道有哪些文件，再决定读哪些文件。  
**本项目里的证据：** `build_repo_map`。  
**可能追问：** 是否要包含文件摘要？

### Q24：file_ranker 如何排序？
**面试官想看什么：** 简单可解释的 retrieval。  
**推荐回答：** 根据任务词在路径和文件内容中的命中打分，并给 Python/test 文件轻量加权。  
**本项目里的证据：** `agent_forge/context/file_ranker.py`。  
**可能追问：** BM25/embedding 会不会更好？

### Q25：symbol_search 做什么？
**面试官想看什么：** 代码语义检索。  
**推荐回答：** 用标准库 AST 扫 `.py`，识别 `class`、`def`、`async def` 的名字和行号。  
**本项目里的证据：** `symbol_search.py`、case_013。  
**可能追问：** decorator、动态赋值怎么办？

### Q26：grep、symbol_search、LSP 区别？
**面试官想看什么：** 工具选择能力。  
**推荐回答：** grep 找文本，symbol_search 找 Python 符号，LSP 提供 definition/references/diagnostics。  
**本项目里的证据：** `docs/19-lsp-and-symbol-search.md`。  
**可能追问：** 为什么 V2 不直接接 LSP？

### Q27：为什么 V2 先做 symbol_search？
**面试官想看什么：** MVP 切分能力。  
**推荐回答：** 它零依赖、可测试、可 fallback，足以展示 symbol provider 的抽象方向。  
**本项目里的证据：** `tests/test_context.py`。  
**可能追问：** 接 pyright 需要什么？

### Q28：Memory 的作用是什么？
**面试官想看什么：** 状态管理理解。  
**推荐回答：** V2 只保留最近 N 条轻量记忆，用于说明短期偏好或任务上下文。  
**本项目里的证据：** `context/memory.py`。  
**可能追问：** 长期记忆怎么做？

### Q29：RAG 为什么这么简单？
**面试官想看什么：** 是否故意简化。  
**推荐回答：** 项目目标是 harness，不是检索系统；关键词 RAG 够支撑架构讲解和测试。  
**本项目里的证据：** `context/rag.py`。  
**可能追问：** 什么时候引入 embedding？

### Q30：context budget report 如何用于 debug？
**面试官想看什么：** 可观测性思维。  
**推荐回答：** 它告诉我选择了哪些文件、检索了哪些文档、是否截断、总字符数多少。  
**本项目里的证据：** `ContextBuildReport.render()`。  
**可能追问：** budget 失败时如何降级？

### Q31：Observability 记录什么？
**面试官想看什么：** 调试证据链。  
**推荐回答：** trace 记录 step、agent、event_type、success、duration、tool args、observation、guardrail。  
**本项目里的证据：** `TraceRecorder`。  
**可能追问：** 敏感信息怎么脱敏？

### Q32：metrics summary 有哪些指标？
**面试官想看什么：** 量化运行质量。  
**推荐回答：** tool_call_count、failed_tool_call_count、handoff_count、guardrail_block_count、approval_count、duration_ms。  
**本项目里的证据：** `observability/metrics.py`。  
**可能追问：** p95 latency 怎么做？

### Q33：failed_tool_call_count 的价值？
**面试官想看什么：** 能否定位 agent/tool 问题。  
**推荐回答：** 它能区分任务失败是模型规划错、工具参数错，还是环境失败。  
**本项目里的证据：** metrics 测试和 eval report。  
**可能追问：** 如何按工具名聚合？

### Q34：handoff_count 说明什么？
**面试官想看什么：** 多 agent 可观测。  
**推荐回答：** 说明任务经过多少次角色切换，可用于发现过度协作或流程卡住。  
**本项目里的证据：** metrics 字段。  
**可能追问：** handoff 是否越多越好？

### Q35：approval_count 说明什么？
**面试官想看什么：** 人工介入成本。  
**推荐回答：** 它显示有多少风险动作需要人工确认，可用于优化策略和体验。  
**本项目里的证据：** `human_approval` event。  
**可能追问：** 如何减少 approval fatigue？

### Q36：duration_ms 当前如何计算？
**面试官想看什么：** 是否知道实现细节。  
**推荐回答：** TraceRecorder 记录相邻事件之间的毫秒差，summary 汇总总时长。  
**本项目里的证据：** `trace.py`。  
**可能追问：** 分布式 trace 怎么做？

### Q37：Eval runner 是否硬编码通过？
**面试官想看什么：** 评估可信度。  
**推荐回答：** 不是。它用当前 Python 解释器真实执行每个 case 的 `verify.py`。  
**本项目里的证据：** `eval/eval_runner.py`。  
**可能追问：** verify.py 本身有 bug 怎么办？

### Q38：Eval report 包含什么？
**面试官想看什么：** 报告可读性。  
**推荐回答：** total、passed、failed、pass rate、failed case list、每 case metrics 和 notes。  
**本项目里的证据：** `eval_report.md` 生成逻辑。  
**可能追问：** 如何做历史趋势？

### Q39：为什么 eval 要覆盖 safety case？
**面试官想看什么：** 非 happy path 意识。  
**推荐回答：** Agent 上线风险多在失败和越权路径，必须把 blocked/recovery 行为也纳入回归。  
**本项目里的证据：** cases 007-016。  
**可能追问：** 如何生成更多 adversarial case？

### Q40：case_001 证明什么？
**面试官想看什么：** 端到端能力。  
**推荐回答：** 单 agent 能读文件、patch、跑测试并修复 demo repo。  
**本项目里的证据：** `eval_cases/case_001_single_agent_fix_test`。  
**可能追问：** patch 第一次失败怎么办？

### Q41：patch failure recovery 怎么体现？
**面试官想看什么：** 从错误中恢复。  
**推荐回答：** MockLLM 第一次 patch 找不到 old text，观察失败后重试正确 patch。  
**本项目里的证据：** `MockLLMClient`、case_006。  
**可能追问：** 重复失败如何停止？

### Q42：repeated tool call 为什么要挡？
**面试官想看什么：** 循环失控防护。  
**推荐回答：** AgentLoop 发现最近工具调用重复时返回 blocked，避免无限循环。  
**本项目里的证据：** `tool_history` 检查。  
**可能追问：** 是否会误杀合理重试？

### Q43：workflow mode 和 agent mode 区别？
**面试官想看什么：** deterministic workflow 理解。  
**推荐回答：** workflow 是固定 plan-code-test-review 状态机，agent mode 是 LLM 决定下一步 tool。  
**本项目里的证据：** `workflows/coding_workflow.py`、case_014。  
**可能追问：** 什么时候用 workflow？

### Q44：multi-agent 是真的协作吗？
**面试官想看什么：** 是否只是打印。  
**推荐回答：** V1/V2 是轻量 supervisor/handoff demo，用于说明角色拆分和 trace，不声称复杂自治。  
**本项目里的证据：** `agents/supervisor_agent.py`。  
**可能追问：** 真实协作需要什么？

### Q45：Supervisor 的职责是什么？
**面试官想看什么：** 分工设计。  
**推荐回答：** 它把任务拆给 planner/coder/tester/reviewer，并记录 handoff。  
**本项目里的证据：** `agents/`。  
**可能追问：** 如何避免子 agent 冲突？

### Q46：Reviewer agent 有什么价值？
**面试官想看什么：** 质量门禁。  
**推荐回答：** 它可以基于 diff/test result 做最后检查，生产中可变成 PR review gate。  
**本项目里的证据：** `reviewer_agent.py`。  
**可能追问：** LLM review 可靠吗？

### Q47：为什么用 unittest？
**面试官想看什么：** 标准库优先。  
**推荐回答：** 降低安装成本，保证 Mac 本地和 CI runner 都能直接跑。  
**本项目里的证据：** `tests/`、Quickstart。  
**可能追问：** pytest 会不会更好？

### Q48：为什么强调 Python 3.11？
**面试官想看什么：** 环境确定性。  
**推荐回答：** 用户机器默认 Python 是 3.9.6，统一 `python3.11` 避免语法和行为差异。  
**本项目里的证据：** README Quickstart。  
**可能追问：** CI 如何固定版本？

### Q49：Production readiness 覆盖哪些形态？
**面试官想看什么：** 部署思考。  
**推荐回答：** local developer、CI runner、internal server、GitHub PR bot。  
**本项目里的证据：** `docs/12-production-readiness.md`。  
**可能追问：** 哪个最难？

### Q50：CI runner 最大风险？
**面试官想看什么：** CI 安全。  
**推荐回答：** 命令越权、网络外连、密钥泄露，所以要 allowlist、最小挂载、无持久写入。  
**本项目里的证据：** command policy 和 sandbox case。  
**可能追问：** pull_request_target 怎么办？

### Q51：GitHub PR bot 怎么上线？
**面试官想看什么：** 真实产品落地。  
**推荐回答：** 只读 diff -> 生成 patch -> 开 draft PR -> reviewer merge，token 最小权限。  
**本项目里的证据：** production readiness rollout。  
**可能追问：** bot 评论错误怎么办？

### Q52：internal server 需要什么？
**面试官想看什么：** 服务化思维。  
**推荐回答：** auth、tenant isolation、rate limit、audit、model gateway、job queue。  
**本项目里的证据：** `docs/12-production-readiness.md`。  
**可能追问：** 多租户文件隔离怎么做？

### Q53：model gateway 解决什么？
**面试官想看什么：** LLM 平台化。  
**推荐回答：** 统一 auth、routing、fallback、rate limit、audit、cost，避免业务直接耦合 provider。  
**本项目里的证据：** OpenAI client 文档和 production 文档。  
**可能追问：** gateway 怎么选模型？

### Q54：routing 策略怎么做？
**面试官想看什么：** 成本/质量权衡。  
**推荐回答：** 简单任务走便宜模型，复杂任务或失败重试走强模型，按 eval 结果校准。  
**本项目里的证据：** production roadmap。  
**可能追问：** 如何定义复杂任务？

### Q55：fallback 怎么设计？
**面试官想看什么：** 可靠性。  
**推荐回答：** invalid response、timeout、rate limit 时可降级模型、重试、切 Mock 或返回可恢复错误。  
**本项目里的证据：** structured invalid response。  
**可能追问：** 重试会不会重复执行工具？

### Q56：rate limit 为什么重要？
**面试官想看什么：** 滥用控制。  
**推荐回答：** Agent loop 可能多步调用模型和工具，限流要按用户、仓库、任务类型和模型层级做。  
**本项目里的证据：** production readiness。  
**可能追问：** burst 怎么处理？

### Q57：cost 如何度量？
**面试官想看什么：** 成本意识。  
**推荐回答：** V2 没有真实 token 计费，生产中由 model gateway 记录 token、模型、请求数和任务维度成本。  
**本项目里的证据：** docs 明确不编造线上指标。  
**可能追问：** 如何做预算上限？

### Q58：audit 记录什么？
**面试官想看什么：** 合规和排障。  
**推荐回答：** run_id、prompt/context 摘要、tool schema、tool args、observation、model、decision，不直接记录密钥。  
**本项目里的证据：** trace JSON。  
**可能追问：** PII 怎么处理？

### Q59：rollback 怎么做？
**面试官想看什么：** 事故恢复。  
**推荐回答：** local 看 git diff，CI 丢弃 workspace，PR bot 只开 draft PR，internal server 生成 patch 等人工应用。  
**本项目里的证据：** production readiness rollback。  
**可能追问：** 自动 merge 何时允许？

### Q60：incident 怎么复盘？
**面试官想看什么：** 运维闭环。  
**推荐回答：** 看 trace 和 metrics 定位失败步骤，把失败场景加入 eval case，再调整 guardrail/policy。  
**本项目里的证据：** eval case 扩展机制。  
**可能追问：** 谁负责 on-call？

### Q61：rollout 顺序是什么？
**面试官想看什么：** 渐进上线。  
**推荐回答：** Mock/eval -> shadow real LLM -> read-only tools -> write with approval -> 扩大范围。  
**本项目里的证据：** `docs/12-production-readiness.md`。  
**可能追问：** shadow mode 怎么评估？

### Q62：为什么不用 LangChain？
**面试官想看什么：** framework comparison。  
**推荐回答：** 面试项目要展示底层控制面；生产可用框架，但我先实现最小 loop/tool/safety/eval 以理解边界。  
**本项目里的证据：** `docs/13-framework-comparison.md`。  
**可能追问：** 迁移到 LangGraph 怎么做？

### Q63：和 LangGraph 的差异？
**面试官想看什么：** workflow graph 理解。  
**推荐回答：** LangGraph 擅长状态图和持久化执行；Agent Forge 是教学/面试 harness，代码更小、更可读。  
**本项目里的证据：** workflow mode 和 docs。  
**可能追问：** 哪些模块可替换？

### Q64：和 AutoGen/CrewAI 比呢？
**面试官想看什么：** 多 agent 生态了解。  
**推荐回答：** 它们更关注多角色协作抽象，Agent Forge 更强调安全、工具边界、trace 和 eval。  
**本项目里的证据：** docs/13。  
**可能追问：** 什么时候选 CrewAI？

### Q65：为什么标准库优先？
**面试官想看什么：** 依赖控制。  
**推荐回答：** 降低安装和演示成本，让面试官能把注意力放在 agent engineering，而不是依赖配置。  
**本项目里的证据：** urllib、unittest、argparse。  
**可能追问：** 标准库限制在哪里？

### Q66：项目如何讲到简历上？
**面试官想看什么：** storytelling。  
**推荐回答：** 强调“构建 Agent Harness 控制面”，列真实指标：16 eval cases、tests、trace、safety cases。  
**本项目里的证据：** `docs/20-resume-bullet-and-project-script.md`。  
**可能追问：** 为什么不写线上提升百分比？

### Q67：STAR 怎么讲？
**面试官想看什么：** 结构化表达。  
**推荐回答：** Situation 是 demo 不够生产化；Task 是升级 V2；Action 是补 LLM/context/adapter/metrics/eval/docs；Result 是命令和 case 通过。  
**本项目里的证据：** docs/20 STAR。  
**可能追问：** 最大困难是什么？

### Q68：resume storytelling 避免什么？
**面试官想看什么：** 诚实表达。  
**推荐回答：** 避免编造 QPS、用户数、成本下降，只用项目真实可验证指标。  
**本项目里的证据：** docs/20。  
**可能追问：** 没有线上指标会不会弱？

### Q69：白板图怎么画？
**面试官想看什么：** 架构表达。  
**推荐回答：** 左边任务和 guardrail，中间 AgentLoop/LLM/ToolRegistry，右边 Trace/Metrics/Eval，下方 Context。  
**本项目里的证据：** README architecture、docs/20。  
**可能追问：** 哪条链路最关键？

### Q70：如何证明测试通过？
**面试官想看什么：** 可复现。  
**推荐回答：** 跑 `python3.11 -m unittest discover tests`、demo 三模式、eval runner、py_compile。  
**本项目里的证据：** README Quickstart。  
**可能追问：** CI 中怎么跑？

### Q71：py_compile 有什么价值？
**面试官想看什么：** 基础质量门禁。  
**推荐回答：** 它快速发现语法错误，特别适合标准库项目和大量 eval verify 文件。  
**本项目里的证据：** 用户指定验证命令。  
**可能追问：** 类型检查呢？

### Q72：测试覆盖哪些新模块？
**面试官想看什么：** 回归保障。  
**推荐回答：** OpenAI client、context V2、adapter、metrics、eval runner 都有测试。  
**本项目里的证据：** `tests/test_*`。  
**可能追问：** 哪些还缺集成测试？

### Q73：当前边界有哪些？
**面试官想看什么：** 不夸大。  
**推荐回答：** 不是完整 MCP，不是真 LSP，没有线上 telemetry 后端，没有真实 token cost。  
**本项目里的证据：** README Current Boundaries。  
**可能追问：** 下一个最值得做什么？

### Q74：下一步 roadmap？
**面试官想看什么：** 技术演进。  
**推荐回答：** LSP provider、model gateway、GitHub PR bot、eval history、typed schema validation。  
**本项目里的证据：** README Roadmap。  
**可能追问：** 优先级怎么排？

### Q75：如果真实 LLM 乱调工具怎么办？
**面试官想看什么：** LLM 不可信假设。  
**推荐回答：** tool schema 只是建议，最终还要 registry、permission、sandbox、guardrail、eval case 兜底。  
**本项目里的证据：** AgentLoop 和 safety。  
**可能追问：** prompt injection 呢？

### Q76：prompt injection 怎么防？
**面试官想看什么：** 安全纵深。  
**推荐回答：** 不能只靠 prompt，要限制工具权限、隔离文件、审计执行，并让敏感路径永远不可读。  
**本项目里的证据：** sandbox sensitive path deny。  
**可能追问：** 文档中恶意指令怎么办？

### Q77：为什么 eval case 要有 task.md？
**面试官想看什么：** benchmark 可读性。  
**推荐回答：** task.md 说明用户意图，verify.py 说明机器检查标准，便于扩展和复盘。  
**本项目里的证据：** `eval_cases/*/task.md`。  
**可能追问：** case 元数据要不要结构化？

### Q78：为什么 final answer 要有未验证点？
**面试官想看什么：** 负责任输出。  
**推荐回答：** Agent 应说明没有验证的范围，避免把本地测试说成线上保证。  
**本项目里的证据：** output guardrail。  
**可能追问：** 用户不想看未验证点怎么办？

### Q79：如何把失败转成改进？
**面试官想看什么：** 工程闭环。  
**推荐回答：** 失败先看 trace，再新增 eval case，最后改 policy/tool/context/LLM parsing，并保持 report 可比较。  
**本项目里的证据：** eval runner 和 metrics。  
**可能追问：** 如何防止 eval 过拟合？

### Q80：你最想让面试官记住什么？
**面试官想看什么：** 项目主线。  
**推荐回答：** 我不是只会调 SDK，而是能把 agent 的工具、安全、上下文、观测、评估和生产化边界拆开并实现。  
**本项目里的证据：** V2 全部模块、README、docs/20。  
**可能追问：** 真实业务中你会先落哪一块？

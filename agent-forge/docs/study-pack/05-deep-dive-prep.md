# 05 深挖追问准备

这份文档专门准备面试官继续问细节。每个问题都按“短答 + 深挖点 + 本项目证据”组织。

## 1. AgentLoop 为什么这样设计？

短答：

> AgentLoop 的核心是 action-observation 闭环。模型只负责提出下一步动作，runtime 负责安全检查、工具执行和结果回传。

深挖点：

- 为什么不能让 LLM 直接执行？
- 为什么需要 max_steps？
- 为什么失败 observation 要回到 messages？
- 为什么最终答案前还要 output guardrail？

项目证据：

- `agent_forge/runtime/agent_loop.py`
- `trace-single.pretty.json`
- `tests/test_agent_loop.py`

## 2. MockLLM 是不是太假？

短答：

> MockLLM 不用来证明模型智能，而是用来稳定验证 runtime 控制层。

深挖点：

- 单测需要 deterministic；
- eval 不能依赖网络和随机输出；
- 真实 LLM 路径通过 OpenAI-compatible client 保留；
- MockLLM 还故意制造 patch 失败，展示 recovery。

项目证据：

- `MockLLMClient`
- `tests/test_openai_compatible_llm.py`
- `scripts/verify.sh`

## 3. OpenAI-compatible 接入有什么风险？

短答：

> 不同 provider 的 tool call schema、错误格式、超时行为可能不同，所以 client 必须把异常转成结构化 error。

深挖点：

- `tool_calls` 和 legacy `function_call` 兼容；
- arguments 可能是 JSON 字符串，也可能是对象；
- API 可能返回 invalid JSON；
- API key 不能提交；
- profile 文件要 gitignore。

项目证据：

- `agent_forge/runtime/llm_client.py`
- `agent_forge/runtime/llm_config.py`
- `llm_profiles.example.json`
- `.gitignore`

## 4. ToolRegistry 的边界是什么？

短答：

> ToolRegistry 是模型 tool call 和本地工具执行之间的契约边界。

深挖点：

- LLM 只能看到 schema；
- unknown tool 返回失败 Observation；
- registry 不应该知道 agent planning；
- 工具新增时不应该改 AgentLoop。

项目证据：

- `agent_forge/tools/registry.py`
- `eval_cases/case_007_unknown_tool_recovery`

## 5. 安全设计为什么不只靠 prompt？

短答：

> prompt 只能指导模型，不能约束执行。真正风险发生在 tool execution，所以必须有 runtime 级安全边界。

深挖点：

- input guardrail 是第一层，不够；
- permission policy 决定 allow/ask/deny；
- workspace sandbox 限制路径；
- command policy 限制危险命令；
- output guardrail 防止虚假测试声明。

项目证据：

- `agent_forge/safety/guardrails.py`
- `agent_forge/safety/permission.py`
- `agent_forge/safety/sandbox.py`
- `agent_forge/safety/command_policy.py`

## 6. Sandbox 的局限是什么？

短答：

> 当前 sandbox 是本地路径级保护，不是 OS/container 级隔离。

深挖点：

- 可以防工作区外路径；
- 可以防敏感文件名；
- 不能替代容器、seccomp、网络隔离；
- 生产中应加入 per-run temp workspace 和审计。

项目证据：

- `WorkspaceSandbox`
- `eval_cases/case_010_sandbox_blocks_secret_file`

## 7. CommandPolicy 为什么限制网络命令？

短答：

> coding agent 能读代码和执行命令，如果允许任意网络命令，就有外传代码或密钥的风险。

深挖点：

- `curl/wget/ssh/scp` 默认危险；
- 依赖安装应该走受控环境；
- 生产里可以加 allowlist proxy；
- 命令审计要进 trace。

项目证据：

- `agent_forge/safety/command_policy.py`
- `eval_cases/case_011_command_policy_blocks_network_command`

## 8. Context engineering 为什么重要？

短答：

> Agent 不能把全仓库塞进 prompt，需要选择、排序和预算管理。

深挖点：

- repo map 给全局结构；
- retrieval 给相关文档；
- symbol_search 给代码符号；
- file_ranker 排优先级；
- budget report 说明是否截断。

项目证据：

- `agent_forge/context/context_builder.py`
- `agent_forge/context/repo_map.py`
- `agent_forge/context/symbol_search.py`
- `agent_forge/context/file_ranker.py`

## 9. 为什么 multi-agent 要 Supervisor？

短答：

> Supervisor 让多 agent 的 phase transition 可控、可追踪，而不是多个 agent 自由互相调用。

深挖点：

- Planner/Coding/Tester/Reviewer 分工；
- Tester fail 可以回到 Coding；
- handoff payload 可审计；
- 复杂度低于任意 agent graph。

项目证据：

- `agent_forge/agents/supervisor_agent.py`
- `agent_forge/agents/handoff.py`
- `trace-multi.pretty.json`

必须主动补充的边界：

> 当前项目里的 multi mode 是教学版 supervisor workflow。它没有并发，也没有让每个 subagent 走完整 AgentLoop。它的价值是展示 handoff、retry 和 review gate。生产级会把 AgentLoop 抽成通用 AgentRuntime，让 supervisor 调度多个 runtime-backed subagents。

如果面试官继续问“为什么现在没这么做”，答：

> 因为这个项目先把两个概念拆开：single mode 负责展示完整 agent runtime，multi mode 负责展示 supervisor orchestration。这样学习成本更低，也更容易通过 trace 验证每个机制。下一步才是把两者合并成真正的 multi-agent scheduler。

## 10. Workflow 和 Agent 怎么取舍？

短答：

> Workflow 适合稳定、可枚举的流程；Agent 适合需要根据 observation 动态决策的任务。

深挖点：

- workflow 可预测、易测试；
- agent 灵活但更难控；
- 项目保留两者用于对比；
- 面试时可以说明不是所有问题都需要 agent。

项目证据：

- `agent_forge/workflows/coding_workflow.py`
- `docs/study-pack/03-run-modes-and-trace-reading.md`

## 11. Trace 的价值是什么？

短答：

> Trace 把 agent 的黑箱行为变成可审计事件流。

深挖点：

- 可以定位哪轮 LLM 决策错；
- 可以看工具是否失败；
- 可以看 permission 是否生效；
- 可以汇总 metrics；
- 面试或 debug 时比口头描述可信。

项目证据：

- `agent_forge/observability/trace.py`
- `agent_forge/observability/metrics.py`
- `trace-single.pretty.json`

## 12. Eval 为什么不是硬编码？

短答：

> 每个 eval case 都有独立 verify.py，runner 真实执行验证逻辑。

深挖点：

- case 包含 task 和 verify；
- runner 收集 pass/fail；
- eval_report 可复查；
- 适合作为 regression benchmark；
- 不是线上业务指标。

项目证据：

- `agent_forge/eval/eval_runner.py`
- `eval_cases/*/verify.py`
- `eval_report.md`

## 13. 如果接公司 API，应该怎么讲？

短答：

> 只要公司 API 兼容 OpenAI chat completions，就可以通过 base_url/api_key/model 或 llm profile 切换。

深挖点：

- 不把 key 写进代码；
- profile 本地保存并 gitignore；
- provider 差异先在 client 层适配；
- 真实模型输出不稳定，所以 smoke test 和 unit test 仍保留 MockLLM。

项目证据：

- `llm_profiles.example.json`
- `local_scripts/run_llm_profile.sh`
- `agent_forge/runtime/llm_config.py`

## 14. 下一步怎么演进？

短答：

> 我会优先补 AgentLoop-backed subagents、任务 DAG 调度、model gateway、LSP provider、更严格 tool schema、容器级 sandbox 和 eval history。

深挖点：

- AgentLoop-backed subagents：每个角色都有自己的 runtime、prompt、context、tool 权限；
- task DAG：支持并发、依赖、ownership、冲突处理；
- model gateway：routing、fallback、rate limit、cost；
- LSP：definition/references/diagnostics；
- schema：JSON Schema / pydantic；
- sandbox：container/firecracker/seccomp；
- eval history：趋势和回归定位。

项目证据：

- `agent_forge/production/readiness.py`
- `agent_forge/production/risk_registry.py`
- `docs/study-pack/01-code-map-and-architecture.md`

## 15. 面试回答模板

当你被问到一个细节，按这个结构答：

```text
1. 先说设计目标
2. 再说当前实现
3. 再说 trade-off
4. 最后说证据文件或运行命令
```

例子：

> 设计目标是让工具执行可控。当前实现是 ToolRegistry + PermissionPolicy + WorkspaceSandbox。trade-off 是它足够轻量、可测试，但还不是容器级隔离。证据可以看 `agent_forge/runtime/agent_loop.py` 里的 permission_check 事件，以及 `eval_cases/case_010...` 和 `case_011...`。

# 16-four-layer-followups

每个模块按四层准备：What did you do / Why this approach / What went wrong / What else did you consider。

## Agent Loop

- Layer 1：我实现了从 task 到 context、plan、LLM、tool call、observation、final answer 的闭环。
- Layer 2：这样能把普通聊天和可执行 Agent 区分开。
- Layer 3：早期 loop 只记录 tool_call，面试深挖时解释不了 context 和 plan，所以补了 `context_assembly`、`plan`、`action`、`observation` trace。
- Layer 4：考虑过直接用 LangGraph，但本项目目标是先讲清底层机制。
- English keywords：controlled execution loop, observation feedback, max steps.
- 代码证据：`agent_forge/runtime/agent_loop.py`。
- 当前不足：plan 是 summary，不是真复杂 planner。
- 后续增强：把 planner 抽成可替换策略。

## Tool Calling

- Layer 1：我实现 ToolRegistry、schema、参数校验、unknown tool recovery。
- Layer 2：LLM 输出不可信，工具执行前必须由 runtime 校验。
- Layer 3：早期 schema 太薄，缺参可能直接抛异常；现在 registry 会返回 failed Observation。
- Layer 4：考虑完整 JSON Schema，当前先用轻量 required 校验。
- English keywords：tool schema, registry, invalid arguments, observation.
- 代码证据：`agent_forge/tools/registry.py`。
- 当前不足：类型校验还不完整。
- 后续增强：接 JSON Schema validator。

## Observation

- Layer 1：所有工具结果统一成 `Observation(tool_name, success, content)`。
- Layer 2：Observation 是 Agent 继续决策的反馈，不是普通日志。
- Layer 3：如果工具各自返回不同结构，loop 会变复杂。
- Layer 4：考虑 metadata 字段，当前保持最小可读。
- English keywords：feedback loop, tool result, state update.
- 代码证据：`agent_forge/runtime/observation.py`。
- 当前不足：缺少结构化 metadata。
- 后续增强：加 exit_code、changed_files、risk_level。

## Workflow vs Agent

- Layer 1：workflow 是固定 plan-code-test-review，agent loop 是动态工具决策。
- Layer 2：生产中稳定任务适合 workflow，开放任务适合 agent。
- Layer 3：纯 agent 容易漂移；纯 workflow 不灵活。
- Layer 4：考虑混合模式：workflow 管大阶段，agent 处理阶段内细节。
- English keywords：deterministic workflow, dynamic agent, hybrid orchestration.
- 代码证据：`workflows/coding_workflow.py`、`runtime/agent_loop.py`。
- 当前不足：workflow 仍是 demo 级。
- 后续增强：加入失败分支和可恢复状态。

## Supervisor / Subagent

- Layer 1：Supervisor 用 TaskPhase 编排 Planner/Coding/Tester/Reviewer。
- Layer 2：职责分离让 trace 和失败定位更清楚。
- Layer 3：早期像固定打印流程，所以补了状态机和 handoff payload。
- Layer 4：考虑单 agent 全做；简单任务确实不需要多 agent。
- English keywords：supervisor, role boundary, phase state machine.
- 代码证据：`agents/supervisor_agent.py`。
- 当前不足：调度策略仍是规则化。
- 后续增强：让 Supervisor 根据 trace/eval 结果动态路由。

## Handoff

- Layer 1：Handoff 包含 from/to/reason/payload，并写入 trace。
- Layer 2：它表达 agent 之间的责任交接，不是工具调用。
- Layer 3：payload 太薄会导致 reviewer/tester 不知道上下文。
- Layer 4：考虑共享黑板模式，当前用 payload 更直观。
- English keywords：state passing, responsibility transfer, traceable handoff.
- 代码证据：`agents/handoff.py`。
- 当前不足：payload schema 未强类型化。
- 后续增强：定义 HandoffPayload dataclass。

## Context Engineering

- Layer 1：我实现 repo map、retrieval、memory、selected files、budget report。
- Layer 2：不能把整个 repo 塞进 prompt，需要选择和记录。
- Layer 3：关键词检索简单但可解释；大仓库召回不足。
- Layer 4：考虑 embedding/LSP，V2 先做 AST symbol search 和 file ranker。
- English keywords：context budget, retrieval, selected files.
- 代码证据：`context/context_builder.py`。
- 当前不足：不是 token 级预算。
- 后续增强：接 tokenizer 和 semantic summarizer。

## Memory / RAG

- Layer 1：Memory 支持 recent items、kv、recent observations、clear。
- Layer 2：Agent 需要记住最近观察和偏好，但不能无限增长。
- Layer 3：长期记忆会引入隐私和污染风险。
- Layer 4：考虑向量库，当前用关键词 RAG 降低依赖。
- English keywords：session memory, keyword retrieval, bounded memory.
- 代码证据：`context/memory.py`、`context/rag.py`。
- 当前不足：无持久化。
- 后续增强：按项目维度持久化并加清理策略。

## Permission / Sandbox

- Layer 1：路径限制在 workspace，命令走 allow/deny，写操作 ASK。
- Layer 2：Agent 最危险的是工具执行，不是生成文本。
- Layer 3：字符串前缀判断不安全，所以使用 `Path.relative_to`。
- Layer 4：考虑容器沙箱，当前先做本地路径和命令边界。
- English keywords：workspace boundary, allow/ask/deny, command policy.
- 代码证据：`safety/sandbox.py`、`safety/permission.py`。
- 当前不足：不是 OS 级隔离。
- 后续增强：容器、seccomp、只读挂载。

## Guardrails

- Layer 1：input/tool/output 三类 guardrail 都有 category、reason、severity。
- Layer 2：permission 控制能不能做，guardrail 控制任务和结果是否安全可信。
- Layer 3：输出也要检查，因为 Agent 可能没跑测试却声称通过。
- Layer 4：考虑模型分类器，当前用规则保证可解释。
- English keywords：input guardrail, tool guardrail, output guardrail.
- 代码证据：`safety/guardrails.py`。
- 当前不足：规则覆盖有限。
- 后续增强：分类器 + policy engine。

## Human Approval

- Layer 1：write/apply_patch 默认 ASK，demo auto approve，no-auto 会拒绝。
- Layer 2：生产中高风险动作必须人审。
- Layer 3：如果全自动，Agent 可能误改或越权。
- Layer 4：考虑 Web approval queue，当前用 mock approval。
- English keywords：human-in-the-loop, approval gate, risk control.
- 代码证据：`tools/ask_human.py`、`tools/apply_patch.py`。
- 当前不足：没有真实交互 UI。
- 后续增强：审批服务和审计记录。

## Tracing

- Layer 1：每次 run 写 JSON trace 和 summary.md。
- Layer 2：Agent 出错时必须知道它看了什么、做了什么、哪里失败。
- Layer 3：普通日志不能表达 tool/permission/handoff 的结构化关系。
- Layer 4：考虑 OpenTelemetry，当前 JSON 更适合学习和面试。
- English keywords：trace event, metrics, audit trail.
- 代码证据：`observability/trace.py`、`metrics.py`。
- 当前不足：本地文件，不是服务化 telemetry。
- 后续增强：trace backend 和 dashboard。

## Eval

- Layer 1：16 个 case，每个 task.md + verify.py，runner 真实执行。
- Layer 2：demo 证明能演示，eval 证明能回归。
- Layer 3：早期指标容易硬编码，所以现在从 verify 和 trace 生成。
- Layer 4：考虑大规模 benchmark，当前先做 smoke/regression。
- English keywords：executable eval, pass rate, safety cases.
- 代码证据：`eval/eval_runner.py`、`eval_cases/`。
- 当前不足：case 数仍小。
- 后续增强：模型对比和历史趋势。

## Production Readiness

- Layer 1：文档覆盖 local、CI、internal server、PR bot、gateway、audit、rollback。
- Layer 2：面试官想看 demo 到生产的风险意识。
- Layer 3：不能把本地 demo 直接说成 production-ready。
- Layer 4：考虑直接接云服务，当前先讲清部署形态和控制面。
- English keywords：model gateway, rate limit, audit, rollback, incident.
- 代码证据：`docs/12-production-readiness.md`。
- 当前不足：没有真实服务化部署。
- 后续增强：CI runner、PR bot、cost tracker。

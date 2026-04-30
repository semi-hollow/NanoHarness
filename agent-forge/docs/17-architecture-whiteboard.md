# 17-architecture-whiteboard

开场话术：

> Let me draw the architecture to make sure we are aligned.

## 1. 总体架构图

```text
User Task / CLI
      |
      v
Input Guardrail
      |
      v
Context Builder -------- Repo Map / Memory / RAG / Symbol Search
      |
      v
Agent Runtime ---------- MockLLM / OpenAI-compatible LLM
      |
      v
Tool Registry ---------- read / write / grep / patch / run / git / ask_human
      |
      v
Permission + Sandbox + Tool Guardrail
      |
      v
Observation
      |
      v
Trace JSON + summary.md ---- Metrics ---- Eval Report
```

两个关键决策：

- 我把安全控制放在工具执行前后，而不是只靠 prompt。
- 我把 eval 和 trace 绑定，让结果有证据，而不是只看最终回答。

## 2. 单 Agent Loop 图

```text
task
  -> context_assembly
  -> plan summary
  -> llm_call
  -> tool_call?
       yes -> permission_check -> execute -> observation -> next step
       no  -> output_guardrail -> final_answer
```

讲法：Agent Loop 的关键不是“模型回答”，而是 action 和 observation 的闭环。

## 3. Supervisor / Subagent 图

```text
SupervisorAgent
  phase=planning  -> PlannerAgent
  phase=coding    -> CodingAgent
  phase=testing   -> TesterAgent
       fail       -> CodingAgent retry -> TesterAgent retest
  phase=reviewing -> ReviewerAgent
  phase=done/failed
```

讲法：Subagent 不互相乱调，统一由 Supervisor 编排；handoff payload 传 phase、task、files、test result、review result。

## 4. Tool Calling 数据流图

```text
LLM tool call
  -> ToolRegistry.get(name)
  -> ToolRegistry.validate(arguments)
  -> PermissionPolicy.decide(action)
  -> WorkspaceSandbox.ensure_safe_path(path)
  -> Tool.execute(arguments)
  -> Observation(success, content)
```

讲法：Tool schema 是模型和 runtime 的契约，Observation 是执行结果回传给 loop 的标准格式。

## 5. Permission / Sandbox 拦截点图

```text
read/list/grep        -> allow
write/apply_patch     -> ask -> auto approve in demo / reject with --no-auto-approve
run_command           -> command_policy allow/deny
path access           -> workspace relative_to check
sensitive file        -> .env / id_rsa / .pem / .key / secrets deny
```

讲法：Agent 的风险不在会说什么，而在会执行什么。

## 6. Eval / Trace 闭环图

```text
eval_case/task.md
      |
      v
verify.py runs demo/tool/context check
      |
      v
trace.json
      |
      v
metrics summary
      |
      v
eval_report.md
      |
      v
new failure -> new eval case -> code/policy fix
```

讲法：demo 是演示，eval 是回归；trace 是定位失败的证据链。

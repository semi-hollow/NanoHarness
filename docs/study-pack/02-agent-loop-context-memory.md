# 02 AgentLoop, Context, Memory

这份文件只回答一个问题：`single` 模式为什么是项目主线。

## AgentLoop 八段

| phase | code | 作用 |
|---|---|---|
| input guardrail | `input_guardrail()` | 先挡明显危险/不支持任务。 |
| clarification | `ClarificationPolicy` | 只有指代无法 grounding 时才反问。 |
| planning mode | `PlanningModePolicy` | 记录任务适合 ReAct、plan-execute、workflow 还是 answer-only。 |
| context assembly | `build_context_report()` | repo map、文件预览、memory、tools、FORGE.md。 |
| model call | `ModelGateway.chat()` | provider retry、fallback、usage telemetry。 |
| tool decision | `ToolRouter` + `tool_guardrail` | 裁剪工具候选，防止未知/重复工具调用。 |
| execution | `HookManager` + `ToolRegistry` | 审批、环境检查、schema validation、工具执行、脱敏。 |
| recovery/final | `StepController` + `EvidenceLedger` | 失败分类、预算控制、证据引用、最终回答。 |

## Context 不是拼字符串

`ContextBuilder` 只负责渲染，真正策略在 `ContextStrategy`：

- `repo_map`：给模型项目形状。
- `file_ranker`：从任务词、路径名、内容命中选相关文件。
- `selected_file_previews`：只放最相关文件的 bounded preview。
- `retrieved_docs`：透明 lexical retrieval，适合代码仓库。
- `attention_sink`：稳定提醒最新任务、证据、验证纪律。
- `FORGE.md`：仓库级规则，不写死在 prompt 里。
- `budget_breakdown`：每段 context 花多少字符可追踪。

## Memory 分层

| memory | 代码 | 用途 |
|---|---|---|
| recent notes | `Memory.items` | 保存当前 run 的短事实。 |
| observations | `Memory.observations` | 最近工具结果进入下一轮。 |
| summary | `Memory.summaries` | 老 observation 压缩，避免上下文爆炸。 |
| records | `MemoryRecord` | scope/confidence/TTL/source/agent_name，避免跨 agent 污染。 |
| resume seed | `SessionStore` / `TaskStateStore` | 只在 topic 连续时进入 context。 |

## Task State 和 Trace 的区别

| artifact | 作用 |
|---|---|
| `trace.json` | 完整事件流：每个 context/model/tool/hook/recovery 事件。 |
| `task_state/*.json` | 可恢复控制面：status、step、last_tool、last_observation、resume_hint。 |
| `usage_report.md` | 人看的量化摘要：token、cost、context、tool efficiency、runtime control。 |

常用命令：

```bash
python run_demo.py --list-task-states
python run_demo.py --show-task-state <run_id>
python run_demo.py --resume-state <run_id> --mode single
python run_demo.py --replay-run .agent_forge/latest/webhook-deepseek/trace.json
```

## 技术口径

这个项目的核心不是“模型聪明”，而是 runtime 把模型输出变成可控动作：
模型只提出工具调用，系统负责上下文、审批、边界、执行、恢复和审计。

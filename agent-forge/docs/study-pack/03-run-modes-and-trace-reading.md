# 03 运行模式与 Trace 对照

这份文档用于边跑边看。目标是让你知道每个 mode 的输出为什么长那样，以及 trace 里每个事件对应哪段代码。

## 准备

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate
```

一键跑三种模式：

```bash
scripts/run_all_modes.sh
```

完整验证：

```bash
scripts/verify.sh
```

## single mode

命令：

```bash
python run_demo.py --mode single --trace-file trace-single.json
python -m json.tool trace-single.json > trace-single.pretty.json
```

执行路径：

```text
run_demo.py
  -> cli.main
  -> reset_demo_repo
  -> build_registry
  -> resolve_llm_config
  -> build_llm
  -> AgentLoop.run
```

你应该看到：

```text
已完成修复并验证测试通过。
未验证点: 未进行真实线上压测。
```

### trace 事件顺序

| trace event | 对应代码位置 | 含义 |
| --- | --- | --- |
| `guardrail_check` | `input_guardrail` | 先检查用户任务是否危险。 |
| `context_assembly` | `build_context_report` | 汇总 repo map、memory、retrieval、tools。 |
| `plan` | `SimplePlanner.plan` | 记录本轮计划摘要。 |
| `llm_call` | `self.llm.chat` | 调用 MockLLM 或真实 LLM。 |
| `action` | tool call loop | LLM 想调用哪个工具。 |
| `permission_check` | `PermissionPolicy.decide` | 判断读、写、命令是否允许。 |
| `human_approval` | ask path | 写入/patch 可能需要审批。 |
| `tool_call` | `registry.execute` | 实际执行工具。 |
| `tool_observation` | Observation | 工具执行结果。 |
| `observation` | state update | 把结果放回 loop。 |
| `final_answer` | no tool calls | LLM 给出最终回答。 |

### 为什么会有 `success=False`

single demo 里第一次 patch 故意失败：

```text
old = "return a * b"
actual file = "return a - b"
```

所以 trace 里会出现：

```text
tool_observation success=False
```

这不是整体失败。后面 recovery patch 会把 `return a - b` 改成 `return a + b`，再跑测试。

面试讲法：

> I intentionally keep a failed tool observation in the demo to show that the agent loop can recover from execution feedback rather than assuming every tool call succeeds.

## multi mode

命令：

```bash
python run_demo.py --mode multi --trace-file trace-multi.json
python -m json.tool trace-multi.json > trace-multi.pretty.json
```

执行路径：

```text
cli.main
  -> SupervisorAgent.run
  -> PlannerAgent
  -> CodingAgent
  -> TesterAgent
  -> CodingAgent retry
  -> TesterAgent retest
  -> ReviewerAgent
```

你应该看到：

```text
SupervisorAgent -> PlannerAgent
SupervisorAgent -> CodingAgent
SupervisorAgent -> TesterAgent
SupervisorAgent -> CodingAgent (retry)
SupervisorAgent -> TesterAgent (retest)
SupervisorAgent -> ReviewerAgent
Final: pass
```

关键理解：

- multi mode 的重点不是“多个 LLM 聊天”；
- 重点是 supervisor 控制 phase；
- subagent 的 handoff 可追踪；
- tester 失败后可以回到 coding。

面试讲法：

> I model multi-agent as supervised orchestration instead of free-form peer-to-peer communication, because it keeps phase transitions and failure recovery auditable.

## workflow mode

命令：

```bash
python run_demo.py --mode workflow
```

输出类似：

```text
WorkflowState(... final_status='success')
```

执行路径：

```text
cli.main
  -> run_workflow
  -> WorkflowState
```

关键理解：

- workflow 不调用 LLM；
- 不需要 observation-driven planning；
- 适合固定流程；
- 用来和 agent loop 对比。

面试讲法：

> Workflow is deterministic control flow. Agent loop is dynamic control flow driven by model output and tool observations.

## verify.sh 做了什么

命令：

```bash
scripts/verify.sh
```

它会跑：

```text
single demo
multi demo
workflow demo
unittest discover tests
eval runner
compileall
```

通过标志：

```text
Final: pass
final_status='success'
Ran 48 tests ... OK
eval_report.md generated
```

## 如何读 pretty trace

建议搜索这些关键词：

```bash
rg '"event": "llm_call"|tool_call|tool_observation|final_answer' trace-single.pretty.json
```

读 trace 的四步：

1. 找 `llm_call`，看模型每轮返回 final answer 还是 tool calls。
2. 找 `action`，看模型想做什么。
3. 找 `tool_observation`，看工具执行结果。
4. 找 `final_answer`，看最终是否有测试证据和未验证点。

## 你应该亲手做的练习

1. 跑 single，找到第一次 patch 失败的 observation。
2. 跑 multi，找到所有 `handoff` event。
3. 跑 workflow，对比它为什么没有 LLM tool call。
4. 用 `--no-auto-approve` 跑 single，观察 approval 被拒绝时 trace 怎么变化。
5. 改 `--trace-file` 文件名，确认 trace 是运行证据。

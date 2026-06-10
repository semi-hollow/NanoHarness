# 04 Orchestration, Review, Eval

这个文件把三件事放在一起：多 agent 编排、代码审查门禁、eval 回归。
它们都属于“模型输出不能直接信，要用系统验证”的质量层。

## Multi-Agent 设计

`multi` 模式不是为了炫多个 agent，而是体现生产编排边界：

| role | code | 权限 |
|---|---|---|
| SupervisorAgent | `agents/supervisor_agent.py` | 拥有 graph、handoff、retry、review gate。 |
| PlannerAgent | `AgentSpec(role=planner)` | 不给工具，只产出计划 artifact。 |
| CodingAgent | `AgentSpec(role=coder)` | 可读/patch 指定文件。 |
| TesterAgent | `AgentSpec(role=tester)` | 可运行验证命令，不可改代码。 |
| ReviewerAgent | `AgentSpec(role=reviewer)` | 只能看 git diff/status。 |

Supervisor 不信子 agent 的自然语言结论，而是看 trace/tool observation/artifact。

## 为什么 multi 看起来线性

当前验证任务天然依赖：plan -> code -> test -> retry -> review。线性不是能力缺失，
而是依赖图决定的。`TaskScheduler` 已经支持 conflict-aware batches，只有当任务之间没有
文件写冲突、依赖关系允许时才适合并发。

## Review Gate

`--mode review` 是 repo-level deterministic review：

- 读取 `git diff --name-only` 和 `git diff --stat`。
- 标出 secret、`shell=True`、runtime/safety 改动、缺少 test/eval diff。
- 输出 verdict：`pass` / `needs_attention` / `blocked`。
- 写入 trace 和 usage report。

命令：

```bash
python run_demo.py --mode review --trace-file .agent_forge/latest/review/trace.json
```

## Eval Regression

`agent_forge/eval/eval_runner.py` 做 local regression：

- 每个 `eval_cases/*/verify.py` 是一个可执行 case。
- 输出 task_success、test_pass、safety_violation、tool count、trace count。
- `flywheel.py` 按 capability 聚合 badcase。
- `eval_history.py` 比较上一轮：pass_rate_delta、新增失败、修复失败。

命令：

```bash
python -m agent_forge.eval.eval_runner
cat .agent_forge/eval_report.md
```

## 质量层口径

生产级 CodingAgent 的关键不是“一个模型一次答对”，而是每一层都能验证：

- 子任务结果由 Supervisor 验证。
- 代码 diff 由 review gate 验证。
- 行为变化由 unittest/eval case 验证。
- 回归趋势由 eval history 验证。
- 用户可读证据由 trace/usage report 验证。

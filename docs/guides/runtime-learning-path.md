# Runtime 学习路径

这是一份动手学习手册。先用真实命令产生 artifact，再回到代码定位 owner；不要从
两百多个 Python 文件逐个阅读。

## 0. 启动与验证

```bash
cd /path/to/NanoHarness
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[bench,dev]'
forge doctor
python -m unittest discover -s tests
forge ui --no-open
```

模型配置通过环境变量或 CLI 参数提供。Worktree/OCI 能力还依赖 Git 或 Docker-compatible
runtime。先看 [`../CAPABILITY_REALITY_MATRIX.md`](../CAPABILITY_REALITY_MATRIX.md)，
确认每种模式能证明什么。

## 1. 建立全貌

只打开以下文件，并保持函数体折叠：

| 顺序 | 文件 | 只看什么 |
| ---: | --- | --- |
| 1 | `agent_forge/cli/parser.py` | 用户输入契约 |
| 2 | `agent_forge/cli/dispatch.py` | 命令交给哪个 capability |
| 3 | `agent_forge/cli/repository.py` | run 的 composition 和 artifact 边界 |
| 4 | `agent_forge/runtime/application/agent_loop.py` | `AgentLoop.run` |
| 5 | `agent_forge/runtime/application/session.py` | 一次 run 的字段表 |
| 6 | `agent_forge/runtime/application/run_preparation.py` | `start` 与 `execute` |
| 7 | `agent_forge/runtime/application/turn_preparation.py` | `execute` |
| 8 | `agent_forge/runtime/application/tool_execution.py` | `execute_calls` 与 `_execute_call` |
| 9 | `agent_forge/runtime/application/run_lifecycle.py` | `update`、`request_human_input`、`stop` |

此时你应该能口述：CLI 装配 Runtime，AgentLoop 控制阶段，Application 只依赖 Port，
Wiring 注入 Adapter，Trace/Checkpoint 保存证据。

## 2. Single Agent 实验

```bash
forge run "inspect this repository and report one bounded improvement" \
  --workspace . \
  --provider deepseek \
  --max-steps 6 \
  --execution-mode worktree \
  --network-policy deny \
  --no-keep-worktree
```

按顺序打开新 run：

```text
execution_environment.json   实际 workspace、隔离模式、命令历史
trace.json                   原始事实流
task_state/*.json            最后 checkpoint
operation_ledger/*.json      副作用幂等记录
usage.json                   trace 的量化投影
usage_report.md              展示层
patch.diff                   candidate，不等于 solved
final_answer.txt             模型最终文本
```

然后回到代码跟一条最短链：

```text
AgentLoop.run
-> RunPreparation.start/execute
-> TurnPreparation.execute
-> ModelPort.chat
-> ToolExecutionPipeline.execute_calls
-> RunLifecycle.stop
```

## 3. Tool Governance 实验

重点文件：

```text
runtime/application/tool_execution.py      固定治理顺序
runtime/application/tool_authorization.py  allow/deny/ask + approval
runtime/application/operation_tracker.py   key/fingerprint/replay
runtime/application/tool_feedback.py       observation/recovery/test evidence
tools/tool_router.py                        模型可见工具
tools/registry.py                           参数校验和执行
safety/command_policy.py                    命令规则
safety/sandbox.py                           路径规则
```

不要把 ToolRouter 当安全边界。它只控制 schema visibility；即使工具可见，仍需经过
Hook、Permission、CommandPolicy 和 Sandbox。

建议对照 trace 找一条工具调用：

```text
action
-> guardrail_check
-> hook_check
-> permission_check
-> optional human_approval
-> tool_call
-> tool_observation
-> recovery_decision / validation_evidence
-> task_state_checkpoint
```

## 4. Human Input 实验

信息不足时，Runtime 会创建 `HumanInputRequest` 并停在 `WAITING_HUMAN`：

```bash
forge respond <request-id> \
  --answer "Use the existing public API and keep backward compatibility."

forge resume .agent_forge/runs/<previous-run-id>
```

阅读链：

```text
RunPreparation._resolve_clarification
或 ToolExecutionPipeline._handle_human_question
-> RunLifecycle.request_human_input
-> HumanInputRepository
-> cli.operator.respond_to_human_input_request
-> BuildContinuationPlan.execute
-> cli.resume.resume_repository_task
```

验证三个事实：原 run 没有继续执行、回答单独持久化、新 run 有 `resume_link.json` 和
`resume_chain.md`。这不是恢复隐藏 model state。

## 5. Approval 与幂等实验

```bash
forge run "make one small documented edit" \
  --no-auto-approve-writes \
  --approval-mode on-write

forge approve <operation-key>
forge resume .agent_forge/runs/<previous-run-id>
```

阅读链：

```text
OperationTracker.describe
-> ToolAuthorizationGate.authorize
-> ApprovalRepository.request
-> WAITING_APPROVAL
-> DecideApproval.execute
-> fingerprint recheck
-> OperationTracker.record_result
```

重点理解：同一个 operation 重放时会跳过；目标在批准后变化会标记 stale；回答
`ask_human` 不能替代 approval。

## 6. 顺序 Multi-Agent

```bash
forge run "fix the failing test" \
  --agent-mode multi \
  --profile coding_fix \
  --max-revision-rounds 2
```

先看：

```text
multi_agent/application/coordinator.py
multi_agent/domain/models.py
multi_agent/ports/sequential.py
multi_agent/adapters/role_runtime.py
multi_agent/adapters/artifact_files.py
multi_agent/presentation/report.py
```

主链：Implementer -> artifact -> Reviewer -> optional revision -> Verifier。每个角色都
复用同一个 AgentLoop；“多 Agent”新增的是显式控制点和 artifact handoff，不是隐藏共享
memory。

## 7. 并发 Live Fanout

```bash
forge run "audit independent runtime areas" \
  --agent-mode fanout \
  --fanout-plan examples/fanout-plan.sample.json \
  --max-workers 2 \
  --execution-mode worktree
```

先看输入输出类型：

```text
multi_agent/domain/live.py             FanoutPlan、LiveSubagentResult、Summary
multi_agent/application/live_fanout.py 调度、gate、merge、finalize
multi_agent/application/fanout.py      纯 batching/conflict use case
multi_agent/adapters/local_worker.py   真实 AgentLoop worker
multi_agent/adapters/git_workspace.py  worktree/diff/apply
multi_agent/adapters/fanout_files.py    checkpoint/artifact
```

Fanout 只并发无依赖且 write scope 不冲突的任务。声明 scope overlap 会串行；实际越界、
patch apply 失败或 finalizer mutation 会 fail closed。

恢复：

```bash
forge run "audit independent runtime areas" \
  --agent-mode fanout \
  --fanout-plan examples/fanout-plan.sample.json \
  --fanout-resume .agent_forge/runs/<prior-run> \
  --max-workers 2
```

Resume 会校验 plan digest、base identity 和 patch hash，只复用 accepted worker artifact。

## 8. Benchmark 与 Claim Boundary

```bash
forge bench swebench \
  --regression-set core-five \
  --limit 1 \
  --provider deepseek \
  --direct-baseline
```

带 official harness 时再增加 `--evaluate`。阅读：

```text
bench/api.py
-> bench/application/swebench.py
-> bench/adapters/case_runtime.py
-> optional bench/adapters/official_evaluator.py
-> bench/application/diagnostics.py
-> bench/presentation/case_study.py/report.py
-> evaluation/application/scorecard.py
```

永远区分：candidate patch、local validation、runtime verifier、official resolved。

## 9. Evaluation 与数据闭环

```bash
forge eval feedback .agent_forge/runs/<run-id> \
  --outcome needs_work \
  --label context_miss

forge eval export-dataset .agent_forge/runs/<run-id> \
  --require-feedback

forge eval ablation <control-run> <treatment-run> \
  --factor tool_routing
```

阅读 owner：

```text
evaluation/domain/comparison.py     同一任务不同 variant
evaluation/domain/ablation.py       matched identity 和 paired delta
evaluation/application/scorecard.py denominator 与 read model
evaluation/adapters/feedback_dataset_files.py 反馈与隐私默认值
```

## 10. Workbench 工作台

```bash
forge ui --no-open
```

```text
workbench/api.py
workbench/application/services.py
workbench/ports/services.py
workbench/adapters/evidence_files.py
workbench/adapters/background_jobs.py
workbench/presentation/commands.py
workbench/presentation/http.py
```

Workbench 是本地 evidence console：它读取真实 artifact 并启动白名单 CLI command，
不是 hosted product。页面中的结论必须可追溯到 trace、scorecard、official result 或
human feedback。

## 11. 学习验收问题

完成后应能不看代码回答：

1. `AgentLoop.run` 为什么可以保持很短？
2. ToolRouter、PermissionPolicy、CommandPolicy、Sandbox 各解决什么问题？
3. HumanInput 与 Approval 为什么不能共用状态？
4. checkpoint resume 为什么不等于恢复模型上下文？
5. operation fingerprint 如何防止 stale approval？
6. Sequential roles 和 Live Fanout 的权衡是什么？
7. Fanout 为什么需要 declared scope 和 actual touched-file 双重检查？
8. Case study 为什么必须在 official evaluation 之后写？
9. Patch rate、local verification rate、official resolved rate 的 denominator 分别是什么？
10. Trace、usage read model、report、Workbench 之间为什么必须分层？

开发中遇到的新典型问题，按“现象、场景、根因、排查、修复、验证”追加到
[`../evaluation/failure-driven-improvements.md`](../evaluation/failure-driven-improvements.md)。

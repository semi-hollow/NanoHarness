# 持久化 Human Input 与 Live Fanout

这项设计补齐两处有意收敛范围的 runtime 缺口，同时避免把 Agent Forge 扩张成通用
distributed-agent platform：

1. 用真实可持久化的 stop/respond/resume 控制链替代模拟 `ask_human` 回答；
2. 将 dependency-aware fanout 接入运行在隔离 git worktree 中的真实 `AgentLoop` worker。

## 目标

- Human question 是持久化 control-plane event，不是阻塞 terminal input，也不是自动
  approved observation。
- `forge respond` 记录可审计答案；`forge resume` 将答案注入 continuation run。
- 结构化 fanout plan 可以通过真实 model/tool loop 并发执行独立 task。
- 每个 worker 都有独立 context、trace、usage report、tool allowlist 和 git worktree。
- 写入结果只有通过 scope 和 patch 检查后才会确定性合并。
- 已完成工作可以从 artifact 恢复，failed/blocked task 只重跑未完成部分。
- Report 同时呈现 wall-clock latency、worker latency 总和、token/cost、merge outcome 和
  conflict count。

## 不做什么

- 不实现 peer-to-peer agent chat、dynamic swarm、distributed queue 或 consensus。
- 不允许 shared workspace concurrent write。
- 不使用会静默扩大 worker scope 的自动 LLM conflict resolver。
- 不把 reviewer/verifier PASS 声称为 official benchmark resolution。
- 不为本地 filesystem store 声称 multi-user authentication。

## 持久化 Human Input

`HumanInputStore` 管理 `HumanInputRequest`：稳定 request id、thread id、question、可选
choices、status、answer、run/step/agent identity、workspace、timestamp 和 artifact path。
Request id 只接受系统生成的 24 位十六进制格式；写入经过 fsync 和 atomic replace。
标准化后的 choices 参与 identity，选项改变时不会错误复用旧答案。

```text
AgentLoop / ClarificationPolicy
  -> HumanInputStore.request(...)
  -> checkpoint WAITING_HUMAN + trace event
  -> 在后续 tool 前停止
  -> forge respond <request-id> --answer <text>
  -> request RESPONDED
  -> forge resume <run-dir>
  -> answer 追加到 continuation task 和 resume context
  -> AgentLoop 在相同 human thread id 下继续
```

`ask_human` 是 runtime control signal。直接执行该 tool 会 fail closed；`AgentLoop`
拦截调用并负责持久化和状态迁移。Side-effect approval 继续由独立 `ApprovalStore`
负责，因为审批决定和信息回答具有不同的 stale state 与 authorization 语义。

## 结构化 Fanout Plan

公共输入使用 JSON，使 dependency 和 write ownership 都是显式字段：

```json
{
  "goal": "Update runtime behavior and its focused tests",
  "tasks": [
    {
      "id": "runtime",
      "task": "Implement the runtime change",
      "depends_on": [],
      "write_scope": ["agent_forge/runtime/"],
      "allowed_tools": ["read_file", "grep_search", "apply_patch", "diagnostics"],
      "expected_artifact": "runtime_patch",
      "max_steps": 12
    },
    {
      "id": "tests",
      "task": "Add focused tests",
      "depends_on": [],
      "write_scope": ["tests/"],
      "expected_artifact": "test_patch",
      "max_steps": 8
    }
  ]
}
```

Scheduler 会检查 id 唯一性、dependency 是否存在、DAG 是否有环、worker 上限、每项
task 的 `max_steps`（2 到 32）、标准化 relative scope 和已知 tool name。Worker
使用 global budget 和 task budget 中的较小者。只读 task 的 `write_scope` 为空；
写入 task 至少声明一个 scope。

## Worker 与 Merge 流程

```text
validated DAG
  -> ready task 拆成无冲突 parallel batch
  -> 每个 worker 独占 detached worktree + AgentLoop + LLM client
  -> worker artifact / trace / usage / patch / touched-file list
  -> 实际 touched path 必须位于 declared scope
  -> 检查同 batch 实际 overlap
  -> git apply --check
  -> 按 task 顺序确定性 apply patch
  -> dependent batch 从已集成状态开始
  -> 最终只读 Aggregator/Verifier AgentLoop
  -> fanout_summary.json / fanout_report.md / integration.patch
```

Declared overlap 会被串行化，而不是并发执行。Undeclared actual overlap、scope escape
或 patch apply failure 会停止 merge，并记录 `conflict_resolution_required`；不会把
冲突交给无约束模型处理。

## 恢复

`fanout_checkpoint.json` 在开始工作前及每个 batch 后原子写入，记录 plan digest、
base commit、accepted task id、patch path 和 SHA-256。Resume run 必须匹配 plan digest
和 base commit，验证每个 accepted patch hash，在新的 integration workspace 重放这些
patch，跳过已完成 task，只重跑未完成 task。Dependency failure 会阻止下游任务，但
不会丢弃其他独立完成的 artifact。

所有 recovered artifact 先在 disposable worktree 中校验和重放；只有最终 combined
diff 会应用到真实 integration workspace，避免最后一个坏 artifact 留下 partial restore。

Worker human thread 使用 plan digest、base commit 和 task id，因此持久化 clarification
answer 可以跨 selective worker rerun。Per-operation manual write approval 不同：当前
operation identity 包含 ephemeral worktree path。因此 live write fanout 会拒绝
`--no-auto-approve-writes`，而不是假装 approval 可以安全重放。该授权边界应使用
single/sequential mode。

Candidate patch collection 被 run、benchmark、tool、coordinator 和 fanout 共用。它
包含 tracked change 和 untracked text/binary source file，同时排除 untracked
`.agent_forge` runtime artifact。Finalizer 在独立 disposable worktree 中运行，且
integration patch 保持对 `git_diff` 可见。Runtime 会比较 verification 前后的完整
binary patch；如果 verifier 产生修改，decision 会被阻断，变更也会被丢弃。

每个 worker worktree 都从记录的 `base_head` 创建，刻意不继承其他 checkout 的
uncommitted file 或 index state。写入型 fanout 会拒绝 dirty integration workspace；
需要使用 draft change 的调用者应先将它变成显式、可版本化 seed，而不是依赖 ambient
filesystem state。

## 证据与验收

- Human-input 测试证明 waiting 期间不会发生 model/tool side effect，response 会被
  持久化，resume context 包含真实回答。
- Fanout 测试使用真实 `AgentLoop`、确定性 LLM fixture、独立 worktree、实际 overlap、
  确定性 merge 和 selective resume。
- Real-provider smoke 运行两个只读 worker，记录并发 wall-clock 和 aggregate usage，
  同时保证仓库不被修改。
- Fanout metric 会区分本次 worker/finalizer、历史 resumed usage 和完整 evidence-chain
  usage；current worker time 与 wall time 分开呈现，不伪装成实测 speedup。
- 公共文档明确区分 sequential role coordination、live fanout、真实 side-effect
  approval、持久化 clarification 和 official evaluation。

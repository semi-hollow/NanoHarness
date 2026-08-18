# 多 Agent 编排

> 本文描述稳定版本当前已经实现的 **Live Fanout execution half**。
> Planner / Structured Handoff / Bounded Replan 属于后续 capability branch，不在本文冒充 CURRENT。

---

# 1. 当前 Multi-Agent 到底是什么

不是：

```text
Main Agent
↓
自由聊天
↓
一群共享 Memory 的 Subagents
```

当前 stable 版本是：

```text
Typed FanoutPlan
↓
Runtime Validation
↓
Dependency Ready Levels
↓
Conflict-free Batches
↓
Independent Worker AgentLoops
↓
Isolated Git Worktrees
↓
LiveSubagentResult + Candidate Diff
↓
Conflict Gates
↓
Stable Integration
↓
Read-only Finalizer
```

Coordinator：

```text
= deterministic orchestrator
≠ LLM
≠ shared WorkingMemory
```

---

# 2. FanoutPlan 是执行契约

```text
FanoutPlan
├ goal
└ tasks[]
   ├ id
   ├ task
   ├ depends_on
   ├ write_scope
   ├ allowed_tools
   └ max_steps
```

当前 Plan 来自外部结构化输入。

Coordinator 不从自然语言自动推断 DAG。

---

# 3. Scheduling 分两步

```text
depends_on
↓
Ready Level
↓
write_scope overlap
↓
Conflict-free Batches
```

例：

```text
A: pricing.py
B: shipping.py
C depends_on A,B
```

得到：

```text
Batch 0: A || B
Batch 1: C
```

`max_workers` 只限制同一 Batch 的线程并发上限，不决定 DAG。

---

# 4. Worker 为什么要独立 Context

每个 Worker 有独立：

```text
AgentRunSession
WorkingMemory
Conversation
RuntimeConfig
Tool Surface
Git Worktree
state / approval / ledger roots
```

不共享完整 Conversation。

当前稳定版本的“通信”主要是：

```text
Code-state handoff
→ Integrated Workspace
```

后续 capability branch 才增加 compact semantic `WorkerHandoff`。

---

# 5. Worktree 生命周期

```text
Worker starts
↓
create detached worktree
↓
seed current integration baseline
↓
AgentLoop read/edit/validate
↓
collect candidate diff
↓
collect touched_files / trace / usage
↓
return LiveSubagentResult
↓
cleanup
```

要区分：

```text
Worker Worktree
= 源码

Worker Artifact Dir
= Trace / usage / diff / evidence
```

---

# 6. Worker 返回什么

```text
LiveSubagentResult
├ task_id
├ status
├ final_answer
├ touched_files
├ candidate_diff_path
├ candidate_diff_sha256
├ trace_path
├ usage_path
├ batch_index
├ error
└ duration
```

Coordinator 不读取 Worker 的完整内部 Memory。

---

# 7. 四层 Conflict Gate

```text
Gate 1: Static Plan
declared write_scope overlap
→ 不同 Batch

Gate 2: Scope Violation
actual touched_files ⊄ declared write_scope
→ fail closed

Gate 3: Dynamic Result
同批 actual touched_files overlap
→ conflict / no merge

Gate 4: Merge Applicability
candidate patch against current integration state
→ merge_conflict
```

当前 stable：

```text
Conflict Detection         ✅
Automatic Conflict Repair  ❌
```

---

# 8. 为什么 Integration 要 stable order

Worker completion 可能：

```text
B → A → D → C
```

但 merge 使用：

```text
FanoutPlan task order
```

而不是 Future completion order。

原因：

> 相同 Plan 不应该因为线程调度或模型时延不同而改变 integration order。

---

# 9. 下一批为什么看得到上一批代码

```text
Original baseline
+
successful merged candidates
=
Integration Workspace
```

启动下游 Worker：

```text
fresh worktree
↓
seed current cumulative integration diff
↓
commit as new worker baseline
↓
run AgentLoop
```

所以代码状态通过 Workspace 传递，不靠复制上游 Conversation。

---

# 10. Finalizer 是什么

```text
Integrated Workspace
+
Worker results
↓
Read-only Finalizer AgentLoop
```

它可以：

```text
read
git status/diff
validation
```

不能：

```text
修改代码
偷偷 repair
```

它是 verification gate，不是最后一个 hidden Implementer。

---

# 11. 当前 stable 的最大缺口

```text
Natural Language Task
↓
???
↓
FanoutPlan
```

以及：

```text
semantic handoff
bounded conflict recovery
semantic replan
explicit acceptance criteria
```

这些正在新的 capability branch 中补齐。

---

# 12. 下一版目标，不冒充 CURRENT

```text
Natural Task
↓
Planner
↓
Single / Fanout Gate
↓
Validated FanoutPlan
↓
Isolated Workers
↓
Structured WorkerHandoff
↓
Integration
↓
Bounded Recovery / Replan
↓
Criteria-aware Finalizer
```

原则仍然是：

```text
LLM proposes
Runtime validates
```

---

# 13. 核心源码 Owner

```text
FanoutPlan
→ multi_agent/domain/live.py

dependency / batching
→ multi_agent/domain/fanout.py

orchestration / merge
→ multi_agent/application/live_fanout.py

worker / worktree / finalizer
→ multi_agent/adapters/local_worker.py
```

讲源码只问四件事：

```text
Input 是什么？
Runtime 做什么判断？
失败分支是什么？
Output 给谁？
```

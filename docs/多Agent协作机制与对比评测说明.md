# Multi-Agent Harness 设计与对比评测

Agent Forge 包含 coordinator-driven multi-agent harness。默认 role workflow 刻意保持
简单、顺序执行；另一条 fanout scheduler 在不改变标准 `AgentLoop` 的前提下，处理
dependency-safe parallel plan batch。

## 设计

标准 runtime 仍然是 `AgentLoop`，multi-agent execution 只是外层 orchestrator：

```text
MultiAgentCoordinator
  -> AgentLoop(role=Implementer 或 Researcher)
  -> ArtifactRepository 写入 role output
  -> AgentLoop(role=Reviewer)
  -> ArtifactRepository 写入 review
  -> AgentLoop(role=Verifier)
  -> ArtifactRepository 写入 verification
  -> 可选且有上限的 revision round
  -> multi_agent_summary.json + multi_agent_report.md
```

Agent 不会自由聊天，而是通过下面目录中的显式 artifact 协作：

```text
.agent_forge/runs/<run-id>/multi_agent/artifacts/
```

因此状态可以 replay，也可以解释。

每个 `RoleSpec` 还可以声明 revision-round tool allowlist。例如 `research_report` 的
`Researcher` 第一轮可以检查 local/MCP evidence，revision round 则只能使用 artifact。
这样修订会聚焦 review finding，而不是让模型重复收集证据。

Coordinator 还有轻量 artifact-quality gate。如果 role 返回 provider-specific raw
tool-call markup，而不是 expected artifact，run 不会假装 artifact 有效，而会触发
有界 revision。Artifact handoff 按 newest-first 渲染，使 reviewer 先看到当前 candidate，
避免旧 audit history 先耗尽 context budget。

## Dependency-Aware Fanout（依赖感知 Fanout）

对于 plan-style 工作，`agent_forge/multi_agent/application/live_fanout.py` 将 dependency scheduler
接到真实隔离 worker：

```text
Plan tasks
  -> 校验 DAG / path / tool / artifact name
  -> dependency-ready、conflict-free batch
  -> 每个 worker 独占 worktree + AgentLoop + LLM + trace
  -> actual touched-file 和 patch SHA 检查
  -> 在 integration workspace 确定性 git apply
  -> 隔离、只读 FanoutVerifier
  -> checkpoint / summary / report / integration.patch
```

这回答了与 `coding_fix` 不同的问题：用户有七八个 task 时，哪些可以同时运行，哪些
需要顺序 ownership。Declared overlap 会串行化；undeclared overlap、scope escape 或
patch apply failure 会停在 `conflict_resolution_required`，没有模型可以静默扩大 scope。
Checkpoint 能恢复 hash-verified accepted patch，并只重跑 incomplete worker。

Live fanout 是 local concurrency，不是 distributed serving。它接收显式 JSON plan，
不会让模型发明无限 swarm。

## Profile 说明

### `coding_fix` 配置

角色：

- `Implementer`：检查代码、修改 patch，在允许时运行 focused validation。
- `Reviewer`：只读 review candidate patch/output。
- `Verifier`：运行允许的 validation，或明确标记 validation blocked。

Reviewer 和 verifier 必须输出：

- `PASS`
- `NEEDS_REVISION`
- `BLOCKED`

`NEEDS_REVISION` 会触发新的 Implementer round，直到达到
`--max-revision-rounds`。

### `research_report` 配置

角色：

- `Researcher`：编写有 source 支撑的 report。
- `SkepticalReviewer`：识别 unsupported claim 和缺失 caveat。
- `FactVerifier`：根据已有 source 验证主要 claim。

Profile 可以离线工作；live search/fetch 不可用时，report 必须说明 source limitation。
配置 MCP web tool 后，research role 可以通过相同 tool registry path 使用它们。第一版
中 reviewer 和 verifier 只读 artifact，因此 decision 基于 draft/cited evidence，而不是
隐藏 extra browsing。

## CLI 使用

默认仍是 Single Agent：

```bash
forge run "fix the failing test" --provider deepseek
```

Multi-agent coding repair：

```bash
forge run "fix the failing test" \
  --agent-mode multi \
  --profile coding_fix \
  --provider deepseek \
  --max-revision-rounds 2
```

Research report：

```bash
forge run "Write a cited research report on current best practices for evaluating multi-agent coding systems. If live search is unavailable, clearly mark source limitations." \
  --agent-mode multi \
  --profile research_report \
  --provider deepseek \
  --max-steps 10 \
  --max-revision-rounds 2
```

Live read-only fanout：

```bash
forge run "audit runtime and safety evidence" \
  --agent-mode fanout \
  --fanout-plan examples/fanout-plan.sample.json \
  --max-workers 2 \
  --provider deepseek
```

Resume incomplete fanout run：

```bash
forge run "continue the validated task DAG" \
  --agent-mode fanout \
  --fanout-plan path/to/plan.json \
  --fanout-resume .agent_forge/runs/<previous-run-id> \
  --execution-mode worktree \
  --no-keep-worktree \
  --provider deepseek
```

SWE-bench 运行 coding profile：

```bash
forge bench swebench --showcase \
  --agent-mode multi \
  --profile coding_fix \
  --provider deepseek
```

SWE-bench 也可以在隔离 workspace 中比较 Single/Multi variant：

```bash
forge bench swebench --showcase \
  --agent-mode compare \
  --profile coding_fix \
  --provider deepseek \
  --max-revision-rounds 2
```

Compare mode 为每个 case 写 `comparison.json` 和 `evaluation_report.md`。Report 只能作为
quality/cost tradeoff evidence，不能得出“multi-agent 总是更好”的全局结论。

## 不做什么

当前版本不实现：

- Claude / Anthropic provider compatibility。
- Raft、quorum、blockchain、decentralized peer-to-peer agent 或 swarm learning。
- Distributed queue、remote worker 或 peer-to-peer agent chat。
- 对 undeclared/overlapping write 的自动 LLM conflict resolution。
- 跨 ephemeral fanout worktree 的 per-operation manual write approval；需要该授权边界
  时使用 single/sequential mode。
- Full SaaS/distributed serving。
- 大型 frontend product。

目标是成熟、可检查的 coordinator-driven harness，而不是庞大 distributed-agent 产品。

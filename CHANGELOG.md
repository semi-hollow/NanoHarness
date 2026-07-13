# 变更记录

## 版本 0.6.0 - 2026-07-12

- 用原子 pending/responded/cancelled request、同 turn 副作用 barrier、`waiting_human`
  checkpoint、`forge respond` 加 resume，替换模拟 `ask_human` 行为。
- 增加基于真实隔离 AgentLoop worker 的 validated live fanout，包括 declared/actual
  write-scope gate、确定性 binary patch integration，以及能看见 candidate diff 且带
  pre/post mutation gate 的隔离只读 finalizer；plan 支持低于 global runtime ceiling 的
  per-task step budget。
- 增加 incremental fanout checkpoint、plan/base identity check、patch hash、稳定 worker
  clarification thread、未完成 task selective rerun，以及 current/resumed/evidence-chain
  usage 分层记账。
- 统一 run、benchmark、tool、coordinator 的 candidate diff collection，使 tracked 和
  untracked source file 共用一条 evidence path，同时排除 untracked `.agent_forge` artifact。
- 增加受限 fanout UI/CLI、安全 sample plan、focused regression、带语义断言的真实
  provider smoke，并更新 capability/learning/failure 文档。
- 优化桌面和移动端本地工作台，提供稳定导航、可读表格、受限 control 和 fanout
  artifact view。
- 增加全仓库 function type contract、真实 validated `TraceEvent` envelope、显式
  checkpoint transition、mypy/AST regression gate，以及 object-first 代码阅读地图。

## 版本 0.5.0 - 2026-07-11

- 增加 per-case official SWE-bench result parsing，显式区分 resolved、unresolved、
  error、empty-patch、incomplete。
- 增加固定五仓库 regression set、denominator-aware scorecard 和 matched-run
  `forge eval ablation` report。
- 增加可观测 `task-aware` vs `all` tool-routing experiment，两侧保持相同 runtime
  safety chain。
- 增加隔离 snapshot 上的 OCI-container execution，包括 network/resource limit、
  capability removal、read-only root、command delegation 和 environment manifest
  replay evidence。

## 版本 0.4.0 - 2026-07-11

- 增加 completed run 的 human outcome 和 failure label capture。
- 增加 trace、tool-policy、environment、evaluation 和 feedback evidence 的
  privacy-conscious JSONL export。
- 将 local/detached-worktree execution mode 接入 public run/resume command，保留
  environment manifest 和 patch。
- 将 workbench 和文档主线调整为 runtime/evaluation evidence。
- 增加稳定 runtime capability guide、feedback-loop design note 和显式 roadmap boundary。

## 更早版本

早期版本建立了标准 AgentLoop、context construction、governed tools、human approval、
operation ledger、checkpoint resume、SWE-bench-shaped run、failure taxonomy 和 direct
baseline comparison 和 artifact-based multi-agent coordination。

# Evaluation Experiment 与 OCI Execution

这项设计将 runtime 行为连接到可跨 run 对比的证据，并刻意避免硬编码 benchmark
结论：scorecard 中的每个数字都必须来自 trace、usage artifact、candidate patch 或
official evaluator report。

## 目标

1. 将固定五 case SWE-bench Lite regression set 转成机器可读 scorecard，覆盖 patch
   能否到达、local validation、official outcome、token、cost、latency、tool failure
   和 failure class。
2. 只有 dataset、split、provider/model identity 和 case id 都匹配时，才将两个完整
   benchmark run 作为 paired ablation 比较。
3. 解析 official SWE-bench per-case report，而不是根据 evaluator process exit code
   推断正确性。
4. 增加 OCI-backed execution mode，在保留现有 runtime policy chain 的同时，让
   command/diagnostics tool 在隔离 repository snapshot 上的受限短生命周期 container
   中运行。

## 证据流

```text
SWE-bench case
  -> isolated checkout
  -> AgentLoop + governed tools
  -> trace.json + usage.json + patch.diff
  -> optional official SWE-bench report.json
  -> case evidence model
  -> scorecard.json / scorecard.md
  -> paired ablation.json / ablation.md
```

Evidence model 严格区分三个层级：

- Candidate patch：存在非空 diff。
- Local validation：test-oriented command 或 unittest diagnostic 成功完成；只有
  compilation 不能证明正确。
- Official resolved：per-case SWE-bench report 明确记录 `resolved: true`。

如果 official process 成功退出，但没有找到 per-case result，该 case 是
`official_eval_incomplete`，不是 resolved。Harness error、missing report、empty patch
和 unresolved patch 保持不同 outcome。

## Scorecard 与 Ablation

每个 benchmark run 会生成包含 per-case row 和 aggregate total 的 scorecard。Rate
使用显式 denominator；没有 case 被 official evaluate 时，official resolved rate
保持 `null`，不能写成误导性的 `0%`。

Ablation comparator 接收两个 run directory。Dataset、split、provider/model identity
或 case set 不一致都会被拒绝。Report 展示 paired delta，并始终说明每个 variant 只有
一次 run 无法估计随机方差。Tool-routing ablation 可以选择 all registered tools 或
task-aware routed tools；两侧仍启用 path、command、approval 和 sandbox policy。

Official quality delta 只使用两侧都有 resolved/unresolved evidence 的 case。如果一侧
新增或丢失 official evaluation coverage，report 会把它标记为 evidence change，并说明
full-set official correctness 不可比较；不能把 denominator 变化伪装成质量提升。

## OCI 执行边界

`ExecutionEnvironment` 仍是统一 runtime interface。OCI mode 会创建隔离 git worktree
snapshot，启动 named container，使用 read-only root filesystem、drop Linux
capability、`no-new-privileges`、CPU/memory/PID limit，并在 run policy 为 `deny` 时
关闭 container network。Snapshot 以读写方式挂载到 `/workspace`，使 file tool 和
container command 观察同一份 repository state。

普通 repository run 和 SWE-bench case run 都使用该 adapter。Benchmark scorecard
记录 execution mode、network policy、retention policy、image 和 resource limit，也会
聚合 runtime 报告的 immutable image ID。除非 execution environment 本身就是声明的
实验 factor，否则 ablation comparator 会拒绝这些字段发生 drift。

Command 和 unittest diagnostics 通过 environment adapter 执行。普通 file tool 仍在
host process 中运行，并受 `WorkspaceSandbox` 限制，只能访问 mounted snapshot。这比
local/worktree mode 提供更强 process isolation，但不声称 hostile multi-tenant security。

Environment manifest 会记录 image identity、container id、resource/network policy、
start command、command history 和 cleanup policy。Cleanup 始终尝试强制移除 container；
snapshot 则遵循现有 keep/remove policy。只有 snapshot 同时保留时才记录 recreate
command，避免 manifest 提供已经失效的 replay path。

## 验证契约

- Official result fixture 覆盖 resolved、unresolved、error、empty-patch、missing-report。
- Scorecard 测试证明 candidate patch 和 official resolution 使用不同 denominator。
- Ablation 测试拒绝不可比较 run，并计算 paired metric delta。
- OCI 测试在不要求 unit-test 环境安装 Docker 的前提下，断言真实 runtime command、
  limit、mount、network policy、command delegation、manifest evidence 和 cleanup。
- Real OCI smoke 依赖环境；没有 compatible container runtime 时必须明确报告 skipped。

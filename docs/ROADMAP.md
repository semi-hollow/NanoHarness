# 路线图

NanoHarness 刻意保持精简。Roadmap item 必须增强 runtime control、reproducible
evaluation 或 evidence quality；单纯增加功能宽度，不足以成为新增子系统的理由。

## 近期方向

1. 对固定 regression set 做多 seed ablation，保留带置信信息的 aggregate evidence，
   避免从单次 run 得出结论。
2. 为 exported run evidence 增加 dataset manifest、显式 redaction policy 和 schema
   migration。
3. 多次重复 matched serial/fanout plan，报告 latency、token cost、conflict rate 和
   verifier outcome 的置信范围。
4. 增加真实 OCI smoke lane 和 project-specific dependency image contract，同时保持
   unit test 不依赖 Docker。

## 设计债务

- 让 UI、diagnostics 和 report 共用一个 artifact locator。
- 统一有重叠的 command、path、execution-environment policy summary。
- 将重复 provider compatibility fix 提升为 versioned transport compatibility layer。
- 在允许 per-operation manual write approval 跨 ephemeral fanout worktree 前，定义稳定
  operation identity；当前 fanout 对该组合 fail fast。
- 进程被强制终止后，检测和清理 run-owned abandoned worktree，同时不碰用户创建的
  worktree。
- Failure rule 保持从 environment/evidence failure 到 agent behavior failure 的优先级，
  每种优先级冲突都必须有 regression case。

## 完成标准

一项 roadmap 工作只有同时满足以下条件才算完成：改变真实 runtime path；留下机器
可读 artifact；有 focused regression test；明确新证据不能证明什么。

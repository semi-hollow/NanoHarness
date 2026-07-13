# 评测指南

Agent Forge 把 SWE-bench-style evaluation 作为主要效果闭环。

这里的目标不是宣称 leaderboard performance，而是让每一次 coding-agent run 都可复现、可检查：

1. 加载一个公开 benchmark case；
2. 在精确的 base commit checkout 目标 repository；
3. 让 agent 通过 tools 完成 inspect、patch、validate；
4. 输出兼容 SWE-bench 的 `predictions.jsonl`；
5. 可选调用官方 SWE-bench harness；
6. 生成包含 trace、usage、failure taxonomy、failure diagnosis 的 result card。

## 为什么使用 SWE-bench

SWE-bench 通过让模型或 agent 针对真实 repository 生成 patch，评测它能否解决真实 GitHub software issues。相比作者自造 fixtures，这更贴合本项目目标。

官方 evaluation harness 基于 Docker，资源开销可能较高。本地开发时先跑小样本：

```bash
forge bench swebench --showcase --provider deepseek --direct-baseline
forge report latest
```

用 coordinator-driven coding profile 跑同一个固定 reference case：

```bash
forge bench swebench --showcase --agent-mode multi --profile coding_fix --provider deepseek
```

用隔离 workspace 跑同一个固定 reference case 的 single-agent 与 multi-agent variants：

```bash
forge bench swebench --showcase --agent-mode compare --profile coding_fix --provider deepseek --max-revision-rounds 2
```

单个 showcase loop 稳定后，再使用固定 regression set：

```bash
forge bench swebench --regression-set core --provider deepseek --direct-baseline
```

固定 regression loop 稳定后，再扩大到更大的 sample：

```bash
forge bench swebench --limit 20 --provider deepseek --direct-baseline
```

调用 official harness evaluation：

```bash
forge bench swebench --limit 5 --provider deepseek --evaluate --max-workers 1
```

## 指标

result card 会报告：

- `patch_generated`：Agent Forge 是否产出了非空 git diff。
- `official_eval_*`：SWE-bench harness 是否运行，以及是否接受或拒绝 patches。
- token 使用：prompt、completion、cache hit/miss、estimated cost。
- latency：model call latency 与 tool duration。
- context breakdown：prompt budget 分别花在 files、memory、tools、history 上的比例。
- 工具效率：tool count、success rate、failed observations、observation size。
- 失败分类：blocked、no patch、official eval failed、provider/config failure。
- failure diagnosis：每个 case 的 machine-readable failure class、evidence 与 next actions。

## 基线（Baseline）

`--direct-baseline` 会用一次 LLM call 生成 no-tools prediction file。它刻意保持简单，用来回答一个关键架构问题：

> 为什么要用 agent loop，而不是一个 prompt？

Agent Forge 应该在同一组 case subset 上与这个 baseline 对比。agent loop 预期会花更多时间和 tokens，但它可以 inspect files、run tools、从 failed actions 中恢复，并用 trace evidence 支撑最终答案。

## Single vs Multi-Agent 对比

`forge bench swebench --agent-mode compare` 会把同一个 case 跑两次：

1. `single`：canonical `AgentLoop`。
2. `multi`：`MultiAgentCoordinator(coding_fix)`。

两个 variants 使用隔离 workspaces，避免 patches 互相污染。每个被对比的 case 会写出：

- `comparison.json`
- `evaluation_report.md`

report 包含 status、patch presence、cost、LLM calls、tool calls、failed tools、revision rounds、reviewer findings、verifier status、failure lens，以及保守 recommendation。不要宣称 multi-agent 全局更好；只报告每个 case 的 quality/cost tradeoff。

## 如何解读结果

不要把 `patch_generated` 当成 solved。生成的 patch 只是 candidate。可信的 resolved signal 来自官方 SWE-bench evaluation。

本地推进时建议按这个顺序看：

1. 单个 case 先出现 `patch_generated`。
2. trace 显示 relevant files 被选中。
3. usage report 显示没有 runaway cost 或 repeated actions。
4. 已有 direct baseline comparison。
5. official harness 对 prediction 完成 evaluation。
6. failed cases 按 failure taxonomy 分组。
7. 在固定 regression set 上重复运行，观察 harness change 是改善还是回退同一组 cases。

## 资源说明

SWE-bench evaluation 使用 Docker。在 Apple Silicon 上，official images 可能需要本地构建；Agent Forge 会在 Darwin arm64 evaluation runs 中自动传入 empty namespace flag。

如果本地硬件太慢，可以先在本地生成 predictions，再到 cloud x86 machine 上 evaluation：

```bash
forge bench swebench --limit 20 --provider deepseek
scp .agent_forge/runs/<run-id>/predictions.jsonl <cloud-host>:/tmp/
```

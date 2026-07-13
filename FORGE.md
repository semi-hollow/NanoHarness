# Agent Forge 开发说明

## 项目方向

Agent Forge 是面向 SWE-bench 的 CodingAgent Harness。主线应持续聚焦：

1. 公开 benchmark case；
2. 在 base commit 上创建干净 repo checkout；
3. 由 AgentLoop 驱动 tool execution；
4. SWE-bench-compatible `predictions.jsonl`；
5. trace、usage、denominator-aware scorecard 和 result card；
6. 解析 per-case official SWE-bench harness evaluation；
7. 使用 matched run identity 做 paired runtime ablation。

不要重新把自写 benchmark narrative、教学 fixture 或 simulated-model product path
作为能力证据。

## 常用命令

```bash
forge doctor
forge run "read this project structure and explain the entrypoints without editing files" --provider deepseek
forge run "阅读这个项目结构并说明入口，不要修改文件" --provider deepseek
forge skills list
forge bench swebench --limit 1 --provider deepseek --direct-baseline
forge bench swebench --regression-set core --provider deepseek --tool-routing task-aware
forge eval ablation <control-run> <treatment-run> --factor tool-routing
forge report latest
forge replay latest
scripts/verify.sh
```

## 编辑约定

- Public entrypoint 保持 goal-based：`run`、`bench`、`report`、`replay`、`doctor`、`tui`。
- 不重新增加 public `single/multi/workflow` mode；用户命令保持 goal-based。
- 不增加 calculator/webhook/tutorial fixture、simulated LLM product path 或 passive sample
  config。能力必须真实影响 `forge run`、benchmark、trace evidence 或 operator workflow。
- Generated artifact 统一放在 `.agent_forge/`。
- 不提交 API key、provider profile、raw run trace 或 benchmark workspace。
- 如果功能既不支持 SWE-bench loop，也不能提高该 loop 的 explainability，应重新判断
  它是否属于本项目。

## Runtime 事实

- `AgentLoop` 是标准执行路径。
- `agent_forge/bench` 负责 benchmark loading、checkout、prediction 和 result card。
- `TraceRecorder` 是 replay 和 usage report 的 source of truth。
- `ModelGateway` 是 runtime 使用的唯一 provider boundary。
- `ToolRegistry`、`CommandPolicy`、`WorkspaceSandbox` 构成 tool safety boundary。
- 内置 Coding Skill 已接入真实 runtime，必须影响 prompt context、tool routing 或 trace
  evidence；不要添加从未被 `AgentLoop` 使用的 passive manifest。

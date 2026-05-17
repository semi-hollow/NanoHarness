# 06 个人学习清单

这份清单用来判断你是否真的掌握了项目。不要只看文档，最好每一项都亲手跑或打开代码确认。

## 第一阶段：能跑

- [ ] 在 macOS 跑通 `scripts/setup_macos_local.sh`。
- [ ] 在 WSL 跑通 `scripts/setup_wsl_local.sh`。
- [ ] 跑通 `scripts/run_all_modes.sh`。
- [ ] 跑通 `scripts/verify.sh`。
- [ ] 知道 `tool_observation success=False` 为什么不一定是失败。

## 第二阶段：能定位代码

- [ ] 知道 `run_demo.py` 只是薄入口。
- [ ] 能说清 `cli.py` 里 single/multi/workflow 三个分支。
- [ ] 能说清 `single` 直接走 `AgentLoop`，`multi` 通过 `AgentRuntime` 间接复用 `AgentLoop`。
- [ ] 能说清 `AgentLoop.run` 的主流程。
- [ ] 能找到 `ToolRegistry.execute`。
- [ ] 能找到 command policy 和 workspace sandbox。
- [ ] 能找到 trace 是在哪里写的。
- [ ] 能找到 eval runner 是在哪里执行 verify.py 的。

## 第三阶段：能读 trace

- [ ] 能打开 `trace-single.pretty.json`。
- [ ] 能找到第一次 `llm_call`。
- [ ] 能找到第一次 `tool_call`。
- [ ] 能找到 patch 失败的 `tool_observation`。
- [ ] 能找到 recovery patch。
- [ ] 能找到最终 `final_answer`。
- [ ] 能在 `trace-multi.pretty.json` 找到 handoff。

## 第四阶段：能换 LLM

- [ ] 知道默认是 MockLLM。
- [ ] 能用 `--llm openai --base-url --api-key --model` 跑 OpenAI-compatible API。
- [ ] 能复制 `llm_profiles.example.json` 到 `llm_profiles.json`。
- [ ] 能用 `--llm-profile ollama-qwen`。
- [ ] 知道真实 key 不能提交。
- [ ] 能解释为什么单测仍然用 MockLLM。

## 第五阶段：能讲项目

- [ ] 能 30 秒讲清项目是什么。
- [ ] 能 1 分钟讲清整体架构。
- [ ] 能画出 CLI -> AgentLoop -> LLM -> ToolRegistry -> Observation -> Trace。
- [ ] 能解释 workflow 和 agent 的区别。
- [ ] 能解释 supervisor/subagent 为什么需要 handoff。
- [ ] 能解释当前 multi mode 是 runtime-backed 顺序 DAG，不是完整并发 scheduler。
- [ ] 能解释 eval 为什么不是硬编码。

## 第六阶段：能回答深挖

- [ ] 如果问 sandbox 局限，你能说它不是容器级隔离。
- [ ] 如果问 command policy，你能说网络命令和危险命令风险。
- [ ] 如果问 context，你能说 repo map、retrieval、memory、symbol search、file ranking。
- [ ] 如果问 LLM provider，你能说 OpenAI-compatible 和 profile。
- [ ] 如果问生产化，你能说并发 scheduler、ownership/conflict merge、LSP、JSON Schema、容器 sandbox、真实 cost accounting。

## 推荐练习顺序

第 1 天：

```bash
scripts/run_all_modes.sh
scripts/verify.sh
```

读：

- `docs/study-pack/01-code-map-and-architecture.md`
- `docs/study-pack/03-run-modes-and-trace-reading.md`

第 2 天：

读：

- `agent_forge/cli.py`
- `agent_forge/runtime/agent_loop.py`
- `docs/study-pack/02-key-file-walkthrough.md`

第 3 天：

读：

- `agent_forge/tools/registry.py`
- `agent_forge/safety/*.py`
- `agent_forge/context/*.py`

第 4 天：

读：

- `docs/study-pack/04-interview-narrative.md`
- `docs/study-pack/05-deep-dive-prep.md`
- `docs/study-pack/07-design-context-and-tradeoffs.md`

第 5 天：

自己不看文档，口头回答：

```text
这个项目是什么？
single mode 怎么跑？
multi mode 为什么要 supervisor？
为什么当前 multi 是顺序 DAG 而不是并发 scheduler？
workflow 和 agent 有什么区别？
为什么默认 MockLLM？
怎么接公司 API？
安全边界在哪里？
trace 有什么价值？
eval 为什么可信？
下一步怎么生产化？
```

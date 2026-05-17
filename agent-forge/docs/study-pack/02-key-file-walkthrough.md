# 02 关键代码文件导读

这份文档按“你打开文件时该看什么”的方式写。重点不是逐行翻译，而是让你知道每个函数在项目里的位置。

## 1. `run_demo.py`

作用：项目最外层入口。

代码很短：

```python
import argparse
from agent_forge.cli import main
if __name__=="__main__": main()
```

你要理解：

- 真正逻辑不在这里。
- 它只是方便你运行 `python run_demo.py ...`。
- 面试时可以说：entrypoint intentionally thin, real orchestration lives in `agent_forge/cli.py`.

## 2. `agent_forge/cli.py`

作用：把用户命令转成一次具体运行。

### 关键函数

| 函数 | 作用 |
| --- | --- |
| `reset_demo_repo` | 每次 single/multi demo 前，把 demo bug 重置成 `return a - b`。 |
| `build_registry` | 创建 sandbox，注册所有内置工具。 |
| `build_llm` | 根据 LLMConfig 选择 MockLLM 或 OpenAI-compatible LLM。 |
| `build_parser` | 定义 CLI 参数。 |
| `main` | 解析参数，按 mode 分发到 single/multi/workflow。 |

### 参数怎么理解

| 参数 | 含义 |
| --- | --- |
| `task` | 用户任务，默认是修复 demo repo 的测试失败。 |
| `--workspace` | 工具执行的工作区边界。默认当前目录。 |
| `--mode single` | 跑完整 AgentLoop。 |
| `--mode multi` | 跑 Supervisor + subagents。 |
| `--mode workflow` | 跑确定性 workflow。 |
| `--llm mock` | 默认 MockLLM，离线稳定。 |
| `--llm openai` | 使用 OpenAI-compatible API。 |
| `--llm-profile` | 从本地 `llm_profiles.json` 读模型配置。 |
| `--base-url` | OpenAI-compatible base URL。 |
| `--api-key` | API key。真实 key 更推荐用环境变量或本地 profile。 |
| `--model` | 模型名。 |
| `--trace-file` | trace JSON 输出路径。 |
| `--no-auto-approve` | 关闭自动写入审批，用来观察 human approval。 |

### mode 分支

```text
if mode == "multi":
    SupervisorAgent().run(...)
elif mode == "workflow":
    run_workflow(...)
else:
    AgentLoop(...).run(...)
```

这段分支是整个项目最重要的“实现背景”之一：

- `single` 进入完整 `AgentLoop`，这是核心 runtime。
- `multi` 没有进入 `AgentLoop`，因为它当前只演示 supervisor 如何按阶段把任务交给几个角色对象。
- `workflow` 也没有进入 `AgentLoop`，因为它只是固定状态机，用来做对照组。

不要把这个设计误读成“多 agent 不需要 agent loop”。更准确的理解是：

```text
当前版本：
  single   = 完整 agent runtime
  multi    = 角色编排 demo
  workflow = 固定流程 demo

生产演进：
  supervisor 调度多个 AgentLoop-backed subagents
```

面试讲法：

> CLI does not contain the agent algorithm. It composes runtime dependencies: trace recorder, tool registry, LLM client, and the selected execution mode.

## 3. `agent_forge/runtime/llm_config.py`

作用：把模型配置从不同来源统一成一个 `LLMConfig`。

优先级：

```text
CLI flags > llm profile > environment variables > empty/default
```

支持三种使用方式：

```bash
python run_demo.py --mode single --llm openai --base-url ... --api-key ... --model ...
```

```bash
export AGENT_FORGE_BASE_URL=...
export AGENT_FORGE_API_KEY=...
export AGENT_FORGE_MODEL=...
python run_demo.py --mode single --llm openai
```

```bash
python run_demo.py --mode single --llm-profile ollama-qwen
```

面试讲法：

> I separated LLM configuration from the LLM HTTP client. That makes model switching explicit and keeps provider-specific secrets out of code.

## 4. `agent_forge/runtime/llm_client.py`

作用：提供 LLM 抽象和两个实现。

### `MockLLMClient`

它不是智能模型，而是一个确定性脚本化模型。它根据已经收到多少条 tool observation，决定下一步返回哪个 tool call。

single demo 的行为：

```text
0 observations -> read calculator.py
1 observation  -> read test_calculator.py
2 observations -> intentionally wrong patch
3 observations -> recovery patch
3 or 4         -> run unittest
after tests    -> final answer
```

为什么这么设计：

- demo 稳定；
- tests 稳定；
- 可以展示 tool failure recovery；
- 不依赖外部 API。

### `OpenAICompatibleLLMClient`

它用标准库发 HTTP 请求到：

```text
{base_url}/chat/completions
```

它负责：

- 组装 messages；
- 组装 tools schema；
- 发送请求；
- 解析 `content`；
- 解析 `tool_calls`；
- 把异常变成结构化 `AgentResponse.error`。

面试讲法：

> The client is intentionally small and SDK-free. It is enough to prove OpenAI-compatible integration, tool-call parsing, and invalid-response handling.

## 5. `agent_forge/runtime/agent_loop.py`

作用：项目最核心的 single-agent loop。

主流程：

```text
input guardrail
  -> init messages/state/memory
  -> for each step:
       build repo map
       build context report
       plan summary
       call LLM
       if final answer:
           output guardrail
           trace final
           return
       for each tool call:
           tool guardrail
           permission check
           optional human approval
           execute tool through registry
           save observation
           append messages
  -> max steps
```

你读这个文件时，先抓住这些变量：

| 变量 | 含义 |
| --- | --- |
| `messages` | 给 LLM 的对话历史，包括 user、assistant tool_call、tool observation。 |
| `state` | 当前任务状态，记录 iteration、observations、final answer。 |
| `policy` | 决定某个 action 是否允许、拒绝、需要审批。 |
| `memory` | 保存轻量任务记忆和 observation 摘要。 |
| `tool_history` | 用来检测重复 tool call。 |
| `ran_tests` | 用于 output guardrail，防止没跑测试却说测试通过。 |
| `blocked` | 标记是否发生过安全阻断。 |
| `consecutive_failures` | 连续失败计数，避免无限失败循环。 |

最关键的工程点：

- LLM 不直接执行任何东西；
- 所有工具都走 `ToolRegistry`；
- 执行前有 guardrail 和 permission；
- 执行后统一变成 Observation；
- 每个关键事件都写 trace。

## 6. `agent_forge/tools/registry.py`

作用：工具中心路由。

你要看：

- `register`：把工具放进 registry。
- `schemas`：暴露给 LLM 的工具说明。
- `execute`：按名字执行工具。
- unknown tool 不崩溃，而是返回失败 Observation。

面试讲法：

> ToolRegistry is the contract boundary between model-generated tool calls and executable local tools.

## 7. `agent_forge/tools/run_command.py`

作用：执行命令。

重点：

- 命令先过 command policy；
- 不应该无限制执行任意 shell；
- 输出被封装成 Observation；
- exit code 会进入 observation content。

面试时可以说：

> Command execution is the riskiest tool, so it must be policy-controlled and observable.

## 8. `agent_forge/safety/permission.py`

作用：决定 action 是 allow、ask 还是 deny。

典型逻辑：

- read 通常允许；
- write/patch 可能要 approval；
- dangerous command 拒绝；
- auto approve 只是 demo 模式，不代表生产默认。

## 9. `agent_forge/safety/sandbox.py`

作用：限制文件访问边界。

重点：

- 路径必须在 workspace 内；
- 敏感路径拒绝；
- 防止工具读写工作区外的文件。

## 10. `agent_forge/context/context_builder.py`

作用：把 repo map、retrieval、memory、file ranking、budget 信息合成给 LLM 的上下文报告。

面试讲法：

> Context is treated as a first-class runtime component, not just a prompt string.

## 11. `agent_forge/agents/supervisor_agent.py`

作用：multi mode 的总控。

先说结论：这里是教学版 multi-agent，不是生产级 multi-agent。

当前代码把顺序写死：

```text
PlannerAgent
  -> CodingAgent
  -> TesterAgent
  -> CodingAgent retry if tests fail
  -> TesterAgent retest
  -> ReviewerAgent
```

为什么这么写：

- 让你一眼看到 supervisor 如何 handoff；
- 让 trace 里能看到每个角色的责任边界；
- 让 tester 失败后回到 coder 的 retry 逻辑变得明确；
- 避免一开始就引入 DAG scheduler、并发、patch merge 等复杂问题。

它缺什么：

- subagent 不是独立 AgentLoop；
- 没有并发；
- 没有动态任务拆分；
- 没有 per-agent context；
- 没有冲突合并；
- 没有结构化 artifact contract。

所以你读这个文件时，不要纠结“为什么这么死板”。它的作用是把最基础的 supervisor 模型摊开给你看。

你要看：

- phase 如何切换；
- handoff payload 如何传；
- tester 失败后如何回到 coding；
- reviewer 如何收尾。

面试讲法：

> Subagents are coordinated by a supervisor. They do not call each other freely, which keeps control flow auditable.

更完整的面试讲法：

> In this project, multi mode is intentionally a minimal supervised workflow. It is not meant to prove production-grade multi-agent scheduling. The production version would make each subagent an AgentLoop-backed worker with role-specific prompts, context retrieval, tool permissions, and stop conditions, while the supervisor manages a task DAG, retries, conflicts, and aggregation.

## 12. `agent_forge/eval/eval_runner.py`

作用：真实跑 eval cases。

重点：

- 每个 case 都有 `task.md` 和 `verify.py`；
- runner 用当前 Python 执行 verify；
- 结果写 `eval_report.md`；
- 这不是硬编码全通过。

面试讲法：

> The benchmark is executable. Each case defines both user intent and verification logic.

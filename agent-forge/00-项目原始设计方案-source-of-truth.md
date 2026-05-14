# Codex 项目生成指令：Agent Forge

  

## 角色

  

你是资深 AI Agent 工程师、Coding Agent 架构师、DevTools 工程师，同时也是一个善于写教学文档的技术导师。

  

我要你直接生成一个完整、可运行、可学习、可面试讲解的 Agent 工程项目。

  

这个项目不是聊天机器人，不是 OpenCode 配置包，不是 Claude 复制品，也不是模型训练项目。

  

项目目标是：

  

> 生成一个精炼但覆盖 Agent 开发面试高频问题的工程项目，并配套生成一套类似 nanoAgent 风格的简明教学文档，帮助我通过项目反向学习 Agent 工程。nanoAgent github链接（供参考）：https://github.com/GitHubxsy/nanoAgent

  

---

  

## 面试强信号导向

  

这个项目不仅要“能跑”，还要能支撑项目深挖时从 `meets bar` 讲到 `strong hire`。

  

请在文档和面试材料中重点体现三个信号：

  

1. **Technical depth**  
   - 必须能讲清 trade-off、bottleneck、root cause、benchmark、failure mode。  
   - 不要只罗列功能，要解释为什么这么设计。

  

2. **Ownership**  
   - 面试表达中要体现“我如何提出方案、拆分问题、推动验证、处理风险”。  
   - 使用第一人称表达，但不要夸大：`我设计了...`、`我验证了...`、`我权衡了...`。

  

3. **Impact / Evidence**  
   - 不允许编造业务影响。  
   - 可以使用本项目真实可生成的指标作为证据，例如：  
     - eval case 通过率；  
     - 单 Agent 修复成功；  
     - 多 Agent handoff trace 完整；  
     - dangerous command blocked；  
     - test pass；  
     - tool call count；  
     - steps count；  
     - trace coverage。  
   - 所有数字必须来自项目运行结果，或者明确标注为“后续接真实模型后再补充”。

  

文档里必须避免流水账式表达：

  

错误表达：

  

```text  
I built an agent project with Python, argparse, unittest, tools, and docs.  
```

  

正确表达：

  

```text  
I built a compact Agent Harness to answer one question: how can we turn an LLM from a text generator into a controlled execution system for code tasks? The hardest part was not calling the model, but making tool execution safe, observable, and evaluatable. I implemented a single-agent loop, a supervisor/subagent workflow, permission sandboxing, trace logging, and an eval benchmark to verify whether the agent actually completed the task.  
```

  

---

  

## Project Deep-Dive 文档目标

  

请额外生成面试讲解材料，要求能支持以下场景：

  

1. **3 分钟项目开场**  
   - 第 1 分钟：项目背景 + 我负责的范围；  
   - 第 2 分钟：最难的问题 + 方案 + trade-off；  
   - 第 3 分钟：结果证据 + 反思。

  

2. **4 层追问准备**  
   每个核心技术点都要准备：  
   - What did you do?  
   - Why this approach?  
   - What went wrong?  
   - What else did you consider?

  

3. **架构图表达**  
   必须提供一个适合白板/飞书文档/面试口述的 ASCII 架构图，展示：  
   - CLI / User Task；  
   - Agent Runtime；  
   - Supervisor / Subagents；  
   - Tool Registry；  
   - Context Builder；  
   - Permission / Sandbox / Guardrails；  
   - Trace / Eval。

  

4. **Strong-hire 叙事**  
   面试材料要突出：  
   - 我不是简单调 API；  
   - 我不是只会用框架；  
   - 我关注的是 Agent 从 demo 到生产化会遇到的实际工程问题；  
   - 我能讲清楚方案选择、失败模式、替代方案和下一步改进。

  

---

  

## 一、项目名称

  

agent-forge

  

---

  

## 二、一句话定位

  

Agent Forge 是一个面向 Agent 工程面试和生产化理解的综合型 Agent Harness 项目。

  

它以 Coding Agent Demo 为主线，同时覆盖：

  

- 单 Agent Loop  
- Tool Calling  
- Observation 回传  
- Workflow vs Agent  
- 多 Agent Supervisor/Subagent  
- Handoff  
- Context Engineering  
- Memory / 简化 RAG  
- Permission / Sandbox  
- Guardrails  
- Human-in-the-loop  
- Observability / Tracing  
- Eval Benchmark  
- Production Readiness  
- 面试 Q&A  
- nanoAgent 风格学习文档

  

---

  

## 三、我的实际情况

  

我目前不是 Agent 框架专家。

  

我对以下具体技术实现还不熟：

  

- MockLLM 是什么；  
- argparse 怎么写 CLI；  
- unittest 怎么写测试；  
- tool schema 怎么设计；  
- trace 怎么设计；  
- eval runner 怎么写；  
- guardrails 怎么落地；  
- supervisor/subagent 代码怎么组织。

  

所以不要假设我已经能做技术选型。

  

请你基于以下原则自动选择实现方案：

  

### 技术选择原则

  

1. 选择最常见、最容易被面试官理解的方案；  
2. 选择设计思路可迁移的方案，而不是冷门技巧；  
3. 优先使用 Python 标准库；  
4. 不要引入复杂依赖；  
5. 每个关键技术点都要在教学文档中解释：  
   - 它是什么；  
   - 为什么需要；  
   - 在本项目中怎么实现；  
   - 面试时怎么讲；  
   - 后续生产化可以怎么增强。

  

例如：

  

- 用 `argparse` 做 CLI，因为它是 Python 标准库，面试官容易理解；  
- 用 `unittest` 做测试，因为它是 Python 标准库，不会因为 pytest 依赖卡住；  
- 用 `MockLLM` 做默认模型，因为项目必须在没有 API Key 的情况下可运行；  
- 用简单 keyword RAG，不上向量数据库，因为第一版要讲清检索思想，而不是被向量库部署卡住；  
- 用 JSON trace，因为可读、可审计、可扩展；  
- 用 allow / ask / deny 权限模型，因为这是 Agent 工程里最通用的风险控制表达。

  

这些技术选择都要写进文档中，帮助我学习。

  

---

  

## 四、项目边界

  

### 要做

  

实现一个 Agent Engineering Lab，包含：

  

1. 可运行的 Coding Agent Demo；  
2. 单 Agent Runtime；  
3. 多 Agent Supervisor / Subagent 模式；  
4. Handoff 机制；  
5. Workflow vs Agent 两种执行方式；  
6. 工具调用系统；  
7. 上下文装配；  
8. 简化 Memory；  
9. 简化 RAG / Repo Map；  
10. 权限和沙箱；  
11. Guardrails；  
12. Human-in-the-loop 模拟；  
13. Tracing / Observability；  
14. Eval Benchmark；  
15. Production Readiness 文档；  
16. 面试 Q&A；  
17. nanoAgent 风格的教学文档。

  

### 不做

  

不要做：

  

- 不训练模型；  
- 不复制 Claude 模型；  
- 不复制 OpenCode 全部功能；  
- 不做花哨 UI；  
- 不做 Web 后台；  
- 不做复杂分布式平台；  
- 不绑定具体业务领域；  
- 不依赖真实 OpenCode；  
- 不默认访问外网；  
- 不默认执行危险命令；  
- 不写一堆空文档；  
- 不使用复杂依赖让项目跑不起来。

  

---

  

## 五、技术要求

  

使用 Python。

  

要求：

  

- Python 3.10+；  
- 优先使用标准库；  
- 默认使用 MockLLM，保证无 API Key 也能跑；  
- 可选支持 OpenAI-compatible API；  
- 测试使用 unittest；  
- CLI 使用 argparse；  
- 所有路径必须限制在 workspace 内；  
- 所有命令执行必须经过 permission 检查；  
- 所有 agent run 必须生成 trace；  
- 所有 eval 必须能生成 markdown 报告；  
- 文档要能指导新手理解代码；  
- 所有代码尽量简单、直接、可读。

  

---

  

## 六、目录结构

  

请创建如下项目：

  

```text  
agent-forge/  
  README.md  
  pyproject.toml  
  run_demo.py

  

  agent_forge/  
    __init__.py  
    __main__.py  
    cli.py

  

    runtime/  
      __init__.py  
      agent_loop.py  
      message.py  
      tool_call.py  
      observation.py  
      llm_client.py  
      config.py  
      errors.py

  

    agents/  
      __init__.py  
      base_agent.py  
      coding_agent.py  
      planner_agent.py  
      reviewer_agent.py  
      tester_agent.py  
      supervisor_agent.py  
      handoff.py  
      agent_registry.py

  

    workflows/  
      __init__.py  
      coding_workflow.py  
      multi_agent_workflow.py  
      workflow_state.py

  

    tools/  
      __init__.py  
      base.py  
      registry.py  
      list_files.py  
      read_file.py  
      write_file.py  
      grep.py  
      apply_patch.py  
      run_command.py  
      git_status.py  
      git_diff.py  
      ask_human.py

  

    context/  
      __init__.py  
      context_builder.py  
      repo_map.py  
      memory.py  
      rag.py  
      token_budget.py

  

    safety/  
      __init__.py  
      sandbox.py  
      permission.py  
      command_policy.py  
      guardrails.py  
      approval.py

  

    observability/  
      __init__.py  
      trace.py  
      metrics.py  
      event.py

  

    eval/  
      __init__.py  
      eval_runner.py  
      eval_case.py  
      scoring.py  
      report.py

  

    production/  
      __init__.py  
      readiness.py  
      risk_registry.py

  

  examples/  
    demo_repo/  
      src/  
        __init__.py  
        calculator.py  
      tests/  
        __init__.py  
        test_calculator.py

  

  eval_cases/  
    case_001_single_agent_fix_test/  
      task.md  
      verify.py  
    case_002_multi_agent_review_then_fix/  
      task.md  
      verify.py  
    case_003_dangerous_command_blocked/  
      task.md  
      verify.py  
    case_004_context_retrieval/  
      task.md  
      verify.py  
    case_005_human_approval_required/  
      task.md  
      verify.py

  

  tests/  
    test_agent_loop.py  
    test_multi_agent.py  
    test_handoff.py  
    test_tools.py  
    test_sandbox.py  
    test_permission.py  
    test_guardrails.py  
    test_context.py  
    test_trace.py  
    test_eval_runner.py

  

  docs/  
    00-project-positioning.md  
    01-agent-loop.md  
    02-workflow-vs-agent.md  
    03-tool-calling.md  
    04-multi-agent-supervisor.md  
    05-subagent-and-handoff.md  
    06-context-engineering.md  
    07-memory-and-rag.md  
    08-permission-and-sandbox.md  
    09-guardrails-and-human-approval.md  
    10-observability-and-tracing.md  
    11-evaluation.md  
    12-production-readiness.md  
    13-framework-comparison.md  
    14-interview-qa.md  
    15-project-deep-dive-playbook.md  
    16-four-layer-followups.md  
    17-architecture-whiteboard.md

  

  tutorials/  
    README.md  
    00-how-to-learn-this-project.md  
    01-why-mock-llm.md  
    02-how-cli-works-argparse.md  
    03-how-agent-loop-works.md  
    04-how-tool-calling-works.md  
    05-how-observation-works.md  
    06-how-workflow-differs-from-agent.md  
    07-how-supervisor-subagent-works.md  
    08-how-handoff-works.md  
    09-how-context-builder-works.md  
    10-how-memory-and-rag-work.md  
    11-how-permission-sandbox-works.md  
    12-how-guardrails-work.md  
    13-how-tracing-works.md  
    14-how-eval-runner-works.md  
    15-how-to-explain-in-interview.md  
    16-how-to-tell-project-story.md  
```

  

---

  

## 七、核心设计要求

  

### 1. 单 Agent Runtime

  

实现标准 Agent Loop：

  

```text  
user task  
  ↓  
build context  
  ↓  
call llm  
  ↓  
llm returns tool calls  
  ↓  
execute tools  
  ↓  
append observations  
  ↓  
call llm again  
  ↓  
final answer  
```

  

必须支持：

  

- max_steps；  
- tool calls；  
- final answer；  
- unknown tool 错误；  
- 参数错误；  
- 工具执行异常；  
- 重复 tool call 检测；  
- doom loop 检测；  
- trace 记录；  
- final summary。

  

文档中必须解释：

  

- Agent Loop 是什么；  
- 为什么不等于普通 ChatGPT 问答；  
- tool call 和 observation 如何形成闭环；  
- 为什么需要 max_steps；  
- 为什么需要 trace。

  

---

  

### 2. 多 Agent Supervisor / Subagent

  

必须实现一个简化多 Agent 系统。

  

设计如下：

  

```text  
SupervisorAgent  
  ├── PlannerAgent  
  ├── CodingAgent  
  ├── TesterAgent  
  └── ReviewerAgent  
```

  

#### SupervisorAgent

  

职责：

  

- 接收用户任务；  
- 判断任务阶段；  
- 分配给 subagent；  
- 收集 subagent 结果；  
- 决定下一步；  
- 输出最终结果。

  

#### PlannerAgent

  

职责：

  

- 分析任务；  
- 输出计划；  
- 不改文件；  
- 不执行 bash。

  

#### CodingAgent

  

职责：

  

- 读取代码；  
- 搜索文件；  
- 修改代码；  
- 生成 patch。

  

#### TesterAgent

  

职责：

  

- 执行测试；  
- 读取失败日志；  
- 总结错误。

  

#### ReviewerAgent

  

职责：

  

- 审查 diff；  
- 检查是否越权；  
- 检查是否有危险修改；  
- 输出 review 结论。

  

文档中必须解释：

  

- 为什么需要 Supervisor；  
- Subagent 和普通工具有什么区别；  
- 多 Agent 什么时候有价值；  
- 多 Agent 的风险是什么；  
- 如何避免 subagent 互相甩锅。

  

---

  

### 3. Handoff

  

实现简化 handoff：

  

```python  
@dataclass  
class Handoff:  
    from_agent: str  
    to_agent: str  
    reason: str  
    payload: dict  
```

  

要求：

  

- 每次 handoff 写入 trace；  
- subagent 之间不要直接乱调；  
- 统一由 supervisor 编排；  
- 支持 mock multi-agent demo；  
- handoff payload 中要包含任务状态、相关文件、测试结果、review 结论等。

  

文档中必须解释：

  

- Handoff 是什么；  
- 和 tool call 的区别；  
- 状态如何传递；  
- 失败如何处理；  
- 面试时怎么讲。

  

---

  

### 4. Workflow vs Agent

  

实现两种模式。

  

#### CodingWorkflow

  

固定流程：

  

```text  
plan → code → test → review → final  
```

  

#### AgentLoop

  

动态流程：

  

```text  
model decides next tool or next agent  
```

  

代码中要体现：

  

- `workflows/coding_workflow.py`  
- `runtime/agent_loop.py`

  

文档必须解释：

  

- Workflow 是固定路径；  
- Agent 是动态决策；  
- 生产中经常混用；  
- 稳定任务用 workflow；  
- 开放任务用 agent；  
- 为什么面试官会问这个问题。

  

---

  

### 5. Tool System

  

实现工具注册器。

  

内置工具：

  

```text  
list_files  
read_file  
write_file  
grep  
apply_patch  
run_command  
git_status  
git_diff  
ask_human  
```

  

每个工具必须有：

  

- name；  
- description；  
- schema；  
- execute；  
- permission check；  
- observation output；  
- error handling。

  

文档中必须解释：

  

- tool schema 是什么；  
- tool registry 是什么；  
- tool executor 是什么；  
- unknown tool 怎么处理；  
- 参数错误怎么处理；  
- tool result 为什么要变成 observation。

  

---

  

### 6. Context Engineering

  

实现最小但真实的上下文工程。

  

#### repo_map.py

  

生成目录树摘要，忽略：

  

- .git  
- __pycache__  
- node_modules  
- target  
- dist  
- build

  

#### memory.py

  

实现简化 memory：

  

- session memory；  
- key-value memory；  
- 最近 N 条 observation；  
- 可清空。

  

#### rag.py

  

实现简化 RAG：

  

- 不用向量库；  
- 用关键词匹配；  
- 输入 query；  
- 返回相关文档片段；  
- 可用于 docs / repo 文件摘要。

  

#### token_budget.py

  

不用真实 tokenizer，使用字符预算：

  

- max_chars；  
- 超出截断；  
- 标注 `[truncated]`。

  

#### context_builder.py

  

组合：

  

- system prompt；  
- user task；  
- repo map；  
- retrieved docs；  
- memory；  
- available tools；  
- permission summary。

  

文档中必须解释：

  

- 为什么不能把整个 repo 塞进 prompt；  
- repo map 的作用；  
- memory 的作用；  
- 简化 RAG 的作用；  
- token budget 为什么重要；  
- 生产中如何升级为向量检索 / LSP / symbol search。

  

---

  

### 7. Permission / Sandbox

  

实现生产感较强的安全层。

  

#### sandbox.py

  

WorkspaceSandbox：

  

- 所有路径限制在 workspace_root；  
- 禁止访问 workspace 外；  
- 禁止敏感文件：  
  - .env  
  - id_rsa  
  - .pem  
  - .key  
  - credentials  
  - secrets  
- 返回明确拒绝原因。

  

#### command_policy.py

  

实现 allowlist / denylist。

  

默认允许：

  

```text  
python -m unittest  
python -m unittest discover  
git status  
git diff  
```

  

默认拒绝：

  

```text  
rm  
rm -rf  
del  
rmdir  
git push  
git reset --hard  
curl  
wget  
ssh  
scp  
chmod  
chown  
powershell Remove-Item  
format  
mkfs  
```

  

#### permission.py

  

实现：

  

```python  
class PermissionDecision(Enum):  
    ALLOW = "allow"  
    ASK = "ask"  
    DENY = "deny"  
```

  

策略：

  

- read/list/grep allow；  
- write/apply_patch ask；  
- run_command 根据 command policy；  
- network deny；  
- external_directory deny；  
- delete deny。

  

Demo 中允许：

  

```python  
auto_approve_writes=True  
```

  

文档中说明：

  

- demo 自动审批；  
- 生产必须接 human approval；  
- 为什么不能全 allow；  
- 为什么 bash 是最高风险工具之一。

  

---

  

### 8. Guardrails

  

实现输入、工具、输出三类 guardrails。

  

#### Input Guardrails

  

检查：

  

- 用户是否要求删除文件；  
- 是否要求读取密钥；  
- 是否要求访问外网；  
- 是否要求越权路径。

  

#### Tool Guardrails

  

检查：

  

- 工具是否存在；  
- 参数是否合法；  
- 路径是否越权；  
- 命令是否危险；  
- 是否触发重复调用。

  

#### Output Guardrails

  

检查 final answer：

  

- 是否声明测试已通过但实际未运行；  
- 是否隐瞒安全拦截；  
- 是否未说明未验证点。

  

实现：

  

```python  
@dataclass  
class GuardrailResult:  
    passed: bool  
    reason: str  
    severity: str  
```

  

文档中必须解释：

  

- Guardrails 和 permission 的区别；  
- input / tool / output guardrails 分别拦什么；  
- 为什么输出也要检查；  
- 生产中如何接审批系统。

  

---

  

### 9. Human-in-the-loop

  

实现 `ask_human` 工具和 approval 模块。

  

要求：

  

- 默认不真的阻塞等待输入；  
- demo 中返回预设 approval；  
- trace 中记录 approval；  
- `--no-auto-approve` 时可以返回 rejected 或提示需要人工确认；  
- README 说明生产中应接审批系统。

  

文档中必须解释：

  

- 为什么 Agent 不能完全自动；  
- 哪些动作必须人工审批；  
- ask_human 和 permission 的关系；  
- 面试时如何回答“如何做人机协同”。

  

---

  

### 10. Observability / Tracing

  

实现 trace 系统。

  

每个 run 生成：

  

```text  
agent_forge_trace.json  
```

  

Trace event 包含：

  

- run_id；  
- step；  
- agent_name；  
- event_type；  
- llm_request_summary；  
- llm_response_summary；  
- tool_call；  
- tool_arguments；  
- observation；  
- permission_decision；  
- handoff；  
- duration_ms；  
- success；  
- error。

  

event_type 包括：

  

```text  
llm_call  
tool_call  
tool_observation  
handoff  
permission_check  
guardrail_check  
human_approval  
final_answer  
error  
```

  

同时生成控制台可读 trace。

  

文档中必须解释：

  

- 为什么 Agent 生产化必须有 tracing；  
- trace 如何帮助排查 hallucination / tool failure / permission issue；  
- 和普通日志有什么区别；  
- 面试时怎么讲。

  

---

  

### 11. Eval Benchmark

  

实现简化 eval。

  

Eval case 必须能跑。

  

#### case_001_single_agent_fix_test

  

测试单 Agent 修复 demo repo 中的测试失败。

  

#### case_002_multi_agent_review_then_fix

  

测试 supervisor 编排 planner → coder → tester → reviewer。

  

#### case_003_dangerous_command_blocked

  

验证危险命令被拒绝。

  

#### case_004_context_retrieval

  

验证 RAG / repo map 能返回相关上下文。

  

#### case_005_human_approval_required

  

验证写文件需要 approval，demo 中自动批准并记录 trace。

  

指标：

  

```python  
@dataclass  
class EvalResult:  
    case_id: str  
    passed: bool  
    task_success: bool  
    test_pass: bool  
    safety_violation: bool  
    handoff_count: int  
    tool_call_count: int  
    steps_count: int  
    notes: str  
```

  

输出：

  

```text  
eval_report.md  
```

  

文档中必须解释：

  

- demo 不等于 eval；  
- 为什么要 benchmark；  
- 每个指标看什么；  
- 如何比较不同模型；  
- 如何分析失败原因。

  

---

  

## 八、Demo 要求

  

必须有两个 demo。

  

### Demo 1：单 Agent Coding Demo

  

运行：

  

```bash  
python run_demo.py --mode single  
```

  

效果：

  

1. 读取 `examples/demo_repo/src/calculator.py`；  
2. 读取测试文件；  
3. 修复 bug；  
4. 运行 unittest；  
5. 输出 final answer；  
6. 生成 trace。

  

Demo bug：

  

```python  
def add(a: int, b: int) -> int:  
    return a - b  
```

  

测试期望：

  

```python  
self.assertEqual(add(2, 3), 5)  
```

  

修复为：

  

```python  
return a + b  
```

  

### Demo 2：多 Agent Demo

  

运行：

  

```bash  
python run_demo.py --mode multi  
```

  

流程：

  

```text  
SupervisorAgent  
  ↓ handoff to PlannerAgent  
  ↓ handoff to CodingAgent  
  ↓ handoff to TesterAgent  
  ↓ handoff to ReviewerAgent  
  ↓ final  
```

  

要求：

  

- 每次 handoff 都写 trace；  
- 控制台能看到每个 agent 做了什么；  
- 最终测试通过；  
- reviewer 输出 review 结论。

  

---

  

## 九、CLI 要求

  

支持：

  

```bash  
python -m agent_forge "修复 examples/demo_repo 里的测试失败问题"  
```

  

参数：

  

```text  
--workspace  
--mode single|multi|workflow  
--llm mock|openai-compatible  
--max-steps  
--trace-file  
--no-auto-approve  
```

  

默认：

  

```text  
workspace = 当前目录  
mode = single  
llm = mock  
max_steps = 12  
trace_file = agent_forge_trace.json  
auto_approve_writes = True  
```

  

文档中必须解释：

  

- argparse 是什么；  
- CLI 参数为什么这么设计；  
- workspace 为什么重要；  
- mode 为什么有 single/multi/workflow；  
- mock 和 openai-compatible 的区别。

  

---

  

## 十、LLM Client

  

实现：

  

### MockLLMClient

  

必须完整可运行。

  

支持：

  

- single agent demo；  
- multi agent demo；  
- deterministic tool calls；  
- final answer。

  

文档中必须解释：

  

- MockLLM 是什么；  
- 为什么第一版要用 MockLLM；  
- MockLLM 不代表真实智能；  
- 它的价值是验证 Harness 链路；  
- 接真实模型后会遇到哪些问题。

  

### OpenAICompatibleLLMClient

  

可选实现。

  

要求：

  

- 环境变量：  
  - AGENT_FORGE_BASE_URL  
  - AGENT_FORGE_API_KEY  
  - AGENT_FORGE_MODEL  
- 不配置时不影响 demo；  
- 可以用 urllib 实现；  
- 如果无法完整支持 tool call，也要保留接口和 TODO；  
- README 说明后续如何接入真实模型。

  

---

  

## 十一、教学文档要求

  

除了 docs，还必须生成 tutorials 目录。

  

tutorials 的风格要参考 nanoAgent：

  

- 每篇 10-15 分钟能看完；  
- 每篇只讲一个模块；  
- 不写大而全理论；  
- 直接结合本项目代码；  
- 讲清“为什么这么写”；  
- 帮我从 0 学会这个项目；  
- 每篇结尾必须有“面试怎么说”。

  

每篇固定结构：

  

```md  
# 标题

  

## 1. 这篇解决什么问题

  

## 2. 先给结论

  

## 3. 最小概念

  

## 4. 对应代码在哪里

  

## 5. 运行一下看效果

  

## 6. 常见坑

  

## 7. 面试怎么说

  

## 8. 下一步学什么  
```

  

必须完整生成以下文件：

  

```text  
tutorials/README.md  
tutorials/00-how-to-learn-this-project.md  
tutorials/01-why-mock-llm.md  
tutorials/02-how-cli-works-argparse.md  
tutorials/03-how-agent-loop-works.md  
tutorials/04-how-tool-calling-works.md  
tutorials/05-how-observation-works.md  
tutorials/06-how-workflow-differs-from-agent.md  
tutorials/07-how-supervisor-subagent-works.md  
tutorials/08-how-handoff-works.md  
tutorials/09-how-context-builder-works.md  
tutorials/10-how-memory-and-rag-work.md  
tutorials/11-how-permission-sandbox-works.md  
tutorials/12-how-guardrails-work.md  
tutorials/13-how-tracing-works.md  
tutorials/14-how-eval-runner-works.md  
tutorials/15-how-to-explain-in-interview.md  
tutorials/16-how-to-tell-project-story.md  
```

  

每篇不需要特别长，但不能只有标题。   
每篇都要能独立帮助我理解一个模块。

  

### tutorials/16-how-to-tell-project-story.md

  

必须专门讲“如何把这个项目讲成强信号项目”，包含：

  

- 3 分钟开场模板；  
- 中文版本；  
- 英文版本；  
- 不同面试官视角：技术面、主管面、HR/综合面；  
- 如何避免流水账；  
- 如何用项目运行指标代替虚假业务数字；  
- 如何把 feature list 改写成 problem-driven story；  
- 如何主动提出“我可以画一下架构图”；  
- 如何在 3 分钟后停住，留给面试官追问空间。

  

---

  

## 十二、docs 要求

  

docs 更偏设计文档和面试资料。

  

### docs/00-project-positioning.md

  

说明：

  

- 这不是 Claude 模型；  
- 这是 Agent Harness；  
- 不是 OpenCode 配置包；  
- 是对 Agent 工程核心问题的最小复现；  
- 为什么适合面试讲。

  

### docs/01-agent-loop.md

  

讲：

  

- messages；  
- tool call；  
- observation；  
- max_steps；  
- doom loop；  
- final answer。

  

### docs/02-workflow-vs-agent.md

  

讲：

  

- workflow 固定路径；  
- agent 动态决策；  
- 生产中混用；  
- coding_workflow 和 agent_loop 的代码对应关系。

  

### docs/03-tool-calling.md

  

讲：

  

- tool schema；  
- tool registry；  
- tool executor；  
- 参数校验；  
- unknown tool；  
- invalid arguments；  
- tool observation。

  

### docs/04-multi-agent-supervisor.md

  

讲：

  

- supervisor 为什么存在；  
- subagent 为什么存在；  
- planner/coder/tester/reviewer 分工；  
- 多 agent 的风险；  
- 什么时候不该用多 agent。

  

### docs/05-subagent-and-handoff.md

  

讲：

  

- handoff 是什么；  
- 和普通 tool call 的区别；  
- handoff payload；  
- state passing；  
- 失败处理。

  

### docs/06-context-engineering.md

  

讲：

  

- repo map；  
- retrieval；  
- memory；  
- token budget；  
- 为什么不能把整个 repo 塞进 prompt。

  

### docs/07-memory-and-rag.md

  

讲：

  

- short-term memory；  
- session memory；  
- retrieval；  
- 关键词 RAG；  
- 和向量库 RAG 的区别；  
- 生产中如何升级。

  

### docs/08-permission-and-sandbox.md

  

讲：

  

- allow/ask/deny；  
- workspace boundary；  
- command policy；  
- dangerous commands；  
- demo approval vs production approval。

  

### docs/09-guardrails-and-human-approval.md

  

讲：

  

- input guardrails；  
- tool guardrails；  
- output guardrails；  
- human approval；  
- 哪些动作必须审批。

  

### docs/10-observability-and-tracing.md

  

讲：

  

- 为什么 Agent 必须 trace；  
- trace event；  
- tool trace；  
- handoff trace；  
- guardrail trace；  
- 生产排障。

  

### docs/11-evaluation.md

  

讲：

  

- demo 不等于 eval；  
- case；  
- metrics；  
- safety violation；  
- tool call success；  
- handoff count；  
- test pass。

  

### docs/12-production-readiness.md

  

讲：

  

- deployment；  
- model gateway；  
- rate limit；  
- audit；  
- rollback；  
- CI/CD；  
- cost；  
- latency；  
- incident response。

  

### docs/13-framework-comparison.md

  

对比：

  

- Agent Forge；  
- OpenCode；  
- Claude Code；  
- LangGraph；  
- OpenAI Agents SDK。

  

不要吹自己，客观写：

  

- 本项目实现了哪些核心机制；  
- 成熟框架强在哪里；  
- 当前项目缺什么；  
- 后续怎么增强。

  

### docs/14-interview-qa.md

  

至少写 50 个 Q&A。

  

必须覆盖：

  

1. 你这个项目是什么？  
2. 你是不是做了一个 Claude？  
3. 和普通 ChatGPT API 调用有什么区别？  
4. Agent Loop 怎么实现？  
5. Tool Calling 怎么实现？  
6. Observation 为什么重要？  
7. Agent 和 Workflow 有什么区别？  
8. 为什么需要多 Agent？  
9. Supervisor 怎么设计？  
10. Subagent 怎么设计？  
11. Handoff 怎么传状态？  
12. 多 Agent 会不会更乱？  
13. 怎么避免 subagent 互相甩锅？  
14. 怎么判断任务完成？  
15. 怎么处理模型幻觉？  
16. 怎么处理 unknown tool？  
17. 怎么处理 invalid arguments？  
18. 怎么处理工具执行失败？  
19. 怎么处理死循环？  
20. 怎么做上下文工程？  
21. 大代码仓怎么处理？  
22. 为什么不能全 repo 塞 prompt？  
23. repo map 有什么用？  
24. Memory 有什么用？  
25. RAG 怎么做？  
26. 权限怎么设计？  
27. Sandbox 怎么设计？  
28. 如何防止 rm -rf？  
29. 如何防止读取密钥？  
30. 如何防止访问外网？  
31. Guardrails 怎么设计？  
32. Human approval 放在哪些点？  
33. Trace 记录什么？  
34. 生产中怎么排查 Agent 错误？  
35. Eval 怎么设计？  
36. 指标有哪些？  
37. 怎么比较不同模型？  
38. 怎么控制 token 成本？  
39. 怎么处理延迟？  
40. 怎么接内网模型？  
41. 怎么做模型路由？  
42. 怎么做 CI/CD？  
43. Agent 能不能直接 push 代码？  
44. 怎么回滚？  
45. 怎么做审计？  
46. 怎么灰度上线？  
47. 和 OpenCode 有什么区别？  
48. 和 Claude Code 有什么区别？  
49. 和 LangGraph 有什么区别？  
50. 项目当前不足和下一步是什么？

  

### docs/15-project-deep-dive-playbook.md

  

生成一份项目深挖讲解手册。

  

必须包含：

  

1. 30 秒版本；  
2. 1 分钟版本；  
3. 3 分钟版本；  
4. 中文版本；  
5. 英文口语化版本；  
6. 面试官可能中断时如何收束；  
7. 如何从 feature-listing 改成 problem-driven；  
8. 如何用项目真实指标支撑结果。

  

3 分钟版本结构必须是：

  

```text  
第 1 分钟：context + role  
第 2 分钟：hardest problem + approach + trade-off  
第 3 分钟：result + evidence + learning  
```

  

注意：

  

- 不允许编造业务规模；  
- 可以使用本项目 eval / trace / safety 的真实运行结果；  
- 不确定的数字要用占位符，并标注“运行后填入”。

  

### docs/16-four-layer-followups.md

  

按面试追问 4 层结构，为每个核心模块准备答案：

  

```text  
Layer 1: What did you do?  
Layer 2: Why this approach?  
Layer 3: What went wrong?  
Layer 4: What else did you consider?  
```

  

覆盖模块至少包括：

  

- Agent Loop；  
- Tool Calling；  
- Observation；  
- Workflow vs Agent；  
- Supervisor / Subagent；  
- Handoff；  
- Context Engineering；  
- Memory / RAG；  
- Permission / Sandbox；  
- Guardrails；  
- Human Approval；  
- Tracing；  
- Eval；  
- Production Readiness。

  

每个模块都要写：

  

- 中文答案；  
- 英文关键词；  
- 面试官追问意图；  
- 项目中的代码证据；  
- 当前不足；  
- 后续增强。

  

### docs/17-architecture-whiteboard.md

  

生成一份可用于面试白板讲解的架构图文档。

  

必须包含：

  

1. ASCII 总体架构图；  
2. 单 Agent Loop 图；  
3. Supervisor / Subagent 图；  
4. Tool Calling 数据流图；  
5. Permission / Sandbox 拦截点图；  
6. Eval / Trace 闭环图。

  

总体架构图示例风格：

  

```text  
User Task / CLI  
      ↓  
Context Builder ── Repo Map / Memory / RAG  
      ↓  
Agent Runtime ── LLM Client  
      ↓  
Tool Registry ── read / grep / patch / bash  
      ↓  
Permission + Sandbox + Guardrails  
      ↓  
Observation + Trace  
      ↓  
Eval / Report  
```

  

文档还要给出一句面试话术：

  

```text  
Let me draw the architecture to make sure we are aligned.  
```

  

并说明画图后如何指出自己的设计范围和两个关键决策。  
---

  

## 十三、README 要求

  

README 必须包含：

  

```md  
# Agent Forge

  

## 1. 项目定位

  

Agent Forge 是一个面向 Agent 工程面试和生产化理解的 Agent Harness 项目。

  

它不是模型，不训练模型，也不是复制某个商业产品。

  

它通过一个可运行的 Coding Agent Demo，覆盖 Agent 开发中的核心问题：

  

- Agent Loop  
- Tool Calling  
- Observation  
- Workflow vs Agent  
- Multi-Agent Supervisor/Subagent  
- Handoff  
- Context Engineering  
- Memory / RAG  
- Permission / Sandbox  
- Guardrails  
- Human-in-the-loop  
- Tracing  
- Eval  
- Production Readiness

  

## 2. 为什么这样选技术

  

解释：

  

- 为什么用 Python；  
- 为什么用 argparse；  
- 为什么用 unittest；  
- 为什么用 MockLLM；  
- 为什么用 JSON trace；  
- 为什么第一版用关键词 RAG；  
- 为什么不用复杂框架。

  

## 3. 快速开始

  

python run_demo.py --mode single

  

python run_demo.py --mode multi

  

python -m unittest discover tests

  

python -m agent_forge.eval.eval_runner

  

## 4. 学习路线

  

先看：

  

1. tutorials/00-how-to-learn-this-project.md  
2. tutorials/01-why-mock-llm.md  
3. tutorials/03-how-agent-loop-works.md  
4. tutorials/04-how-tool-calling-works.md  
5. tutorials/07-how-supervisor-subagent-works.md  
6. docs/14-interview-qa.md  
7. docs/15-project-deep-dive-playbook.md  
8. docs/16-four-layer-followups.md  
9. docs/17-architecture-whiteboard.md

  

## 5. 核心能力

  

列出核心能力。

  

## 6. 项目结构

  

解释每个目录。

  

## 7. 面试表达

  

给一段 1 分钟项目介绍。

  

必须是 problem-driven，不要流水账列功能。

  

同时给出：

  

- 30 秒版本；  
- 1 分钟版本；  
- 3 分钟版本；  
- 中文版本；  
- 英文关键词版；  
- 架构图讲解入口。

  

## 8. 后续增强

  

列出：

  

- 接真实 OpenAI-compatible 模型；  
- 增加 LSP；  
- 增加 MCP-style tool adapter；  
- 增加真实向量检索；  
- 增加 CI runner；  
- 增加 Web approval；  
- 增加更多 eval case。  
```

  

---

  

## 十四、测试要求

  

必须保证：

  

```bash  
python run_demo.py --mode single  
python run_demo.py --mode multi  
python -m unittest discover tests  
python -m agent_forge.eval.eval_runner  
```

  

都能运行。

  

如果失败，请自动修复。

  

---

  

## 十五、质量要求

  

生成后必须检查：

  

1. 单 Agent demo 能修复测试；  
2. 多 Agent demo 能跑出 supervisor → planner → coder → tester → reviewer；  
3. trace JSON 能生成；  
4. eval_report.md 能生成；  
5. unittest 能通过；  
6. 危险命令被拒绝；  
7. workspace 越权被拒绝；  
8. 敏感文件读取被拒绝；  
9. apply_patch 能正常工作；  
10. human approval 有 trace；  
11. docs/interview-qa.md 至少 50 个 Q&A；  
12. tutorials 至少 17 篇，每篇有实质内容；  
13. README 能说明项目定位和学习路线；  
14. 项目没有空文件；  
15. 不要把项目写成 Claude 复制品；  
16. 不要把项目写成 OpenCode 配置包；  
17. 不要引入导致新手难以运行的复杂依赖；  
18. 每个关键技术选择都要有解释；  
19. 项目既能跑，也能学；  
20. 必须生成 project deep-dive、4 层追问、架构白板图三类面试材料；  
21. 面试材料必须 problem-driven，不能只是 feature list；  
22. 所有量化结果必须来自项目运行或明确标注为待填充。

  

---

  

## 十六、最终输出

  

完成后输出：

  

```text  
已生成 Agent Forge 第一版。

  

已实现：  
- 单 Agent Coding Demo  
- 多 Agent Supervisor/Subagent Demo  
- Tool Calling  
- Observation 回传  
- Workflow vs Agent  
- Context Engineering  
- Memory / 简化 RAG  
- Permission / Sandbox  
- Guardrails  
- Human Approval Mock  
- Trace / Observability  
- Eval Benchmark  
- Production Readiness Docs  
- Interview Q&A  
- nanoAgent 风格 tutorials 教学文档  
- Project Deep-Dive 面试讲解材料  
- 4 层追问准备文档  
- 架构白板图文档

  

运行方式：  
python run_demo.py --mode single  
python run_demo.py --mode multi  
python -m unittest discover tests  
python -m agent_forge.eval.eval_runner

  

建议学习路线：  
1. tutorials/00-how-to-learn-this-project.md  
2. tutorials/01-why-mock-llm.md  
3. tutorials/03-how-agent-loop-works.md  
4. tutorials/04-how-tool-calling-works.md  
5. tutorials/07-how-supervisor-subagent-works.md  
6. docs/14-interview-qa.md

  

下一步建议：  
1. 接入真实 OpenAI-compatible 模型；  
2. 增加 LSP / Symbol Search；  
3. 增加 MCP-style tool adapter；  
4. 增加真实向量 RAG；  
5. 增加 CI Runner 模式；  
6. 扩展 Eval Case 到 20 个；  
7. 对照 OpenCode / Claude Code / LangGraph 继续补设计文档。  
```
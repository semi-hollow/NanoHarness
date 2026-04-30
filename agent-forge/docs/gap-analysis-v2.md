# Gap Analysis V2

## Current Repository Summary

- 技术栈：Python 3.11，标准库优先，`argparse` CLI，`unittest` 测试，无强制第三方依赖。
- 入口文件：`run_demo.py` -> `agent_forge.cli:main`，模块入口为 `agent_forge/__main__.py`。
- 核心目录：
  - `agent_forge/runtime/`：agent loop、LLM client、message、tool call、observation。
  - `agent_forge/tools/`：read/write/list/grep/patch/run/git/ask_human 与 registry。
  - `agent_forge/safety/`：sandbox、permission、command policy、guardrails。
  - `agent_forge/context/`：repo map、RAG、memory、budget、symbol search、file ranker、context builder。
  - `agent_forge/observability/`：trace 与 metrics。
  - `agent_forge/eval/` 与 `eval_cases/`：可执行 eval runner 和 case。
  - `docs/`、`tutorials/`：学习、架构和面试材料。
- 当前测试：`python3.11 -m unittest discover tests` 可运行；`python3.11 -m agent_forge.eval.eval_runner` 生成 `eval_report.md`。
- README 一致性：README 已按 Python 命令描述当前能力，没有使用 npm/pnpm 这类不匹配技术栈的命令。

## Already Implemented

- Agent loop：`AgentLoop` 支持 LLM -> tool call -> permission -> tool execution -> observation -> final answer。
- Tool registry：`ToolRegistry` 支持 schema 暴露、工具注册、未知工具 recovery。
- Safety：workspace sandbox、敏感文件拒绝、命令 allowlist/denylist、write approval、input/output guardrail。
- LLM：`MockLLMClient` 和可选 `OpenAICompatibleLLMClient`。
- Context：repo map、keyword retrieval、memory、symbol search、file ranker、context budget report。
- Observability：trace JSON 与 metrics summary。
- Eval：16 个 case，均包含 `task.md` 和 `verify.py`，runner 真实执行 verify。
- Docs：README、production readiness、LSP/symbol search、MCP-style adapter、80 条 interview Q&A、resume/project script。

## Placeholder / Thin Areas Found

- Agent loop 能跑，但原先没有显式 `AgentState`、planner step、stop condition 模块，面试深挖时结构感不足。
- Trace 原先记录事件，但缺少人类可读 `summary.md` 和明确 stop reason。
- Tool schema 原先偏轻，registry 没有统一参数校验。
- 工具名已有 `grep`，但原始方案使用 `grep_search`，需要兼容 alias。
- OpenAI-compatible client 使用 `AGENT_FORGE_*` 环境变量，原始方案写的是 `OPENAI_*`，需要兼容别名。
- 文档已有深度材料，但缺少原始方案点名要求的 `architecture.md`、`interview-story.md`、`demo-script.md`、`limitations.md` 文件名。

## README Claims vs Code

- README 中 V1/V2 能力矩阵与代码基本一致。
- “MCP-style adapter” 是本地 adapter，不是完整 MCP，文档已明确边界。
- “LSP” 当前没有实现完整 LSP server 接入，只实现 AST symbol search，文档已说明 roadmap。
- “Eval benchmark” 已真实执行 verify，不是硬编码全通过。

## Remaining Gaps vs Original Technical Plan

### P0

- 显式 AgentState / Planner / StopCondition。
  - 修改：`agent_forge/runtime/state.py`、`planner.py`、`stop_condition.py`、`agent_loop.py`。
  - 验证：`tests/test_agent_loop.py` 检查 context/plan/action/final trace。
- Registry 参数校验。
  - 修改：`agent_forge/tools/registry.py`。
  - 验证：新增/更新 tool tests。
- `grep_search` alias。
  - 修改：`agent_forge/tools/grep.py`、`agent_forge/cli.py`。
  - 验证：tool tests 和 eval。

### P1

- Trace summary.md 与 stop reason。
  - 修改：`observability/trace.py`，新增 `summary.py`。
  - 验证：trace tests。
- OpenAI 环境变量别名。
  - 修改：`runtime/llm_client.py`。
  - 验证：LLM tests。
- 原始方案指定文档文件名。
  - 修改：新增 `docs/architecture.md`、`docs/interview-story.md`、`docs/demo-script.md`、`docs/limitations.md`。
  - 验证：人工阅读与 py_compile 不受影响。

### P2

- 真 LSP provider。
- 完整 MCP protocol transport。
- 长期 eval history 和趋势图。
- 更完整 JSON Schema 校验。
- 生产级 model gateway。

## Verification Plan

```bash
python3.11 run_demo.py --mode single
python3.11 run_demo.py --mode multi
python3.11 run_demo.py --mode workflow
python3.11 -m unittest discover tests
python3.11 -m agent_forge.eval.eval_runner
python3.11 - <<'PY'
from pathlib import Path
import py_compile
for p in Path(".").rglob("*.py"):
    py_compile.compile(str(p), doraise=True)
print("py_compile passed")
PY
```

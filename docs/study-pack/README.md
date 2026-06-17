# Runtime 学习包

这个目录只放 runtime 学习材料，不重复源码地图，也不放项目讲法材料。

三个文档区这样区分：

| 区域 | 用途 | 从哪里开始 |
|---|---|---|
| `agent_forge/README.md` | 源码地图：package、核心类、调用链。 | 在 IDE 里看代码前。 |
| `docs/study-pack/` | runtime 概念：loop、context、tools、control、MCP。 | 系统学习项目设计时。 |
| `docs/technical-defense/` | 项目讲法和技术追问。 | 准备对外解释项目时。 |

阅读顺序：

1. `01-agent-loop-context-memory.md`
2. `02-tools-control-safety.md`
3. `03-orchestration-review-eval.md`
4. `04-runtime-control-and-extension-map.md`
5. `05-mcp-and-external-tools.md`

边读边跑：

```bash
cd /path/to/NanoHarness
source .venv/bin/activate
local_scripts/run_webhook_deepseek.sh
scripts/verify_mcp.sh
python run_demo.py --mode review
python run_demo.py --list-task-states
```

生成证据按这个顺序看：

1. `.agent_forge/latest/webhook-deepseek/usage_report.md`
2. `.agent_forge/latest/webhook-deepseek/trace.json`
3. `.agent_forge/eval_report.md`

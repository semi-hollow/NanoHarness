# Facade 目录：项目能做什么、从哪里进入

本文只回答“外部可以发起哪些动作”。它不是完整参数手册，也不把内部 helper、测试入口或
兼容别名包装成产品能力。参数以 `forge <command> --help` 为准；架构与 Evidence 语义仍以
`docs/ARCHITECTURE.md` 为准。

## 首轮只记四个动作

| 动作 | 入口 | 规范 owner | 结果与边界 |
| --- | --- | --- | --- |
| 执行真实任务 | `forge run` | `cli.repository -> Harness.run` | 产生 run、checkpoint、trace、patch/manifest；流程完成不等于 issue resolved |
| 读取事实 | `forge inspect` | `cli.inspection -> observability/code_compass` | 只读 run、artifact 或 source symbol；不改变状态 |
| 展示控制面 | `forge demo` | `showcase.run_governed_demo -> Harness.run` | 无在线模型的确定性 approval/HITL story；不证明模型质量或测试通过 |
| 继续中断任务 | `forge resume` | `cli.resume -> repository -> Harness.run` | 先记录 `--answer` 或 `--decision`，再从 durable checkpoint 创建新 continuation |

最短使用方式：

```bash
forge run "fix the failing test" --workspace /path/to/repo --provider deepseek
forge inspect latest
forge demo
forge resume <run-dir> --answer "Python 3.11"
forge resume <run-dir> --decision approved
```

`resume` 不是恢复 Python stack、HTTP connection 或模型隐藏状态；它从持久化 checkpoint 和
人工输入构造一条新的、可审计的 run。

## Advanced 动作：知道入口，不进入首轮主线

| 目标 | 入口 | 是否执行 Agent | 学习要求 |
| --- | --- | --- | --- |
| 查看固定 case 集 | `forge bench cases` | 否 | 知道 Smoke-5 的选择目的和结论边界 |
| 查看一个 SWE-bench case | `forge bench case <instance-id>` | 否 | 看 issue、base commit、测试契约；默认不看 gold/test patch |
| 执行 SWE-bench-shaped run | `forge bench swebench ...` | 是 | 掌握 candidate/local/official 三层证据，不背数据读写与清理细节 |
| 执行重复 preset 比较 | `forge bench campaign ...` | 是 | 只在实验设计追问时展开，不把 multi-factor 说成单因素因果 |
| 查看本地证据 | `forge ui` | 否 | Workbench 是只读 Evidence 视图；执行、审批和恢复仍走 CLI/Public API |

一个真实 case 的最小入口：

```bash
forge bench case astropy__astropy-12907
forge bench swebench --instance-id astropy__astropy-12907 \
  --provider deepseek --model deepseek-chat --max-steps 8
forge inspect <benchmark-run-or-case-dir>
```

只有环境已安装并能运行 official SWE-bench Harness 时才追加 `--evaluate`。API key 只解决模型
调用，不等于 official evaluator、容器、依赖和数据集环境已经可用。

## 嵌入式 Public API

外部 Python 调用方只依赖顶层 `agent_forge`：

```python
from agent_forge import Harness, HarnessConfig, RunRequest, RunResult

result: RunResult = Harness(
    model=my_model,
    config=HarnessConfig(workspace="/path/to/repo"),
).run(RunRequest(task="fix the failing test"))
```

| Surface | 用途 | 稳定性承诺 |
| --- | --- | --- |
| `agent_forge.Harness.run/resume` | Single-Agent run 与显式 continuation | `run` 是唯一 Single-Run API；`resume` 只处理 continuation |
| `agent_forge.HarnessConfig/RunRequest/RunResult` | 类型化配置、输入和返回值 | 顶层兼容面 |
| `agent_forge.HarnessExtensions` | 替换已有 Port，而非创建另一套 Runtime | 顶层兼容面 |
| `agent_forge.extensions` | Model、Tool、State、Event、Environment 等扩展契约 | 高级接入面；首轮只需知道存在 |

`agent_forge.runtime.*`、`application`、`domain`、`adapters` 和 `wiring` 是内部学习/实现路径，
不是外部兼容承诺。`bench.api`、`evaluation.api`、`workbench.api` 是 capability facade，供项目
内部适配器调用；不要从 CLI/页面直接越过它们导入具体 Adapter。

## 隐藏内部能力

`eval`、`memory`、`skills`、`doctor` 和 MCP 支持专项实验、维护或扩展，但不进入公开命令帮助、
首轮阅读和五分钟演示。需要回答对应追问时，再从 capability `api.py` 展开一个 owner。

项目不再保留 `agent-forge` console alias，也不再保留 `report/replay`、`approve/respond`、
`showcase`、`tui` 等平行 CLI。对应用户目标已经分别收敛到 `inspect`、`resume`、`demo` 和 `ui`。

## 如何判断要不要学一个入口

1. 能改变真实 run、durable state 或 Evidence truth：学习 owner 与边界。
2. 只负责参数解析、JSON/Markdown/HTML 呈现：知道输入输出即可。
3. 只服务专项实验或维护：标为 Advanced，追问时再读。
4. 没有生产调用方、独立状态、不变量或 Evidence：不因为“可能有用”保留为 facade。

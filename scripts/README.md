# Scripts 说明

该目录只保留环境准备、Debug Lab 支撑、只读 Workbench 启动与验证脚本。Agent 执行归
`forge` / `Harness` 所有，Workbench 不负责发起运行。

## 学习与演示

```bash
scripts/setup_macos_local.sh --quick
```

首次在 macOS 创建 `.venv`、安装必需开发依赖、执行 `forge doctor`，并自动安装 Debug Lab
断点。完整学习顺序见 [`examples/debug_lab/README.md`](../examples/debug_lab/README.md)。

```bash
.venv/bin/python scripts/install_pycharm_debug_lab.py
```

根据 symbol 定位并合并 20 个 PyCharm 断点。若 PyCharm 已打开，脚本会拒绝写入；关闭后重跑
一次即可，不需要手工点断点。

```bash
scripts/interview_demo.sh [--live|--show-live|--show-astropy]
```

面试一键入口。默认复用确定性 Control Plane Lab 并打开同一份 Evidence 的只读 Workbench；
`--live` 才调用真实 DeepSeek；两个 `--show-*` 只重新发布已保存的 Lab 3/4 Evidence，不发起
模型调用。脚本不复制 Runtime、fixture 或 key 管理逻辑。

```text
scripts/start_workbench.command
```

macOS 双击启动只读 Evidence Workbench。它只回放 `.agent_forge/latest` 与 benchmark/campaign
产物，不是 Agent 执行入口。

## 验证与集成

```bash
scripts/setup_macos_local.sh
scripts/verify.sh
```

不带 `--quick` 时执行完整本地回归。日常学习不要求先跑完整测试。

`setup_wsl_local.sh`、`setup_windows_local.ps1` 与 `verify.ps1` 只保留既有跨平台开发支持；
当前学习和面试运行环境以 macOS 为准，不需要阅读这些实现。

`verify_mcp.sh` / `verify_mcp.ps1` 验证内置 stdio MCP 集成。除非专项准备 MCP 追问，否则不进入
首次学习主线。

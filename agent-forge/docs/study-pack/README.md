# Study Pack 使用说明

这组文档是给你“拿着项目直接读代码、跑命令、准备面试”用的。它不是替代代码，也不是泛泛的项目介绍，而是把每个主要文件放回它在系统里的位置。

建议阅读顺序：

1. `01-code-map-and-architecture.md`：先知道项目分几层、每个目录负责什么。
2. `02-key-file-walkthrough.md`：再读关键代码文件，尤其是 `cli.py` 和 `agent_loop.py`。
3. `03-run-modes-and-trace-reading.md`：一边跑 single/multi/workflow，一边看 trace。
4. `04-interview-narrative.md`：把项目讲成一条清楚的故事线。
5. `05-deep-dive-prep.md`：准备面试官继续追问时的细节。
6. `06-personal-study-checklist.md`：最后用清单确认你真的掌握了。
7. `07-design-context-and-tradeoffs.md`：看清哪些是教学实现、哪些是生产级缺口，避免误读代码。
8. `08-production-interview-upgrade.md`：面向字节/小红书社招，讲清 OpenCode-like 生产化切片。

如果你只想快速复习，按这个 30 分钟路线走：

```text
01 看架构图 5 分钟
02 看 cli.py / agent_loop.py 10 分钟
03 跑 scripts/run_all_modes.sh 并读 trace 10 分钟
04 看 07 的实现边界 3 分钟
05 背 04 的 30 秒 / 1 分钟讲法 2 分钟
```

如果你要准备正式面试，至少完整跑一遍：

```bash
cd /path/to/NanoHarness/agent-forge
source .venv/bin/activate
scripts/run_all_modes.sh
scripts/verify.sh
```

通过标志：

- single 输出 `已完成修复并验证测试通过。`
- multi 输出 `Final: pass`
- workflow 输出 `final_status='success'`
- unittest 输出 `OK`
- eval 输出 `eval_report.md generated`

# 02-how-cli-works-argparse

## 1. 这篇解决什么问题
解释项目如何从命令行启动 Agent。

## 2. 先给结论
CLI 用 Python 标准库 `argparse`，避免新手被复杂依赖卡住。

## 3. 最小概念
CLI 把用户任务、workspace、mode、llm、max_steps、trace_file、approval 配置转成 runtime 参数。

## 4. 对应代码在哪里
`agent_forge/cli.py` 和 `run_demo.py`。

## 5. 运行一下看效果
`python3.11 -m agent_forge "修复 examples/demo_repo 里的测试失败问题" --mode single`。

## 6. 常见坑
workspace 决定工具能访问的根目录；路径不在 workspace 内会被 sandbox 拒绝。

## 7. 面试怎么说
我选择 argparse 是为了让项目标准库优先、易运行，也方便展示参数如何影响 Agent 行为。

## 8. 下一步学什么
读 `03-how-agent-loop-works.md`。

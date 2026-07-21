# 案例研究：astropy__astropy-12907

## 为什么这个 Case 重要

这是一个紧凑的真实仓库案例，适合观察 Coding Agent tool contract、candidate patch
evidence 和保守 evaluation claim。学习目标不是背官方 Harness 实现，而是亲自区分 case 输入、
Agent 产生的修改、仓库自带测试和独立 evaluator。

## 先看 Case，不运行 Agent

```bash
forge bench case astropy__astropy-12907
```

只确认五件事：dataset 的 `instance_id`、issue、`base_commit`、FAIL_TO_PASS/PASS_TO_PASS 测试名，
以及默认没有向 Agent 暴露 gold patch/test patch。`forge bench case` 是只读 Case Explorer。

## Runtime 教训

Agent 需要检查 separability logic 附近的一小段代码。如果 `read_file` 不支持模型自然
使用的 `offset` / `limit` 参数，模型可能反复读取错误位置。这是 tool schema mismatch，
不只是 prompt 问题。

仓库测试命令示例：

```bash
python -m pytest astropy/modeling/tests/test_separable.py
```

`pytest` 是第三方 Python 测试运行器；`python -m pytest` 明确使用当前虚拟环境的 Python，随后
收集并运行指定文件中的测试。Tool 的 `schema()` 只把名称和参数合同展示给模型；真正的进程调用
发生在 Tool Gateway 校验后进入 `execute()`，再由 Execution Environment/subprocess 执行。

SWE-bench 路由必须留下实际测试类型、目标、argv、exit code 和输出证据。只执行 `.py` 文件却没有
pytest collection，或依赖缺失时，local 状态必须是 failed/unavailable，不能标为 verified。

## 一次低预算真实运行

配置 `DEEPSEEK_API_KEY` 后，先不启用 official evaluator：

```bash
forge bench swebench --instance-id astropy__astropy-12907 \
  --provider deepseek --model deepseek-chat --max-steps 8
forge inspect <benchmark-run-or-case-dir>
```

这一步用于辨认 Agent 的 trace、candidate patch 与真实本地测试证据。`--evaluate` 留到已经安装
SWE-bench Harness 的 WSL/Linux/容器环境；API key 本身不等于 official 环境可用。

## 需要收集的证据

- `trace.json`：file inspection step 和 tool argument。
- `patch.diff`：candidate change。
- local validation event：实际 test kind、target/argv、exit code 和输出。
- `usage.json`：tool call、failed tool 和 cost。
- `report.md`：failure class 和 next action。

## 边界

Candidate patch 只表示 Agent 产生了 diff；local verified 只表示记录的指定测试通过；只有 official
Harness 的 per-case parsed outcome 才能进入 official denominator。没有 official artifact 时是
`not_evaluated/Unknown`，不是 0%，更不是 solved。

# 11-evaluation

V2 eval benchmark 扩展到 16 个 case。每个 case 都有 `task.md` 和 `verify.py`，`eval_runner` 会真实执行 verify 脚本，而不是硬编码全通过。

## Report

`python3.11 -m agent_forge.eval.eval_runner` 会生成 `eval_report.md`，包含：

- total
- passed
- failed
- pass rate
- failed case list
- 每个 case 的 handoff/tool/steps/metrics

## Safety Cases

V2 增加了 unknown tool、invalid arguments、false test claim、secret file sandbox、network command policy 等 case。它们证明项目不是只测 happy path，而是覆盖恢复与防护路径。

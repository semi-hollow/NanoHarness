# 11-how-permission-sandbox-works

## 1. 这篇解决什么问题
解释 Agent 为什么不能随便读写和执行命令。

## 2. 先给结论
安全层由 permission、sandbox、command policy 组成：allow/ask/deny、workspace boundary、危险命令拒绝。

## 3. 最小概念
permission 决定动作类型，sandbox 决定路径边界，command policy 决定命令是否允许。

## 4. 对应代码在哪里
`agent_forge/safety/permission.py`、`sandbox.py`、`command_policy.py`。

## 5. 运行一下看效果
`python3.11 -m unittest tests.test_permission tests.test_sandbox`。

## 6. 常见坑
不能用字符串 startswith 判断路径边界；本项目用 `Path.relative_to`。

## 7. 面试怎么说
我把工具执行当成最高风险面，所以安全边界不依赖模型自觉。

## 8. 下一步学什么
读 `12-how-guardrails-work.md`。

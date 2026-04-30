# 09-guardrails-and-human-approval

Guardrails 和 permission 是两层不同控制。

## Guardrails

- Input：拦截删除文件、读取密钥、访问外网、越权路径。
- Tool：拦截 unknown tool、invalid arguments、重复调用。
- Output：拦截没跑测试却声称测试通过、隐瞒 blocked action、缺少未验证点。

`GuardrailResult` 包含 passed、reason、severity、category。

代码：`agent_forge/safety/guardrails.py`。

## Human Approval

写文件和 apply_patch 默认 ASK。demo 中 `auto_approve_writes=True` 自动批准；`--no-auto-approve` 会拒绝。

代码：

- `agent_forge/tools/ask_human.py`
- `agent_forge/tools/apply_patch.py`
- `tests/test_human_approval.py`

## 面试怎么讲

生产中不应该让 Agent 直接执行高风险写入。Human-in-the-loop 是把 autonomy 和 control 平衡起来的工程机制。

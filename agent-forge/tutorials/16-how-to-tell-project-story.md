# 16-how-to-tell-project-story

## 1. 这篇解决什么问题
把“功能清单”改成“问题驱动叙事”，让项目从 meets bar 讲到 strong hire。

## 2. 先给结论
3 分钟里只讲三件事：问题、关键决策、证据。先讲完主线，再停下来给面试官追问。

## 3. 最小概念
- Problem-driven: 先定义工程难点，再讲方案。
- Evidence-driven: 用 eval/trace/test 结果，不编业务数字。
- Ownership-driven: 用“我设计/我验证/我权衡”。

## 4. 对应代码在哪里
- 运行与指标：`agent_forge/runtime/agent_loop.py`、`agent_forge/eval/eval_runner.py`
- 安全链路：`agent_forge/safety/*`
- 多 Agent：`agent_forge/agents/supervisor_agent.py`

## 5. 运行一下看效果
- `python3.11 run_demo.py --mode single`
- `python3.11 run_demo.py --mode multi`
- `python3.11 -m agent_forge.eval.eval_runner`

## 6. 常见坑
- 一上来背功能列表，听起来像 README 朗读。
- 报“提升 80% 效率”但没有证据来源。
- 忽略失败模式与替代方案。

## 7. 面试怎么说
中文 3 分钟模板：
1) 第 1 分钟：我把问题定义为“如何把 LLM 从文本生成器变成受控执行系统”。
2) 第 2 分钟：最难点是安全与可观测，我做了 permission/sandbox/trace，并权衡了灵活性与稳定性。
3) 第 3 分钟：我用 unittest、eval case、handoff trace 来验证，未覆盖项我会明确列出下一步。

English template:
- “I built a compact agent harness to answer one question: how to make tool execution safe, observable, and evaluable.”
- “My hardest trade-off was between autonomy and control, so I implemented allow/ask/deny plus structured traces.”
- “I validated with deterministic demos and eval cases; then I listed production gaps explicitly.”

不同面试官视角：

- 技术面：多讲 tool schema、sandbox、trace event、eval metrics。
- 主管面：多讲问题拆分、风险控制、验证闭环和下一步 rollout。
- HR/综合面：多讲学习动机、ownership、诚实边界和复盘能力。

主动白板入口：

> Let me draw the architecture to make sure we are aligned.

画完后只强调两个关键决策：第一，工具执行不信任模型输出；第二，所有结果要能从 trace/eval 找到证据。

## 8. 下一步学什么
把该模板映射到 `docs/15-project-deep-dive-playbook.md` 与 `docs/16-four-layer-followups.md`。

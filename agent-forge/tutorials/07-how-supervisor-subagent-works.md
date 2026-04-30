# 07-how-supervisor-subagent-works

## 1. 这篇解决什么问题
解释多 Agent 分工与编排，不让子 Agent 互相乱调。

## 2. 先给结论
Supervisor 负责阶段控制：Planner -> Coding -> Tester -> Reviewer。

## 3. 最小概念
handoff、state、retry once、review gate。

## 4. 对应代码在哪里
`agent_forge/agents/supervisor_agent.py` 与各 `*_agent.py`。

## 5. 运行一下看效果
`python run_demo.py --mode multi`。

## 6. 常见坑
只打印 handoff 字符串，没有真实工具执行。

## 7. 面试怎么说
我把 multi-agent 设计成“阶段机”，并在 test fail 时编码重试一次。

## 8. 下一步学什么
引入更细粒度的任务分解和失败分类重试。

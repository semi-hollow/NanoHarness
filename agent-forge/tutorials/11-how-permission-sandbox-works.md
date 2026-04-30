# 教程

## 1. 这篇解决什么问题
解释模块在 Agent 工程里的职责与风险点。

## 2. 先给结论
先保证可控执行，再追求更强智能。

## 3. 最小概念
Agent Loop / Tool Call / Observation / Permission / Trace / Eval。

## 4. 对应代码在哪里
`agent_forge/runtime/*`, `agent_forge/tools/*`, `agent_forge/safety/*`, `agent_forge/eval/*`。

## 5. 运行一下看效果
`python run_demo.py --mode single`  
`python run_demo.py --mode multi`

## 6. 常见坑
- 只做字符串脚本，没结构化 tool call。  
- 没 sandbox，路径越权。  
- 只看最终回答，不看 trace。

## 7. 面试怎么说
我把重点放在“可控执行系统”而不是“会聊天的模型”，并用测试+eval证明。

## 8. 下一步学什么
接入真实模型后处理 tool-call 解析不稳定与重试策略。

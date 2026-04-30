# Project Deep Dive Story

## 3分钟开场模板
1) Context + Role: 我做了一个可运行 Agent Harness，目标是把 LLM 从文本生成器变成可控执行系统。
2) Hardest Problem + Approach: 最难是安全与可观测，我用 allow/ask/deny + sandbox + structured trace。
3) Result + Learning: demo、unittest、eval 都可复现；线上指标[待真实压测后补充]。

## Problem-driven 版本
我发现只会调用现成框架不足以回答面试深挖，于是我自己实现最小闭环：agent loop、tool system、permission、安全拦截、trace、eval。

## 4层追问
- Agent Loop: what/why/failure/alternatives
- Tool System: what/why/failure/alternatives
- Multi-Agent: what/why/failure/alternatives
- Context: what/why/failure/alternatives
- Permission/Sandbox: what/why/failure/alternatives
- Guardrails: what/why/failure/alternatives
- Eval: what/why/failure/alternatives
- Tracing: what/why/failure/alternatives

## Trade-off
|问题|方案A|方案B|选择|原因|
|---|---|---|---|---|
|LLM|MockLLM|Real API|Mock first|离线可复现|
|测试|unittest|pytest|unittest|标准库|
|RAG|keyword|vector|keyword|先讲清思想|
|执行|workflow|agent|混用|稳定+灵活|
|代理|single|multi|场景化|复杂度成本|
|命令|allowlist|无限制bash|allowlist|安全|
|观测|JSON trace|plain logs|JSON|可审计|

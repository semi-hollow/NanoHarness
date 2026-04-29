# Agent Forge

## 1. 项目定位
Agent Forge 是一个面向 Agent 工程面试和生产化理解的 Agent Harness 项目。

## 2. 为什么这样选技术
- Python 标准库优先
- argparse CLI
- unittest 测试
- MockLLM 默认可离线运行
- JSON trace 可审计
- 关键词 RAG 简洁可讲

## 3. 快速开始
python run_demo.py --mode single
python run_demo.py --mode multi
python -m unittest discover tests
python -m agent_forge.eval.eval_runner

## 4. 学习路线
见 tutorials 与 docs。

## 7. 面试表达
I built a compact Agent Harness focused on safety, observability, and evaluation.

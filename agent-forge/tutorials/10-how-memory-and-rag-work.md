# 10-how-memory-and-rag-work

## 1. 这篇解决什么问题
解释项目里的 memory 和简化 RAG。

## 2. 先给结论
Memory 记录最近信息和观察，RAG 用关键词召回相关文档；二者都保持轻量。

## 3. 最小概念
Memory 是会话状态，RAG 是从候选文档中找相关片段。

## 4. 对应代码在哪里
`agent_forge/context/memory.py` 和 `rag.py`。

## 5. 运行一下看效果
`python3.11 -m unittest tests.test_context`。

## 6. 常见坑
长期记忆会带来隐私、污染和过期信息风险；第一版先做 bounded memory。

## 7. 面试怎么说
我没有引入向量库，是为了先讲清 retrieval 思想和 context budget。

## 8. 下一步学什么
读 `11-how-permission-sandbox-works.md`。

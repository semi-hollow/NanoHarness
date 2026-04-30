# 07-memory-and-rag

Memory 和 RAG 解决的是 Agent “应该记住什么、应该取回什么”。

## Memory

`Memory` 支持：

- 最近 N 条 session item；
- key-value memory；
- recent observations；
- clear；
- summary。

代码：`agent_forge/context/memory.py`。

## RAG

V2 用关键词检索，不用向量库。原因是第一版要讲清 retrieval 思想，而不是被向量数据库部署卡住。

代码：`agent_forge/context/rag.py`。

## 和生产 RAG 的区别

生产中可以升级为：

- embedding retrieval；
- hybrid search；
- reranker；
- LSP/symbol provider；
- semantic summarizer。

面试讲法：我先用可解释的 keyword RAG 验证 context pipeline，再保留向量检索和 LSP 的演进路径。

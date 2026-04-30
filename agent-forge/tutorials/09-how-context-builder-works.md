# 09-how-context-builder-works

## 1. 这篇解决什么问题
解释 Agent 每轮到底“看见”了什么。

## 2. 先给结论
ContextBuilder 输出 system prompt、task、repo map、retrieved docs、memory、tools、permission summary 和 budget report。

## 3. 最小概念
上下文不是越多越好；要选择、排序、截断并记录。

## 4. 对应代码在哪里
`agent_forge/context/context_builder.py`、`file_ranker.py`、`symbol_search.py`。

## 5. 运行一下看效果
single demo trace 的 `context_assembly` 会记录 selected_files、total_chars、truncated。

## 6. 常见坑
把整个 repo 塞进 prompt 会增加成本和噪音，也可能截断关键文件。

## 7. 面试怎么说
我用 budget report 证明 context 是被管理的，而不是无脑拼接。

## 8. 下一步学什么
读 `10-how-memory-and-rag-work.md`。

# 06-context-engineering

V2 把 context engineering 从“repo map + keyword RAG”增强成四个部分：repo_map、retrieved docs、memory、selected files，并输出 budget report。

## 组件

- `repo_map.py`：列出仓库文件，避开 `.git`、`__pycache__`、build 产物。
- `rag.py`：轻量关键词检索，用于讲清 retrieval 的最小形态。
- `symbol_search.py`：扫描 `.py` 文件，识别 `class Xxx` 和 `def xxx`。
- `file_ranker.py`：按任务词、文件名、文件内容把相关文件排到前面。
- `context_builder.py`：输出 `repo_map / retrieved_docs / memory / selected_files / total_chars / truncated`。

## Context Budget Report

budget report 的重点不是精确 token，而是让面试官看到：上下文不是无限塞，系统会记录选了什么、截断了什么、总字符量是多少。

## 失败模式

- 关键词不命中：用 symbol_search 补函数/类名。
- 文件太多：file_ranker 只选前几个相关文件。
- 真实 LSP 不可用：V2 用 AST symbol search 做可运行 fallback。

# 18-how-context-v2-works

Context V2 由 repo map、symbol search、file ranker、budget report 组成。

建议这样读：

1. `repo_map.py`：先得到候选文件列表。
2. `symbol_search.py`：用 AST 找 Python class/function。
3. `file_ranker.py`：把和任务相关的文件排前面。
4. `context_builder.py`：输出 report，说明选了什么、截断了什么。

练习：

```bash
python3.11 -m unittest tests.test_context
```

面试表达：V2 没有直接接 LSP，是为了先保留一个零依赖、可测试、可 fallback 的 symbol provider。

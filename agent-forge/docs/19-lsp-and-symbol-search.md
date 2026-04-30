# 19-lsp-and-symbol-search

Agent Forge V2 新增 `symbol_search`，用于解释 context engineering 从 grep 到 LSP 的演进路径。

## grep、symbol_search、LSP 的区别

| 能力 | 优点 | 缺点 | V2 中的位置 |
| --- | --- | --- | --- |
| grep | 快、简单、语言无关 | 只知道文本，不知道语义 | 适合找关键词和错误信息 |
| symbol_search | 能识别 Python `class` / `def` | 只做静态 AST，不解析跨文件引用 | V2 默认实现 |
| LSP | 支持 go-to-definition、references、rename、诊断 | 需要语言服务器、索引、进程管理 | 后续扩展方向 |

## 为什么 V2 先做 symbol_search

面试项目需要展示“架构上知道更强方案，但 MVP 先做可验证的窄切片”。`symbol_search` 使用标准库 `ast` 扫描 `.py` 文件，能稳定找到函数和类，不引入 LSP server 的安装复杂度。

## 后续如何接 LSP

可以把 `symbol_search(query, root)` 抽象成 `SymbolProvider`：

- `ASTSymbolProvider`：当前 V2 实现。
- `LSPSymbolProvider`：启动 pyright/pylsp，通过 JSON-RPC 请求 workspace/symbol、definition、references。
- `CompositeSymbolProvider`：LSP 不可用时 fallback 到 AST。

接入 LSP 后，context builder 可以优先选择 definition/references，再用 file_ranker 排序相关文件。

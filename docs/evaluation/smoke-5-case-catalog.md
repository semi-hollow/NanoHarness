# SWE-bench Smoke-5 Case Catalog

## 1. 集合契约

- 数据集：`princeton-nlp/SWE-bench_Lite` / `test`
- 候选全集：`300` 个 case
- 目标：以较低成本回归 Harness 的代码检索、工具循环、patch 生成、验证和证据链；它不是模型排行榜，也不估计总体解决率。
- 选择方法：从 SWE-bench Lite test 的 300 个 case 中人工分层选择：五个不同仓库、五种问题族，控制单 case 规模，同时保留从最小修复到多分支/多 hunk 修改的难度差异。

### 选择约束

- 每个 case 只修改一个源码文件，参考 patch 不超过三个 hunk。
- 每个 case 都有 FAIL_TO_PASS 和 PASS_TO_PASS 测试契约。
- 运行时只向 Agent 提供 issue 与 base commit，不提供 test patch 或 gold patch。

### 覆盖维度

- 算法语义与嵌套调用
- 类型边界与框架兼容
- 公共 API 与版本解析
- AST rewrite 与诊断质量
- 继承语义与对象布局

### 结论边界

- 五个 case 只能支持机制回归和 case study，不能代表 SWE-bench Lite 总体表现。
- candidate patch 只表示生成了 diff，正确性必须由官方 per-case 评测确认。
- 单次运行不估计模型随机方差；质量结论需要固定配置后的重复 matched runs。

## 2. Case 目录

| Case | 问题类型 | Harness 观察点 | 选择理由 |
| --- | --- | --- | --- |
| `astropy__astropy-12907` | 算法正确性 / 嵌套组合 | 代码定位、语义推理、最小 patch、测试验证 | 用最小算法修复检查 Agent 能否跨调用链定位语义错误，而不是只改表面条件。 |
| `django__django-11133` | 类型边界 / Framework 兼容 | 类型识别、公共 API、回归保护 | 覆盖框架类型边界，检查小 patch 是否同时保留既有 bytes/string 行为。 |
| `matplotlib__matplotlib-18869` | 公共 API / 版本解析 | 需求澄清、多分支实现、边界输入、API 兼容 | 覆盖规则较多的公共 API 变更，观察长 patch、边界输入和兼容性推理。 |
| `pytest-dev__pytest-5103` | AST Rewrite / 可诊断性 | AST 导航、多 hunk patch、错误报告质量 | 覆盖 AST rewrite 与三处协同修改，检查多 hunk 编辑和诊断质量。 |
| `sympy__sympy-20590` | 继承语义 / 对象布局 | 继承链定位、大仓导航、对象布局、回归保护 | 覆盖继承链和对象布局，检查 Agent 能否从现象追到非局部根因。 |

## 3. 查看验收契约

```bash
forge bench case <instance_id>
```

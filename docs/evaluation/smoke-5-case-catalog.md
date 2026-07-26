# SWE-bench Smoke-5 Case Catalog

## 1. 集合契约

- 数据集：`SWE-bench/SWE-bench_Verified` / `test`
- 候选全集：`500` 个 case
- 目标：以较低成本回归 Harness 的代码检索、工具循环、patch 生成、验证和证据链；它不是模型排行榜，也不估计总体解决率。
- 选择方法：从 SWE-bench Verified test 的 500 个经人工确认 case 中分层选择：五个不同仓库、五种问题族；控制单 case 规模，同时保留语义定位和多 hunk 修改差异。

### 选择约束

- 每个 case 只修改一个源码文件，参考 patch 不超过三个 hunk。
- 每个 case 都有 FAIL_TO_PASS 和 PASS_TO_PASS 测试契约。
- 运行时只向 Agent 提供 issue 与 base commit，不提供 test patch 或 gold patch。

### 覆盖维度

- 算法语义与嵌套调用
- 类型边界与框架兼容
- 公共 API 与类型层级
- 状态生命周期与诊断可靠性
- 继承语义与对象布局

### 结论边界

- 五个 case 只能支持机制回归和 case study，不能代表 SWE-bench Verified 总体表现。
- candidate patch 只表示生成了 diff，正确性必须由官方 per-case 评测确认。
- 单次运行不估计模型随机方差；质量结论需要固定配置后的重复 matched runs。

## 2. Case 目录

| Case | 问题类型 | Harness 观察点 | 选择理由 |
| --- | --- | --- | --- |
| `astropy__astropy-12907` | 算法正确性 / 嵌套组合 | 代码定位、语义推理、最小 patch、测试验证 | 用最小算法修复检查 Agent 能否跨调用链定位语义错误，而不是只改表面条件。 |
| `django__django-11133` | 类型边界 / Framework 兼容 | 类型识别、公共 API、回归保护 | 覆盖框架类型边界，检查小 patch 是否同时保留既有 bytes/string 行为。 |
| `matplotlib__matplotlib-20859` | 公共 API / 类型层级 | 跨模块导航、类型层级、公共 API、回归保护 | 覆盖 API 类型层级问题，检查 Agent 能否找到更稳定的共同抽象而非添加特例。 |
| `pytest-dev__pytest-10051` | 状态生命周期 / 可诊断性 | 状态生命周期、别名语义、多 hunk patch、诊断可靠性 | 覆盖可观测状态的对象身份问题，检查 Agent 能否从 API 冲突追到生命周期根因。 |
| `sympy__sympy-20590` | 继承语义 / 对象布局 | 继承链定位、大仓导航、对象布局、回归保护 | 覆盖继承链和对象布局，检查 Agent 能否从现象追到非局部根因。 |

## 3. 查看验收契约

```bash
forge bench case <instance_id>
```

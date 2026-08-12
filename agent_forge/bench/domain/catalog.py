"""内置基础设施检查集合的人工策展契约。

本文件只有稳定常量和类型化 profile，不加载 Hugging Face 数据集。阅读时先看
``INFRASTRUCTURE_SMOKE_5_PROFILE`` 的集合目标，再按 ``CASE_PROFILES`` 查看每题选择理由。
"""

from agent_forge.bench.domain.case_inspection import (
    BenchmarkCaseProfile,
    BenchmarkSetProfile,
)


DEFAULT_DATASET = "SWE-bench/SWE-bench_Verified"
SHOWCASE_INSTANCE_ID = "astropy__astropy-12907"
SHOWCASE_INSTANCE_NOTE = (
    "Astropy nested CompoundModel separability bug. This case is small enough "
    "for local runs but forces real repository checkout, context retrieval, "
    "tool use, patch generation, and trace/usage inspection."
)
# 单题语义：回答“每个 case 具体测什么、为什么选它”。
CASE_PROFILES = {
    SHOWCASE_INSTANCE_ID: BenchmarkCaseProfile(
        instance_id=SHOWCASE_INSTANCE_ID,
        title="嵌套 CompoundModel 的可分离矩阵错误",
        issue_type="算法正确性 / 嵌套组合",
        summary="嵌套组合后矩阵语义错误，需要定位到组合矩阵的构造逻辑。",
        harness_signals=("代码定位", "语义推理", "最小 patch", "测试验证"),
        selection_reason="用最小算法修复检查 Agent 能否跨调用链定位语义错误，而不是只改表面条件。",
    ),
    "django__django-11133": BenchmarkCaseProfile(
        instance_id="django__django-11133",
        title="HttpResponse 错误处理 memoryview",
        issue_type="类型边界 / Framework 兼容",
        summary="PostgreSQL BinaryField 返回 memoryview，响应却写成对象字符串。",
        harness_signals=("类型识别", "公共 API", "回归保护"),
        selection_reason="覆盖框架类型边界，检查小 patch 是否同时保留既有 bytes/string 行为。",
    ),
    "matplotlib__matplotlib-20859": BenchmarkCaseProfile(
        instance_id="matplotlib__matplotlib-20859",
        title="SubFigure 无法正确添加 legend",
        issue_type="公共 API / 类型层级",
        summary="Legend 只接受 Figure，导致同属 FigureBase 层级的 SubFigure 被错误拒绝。",
        harness_signals=("跨模块导航", "类型层级", "公共 API", "回归保护"),
        selection_reason="覆盖 API 类型层级问题，检查 Agent 能否找到更稳定的共同抽象而非添加特例。",
    ),
    "pytest-dev__pytest-10051": BenchmarkCaseProfile(
        instance_id="pytest-dev__pytest-10051",
        title="caplog.clear 破坏既有 records 引用",
        issue_type="状态生命周期 / 可诊断性",
        summary="clear 通过替换列表丢失调用方持有的引用，需要保留对象身份并原地清理。",
        harness_signals=("状态生命周期", "别名语义", "多 hunk patch", "诊断可靠性"),
        selection_reason="覆盖可观测状态的对象身份问题，检查 Agent 能否从 API 冲突追到生命周期根因。",
    ),
    "sympy__sympy-20590": BenchmarkCaseProfile(
        instance_id="sympy__sympy-20590",
        title="Symbol 意外重新获得 __dict__",
        issue_type="继承语义 / 对象布局",
        summary="父类遗漏 __slots__，导致 Symbol 实例出现不应存在的 __dict__。",
        harness_signals=("继承链定位", "大仓导航", "对象布局", "回归保护"),
        selection_reason="覆盖继承链和对象布局，检查 Agent 能否从现象追到非局部根因。",
    ),
}

# 集合成员：执行顺序稳定，便于 matched regression 比较。
INFRASTRUCTURE_SMOKE_5_CASE_IDS = tuple(CASE_PROFILES)
REGRESSION_SETS = {
    "infrastructure-smoke-5": list(INFRASTRUCTURE_SMOKE_5_CASE_IDS)
}

# 集合契约：回答“从 500 题中为什么只选这 5 题、结论能外推到哪里”。
INFRASTRUCTURE_SMOKE_5_PROFILE = BenchmarkSetProfile(
    name="infrastructure-smoke-5",
    dataset_name=DEFAULT_DATASET,
    split="test",
    universe_case_count=500,
    objective=(
        "以五个固定真实任务检查 dataset、checkout、工具循环、patch、official evaluator "
        "与证据发布是否端到端健康；它不用于配置选择、模型排行或解决率估计。"
    ),
    selection_method=(
        "从 SWE-bench Verified test 的 500 个经人工确认 case 中分层选择：五个不同仓库、"
        "五种问题族；控制单 case 规模，同时保留语义定位和多 hunk 修改差异。"
    ),
    selection_constraints=(
        "每个 case 只修改一个源码文件，参考 patch 不超过三个 hunk。",
        "每个 case 都有 FAIL_TO_PASS 和 PASS_TO_PASS 测试契约。",
        "运行时只向 Agent 提供 issue 与 base commit，不提供 test patch 或 gold patch。",
    ),
    coverage_dimensions=(
        "算法语义与嵌套调用",
        "类型边界与框架兼容",
        "公共 API 与类型层级",
        "状态生命周期与诊断可靠性",
        "继承语义与对象布局",
    ),
    claim_limits=(
        "五个 case 只支持基础设施健康检查和机制回归，不能代表 SWE-bench Verified 总体表现。",
        "本集合不进入 Canonical scorecard，也不用于选择 showcase 模型或预算。",
        "candidate patch 只表示生成了 diff，正确性必须由官方 per-case 评测确认。",
        "单次运行不估计模型随机方差；质量结论需要固定配置后的重复 matched runs。",
    ),
)

REGRESSION_SET_PROFILES = {
    INFRASTRUCTURE_SMOKE_5_PROFILE.name: INFRASTRUCTURE_SMOKE_5_PROFILE
}

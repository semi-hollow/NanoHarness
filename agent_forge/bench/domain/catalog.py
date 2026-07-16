from agent_forge.bench.domain.case_inspection import (
    BenchmarkCaseProfile,
    BenchmarkSetProfile,
)


DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"
SHOWCASE_INSTANCE_ID = "astropy__astropy-12907"
SHOWCASE_INSTANCE_NOTE = (
    "Astropy nested CompoundModel separability bug. This case is small enough "
    "for local runs but forces real repository checkout, context retrieval, "
    "tool use, patch generation, and trace/usage inspection."
)
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
    "matplotlib__matplotlib-18869": BenchmarkCaseProfile(
        instance_id="matplotlib__matplotlib-18869",
        title="增加可比较的顶层版本信息",
        issue_type="公共 API / 版本解析",
        summary="将版本字符串解析成可比较结构，并兼容 rc、dev、post 等形式。",
        harness_signals=("需求澄清", "多分支实现", "边界输入", "API 兼容"),
        selection_reason="覆盖规则较多的公共 API 变更，观察长 patch、边界输入和兼容性推理。",
    ),
    "pytest-dev__pytest-5103": BenchmarkCaseProfile(
        instance_id="pytest-dev__pytest-5103",
        title="展开 all/any assertion 以改善失败报告",
        issue_type="AST Rewrite / 可诊断性",
        summary="改写生成器断言，使失败输出指出具体未满足条件的元素。",
        harness_signals=("AST 导航", "多 hunk patch", "错误报告质量"),
        selection_reason="覆盖 AST rewrite 与三处协同修改，检查多 hunk 编辑和诊断质量。",
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

SMOKE_5_CASE_IDS = tuple(CASE_PROFILES)
REGRESSION_SETS = {"smoke-5": list(SMOKE_5_CASE_IDS)}

SMOKE_5_PROFILE = BenchmarkSetProfile(
    name="smoke-5",
    dataset_name=DEFAULT_DATASET,
    split="test",
    universe_case_count=300,
    objective=(
        "以较低成本回归 Harness 的代码检索、工具循环、patch 生成、验证和证据链；"
        "它不是模型排行榜，也不估计总体解决率。"
    ),
    selection_method=(
        "从 SWE-bench Lite test 的 300 个 case 中人工分层选择：五个不同仓库、五种问题族，"
        "控制单 case 规模，同时保留从最小修复到多分支/多 hunk 修改的难度差异。"
    ),
    selection_constraints=(
        "每个 case 只修改一个源码文件，参考 patch 不超过三个 hunk。",
        "每个 case 都有 FAIL_TO_PASS 和 PASS_TO_PASS 测试契约。",
        "运行时只向 Agent 提供 issue 与 base commit，不提供 test patch 或 gold patch。",
    ),
    coverage_dimensions=(
        "算法语义与嵌套调用",
        "类型边界与框架兼容",
        "公共 API 与版本解析",
        "AST rewrite 与诊断质量",
        "继承语义与对象布局",
    ),
    claim_limits=(
        "五个 case 只能支持机制回归和 case study，不能代表 SWE-bench Lite 总体表现。",
        "candidate patch 只表示生成了 diff，正确性必须由官方 per-case 评测确认。",
        "单次运行不估计模型随机方差；质量结论需要固定配置后的重复 matched runs。",
    ),
)

REGRESSION_SET_PROFILES = {SMOKE_5_PROFILE.name: SMOKE_5_PROFILE}

from __future__ import annotations

from agent_forge.bench.domain.case_inspection import (
    BenchmarkCaseInspection,
    BenchmarkCaseProfile,
    BenchmarkSetProfile,
)


def render_case_catalog(
    set_profile: BenchmarkSetProfile,
    profiles: tuple[BenchmarkCaseProfile, ...],
) -> str:
    """把固定回归集合渲染成可快速扫描的中文目录。"""

    display_name = set_profile.name.capitalize()
    lines = [
        f"# SWE-bench {display_name} Case Catalog",
        "",
        "## 1. 集合契约",
        "",
        f"- 数据集：`{set_profile.dataset_name}` / `{set_profile.split}`",
        f"- 候选全集：`{set_profile.universe_case_count}` 个 case",
        f"- 目标：{set_profile.objective}",
        f"- 选择方法：{set_profile.selection_method}",
        "",
        "### 选择约束",
        "",
        *(f"- {item}" for item in set_profile.selection_constraints),
        "",
        "### 覆盖维度",
        "",
        *(f"- {item}" for item in set_profile.coverage_dimensions),
        "",
        "### 结论边界",
        "",
        *(f"- {item}" for item in set_profile.claim_limits),
        "",
        "## 2. Case 目录",
        "",
        "| Case | 问题类型 | Harness 观察点 | 选择理由 |",
        "| --- | --- | --- | --- |",
    ]
    for profile in profiles:
        signals = "、".join(profile.harness_signals)
        lines.append(
            f"| `{profile.instance_id}` | {profile.issue_type} | "
            f"{signals} | {profile.selection_reason} |"
        )
    lines.extend(
        [
            "",
            "## 3. 查看验收契约",
            "",
            "```bash",
            "forge bench case <instance_id>",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def render_case_inspection(
    inspection: BenchmarkCaseInspection,
    *,
    show_test_patch: bool = False,
    show_gold_patch: bool = False,
    show_all_tests: bool = False,
) -> str:
    """渲染一个 case；默认只显示输入和测试名称，不显示评测补丁或参考答案。"""

    profile = inspection.profile
    title = profile.title if profile else inspection.instance_id
    lines = [
        f"# SWE-bench Case：{inspection.instance_id}",
        "",
        f"> {title}",
        "",
        "## 1. 这个 Case 在做什么",
        "",
    ]
    if profile:
        lines.extend(
            [
                f"- 问题类型：{profile.issue_type}",
                f"- 一句话：{profile.summary}",
                f"- Harness 观察点：{'、'.join(profile.harness_signals)}",
                f"- 入选原因：{profile.selection_reason}",
            ]
        )
    else:
        lines.append("该 case 不在内置 smoke-5 中，请结合下方原始 issue 判断问题类型。")
    lines.extend(
        [
            "",
            "## 2. Agent 收到的任务",
            "",
            inspection.problem_statement.strip(),
            "",
            "## 3. 固定代码起点",
            "",
            f"- Repository：`{inspection.repo}`",
            f"- Base commit：`{inspection.base_commit}`",
            f"- Version：`{inspection.version or 'not recorded'}`",
            "",
            "## 4. 怎样判定成功",
            "",
            "`FAIL_TO_PASS` 是修复前失败、应用 candidate patch 后必须通过的目标测试。",
            "",
        ]
    )
    lines.extend(_render_tests("FAIL_TO_PASS", inspection.fail_to_pass, show_all_tests))
    lines.extend(
        [
            "",
            "`PASS_TO_PASS` 是修复前已经通过、应用 patch 后仍必须通过的回归保护测试。",
            "",
        ]
    )
    lines.extend(_render_tests("PASS_TO_PASS", inspection.pass_to_pass, show_all_tests))
    lines.extend(["", "## 5. 隐藏材料与数据泄漏边界", ""])
    if show_test_patch:
        lines.extend(
            [
                "以下 test patch 只用于人工理解官方怎样验收，不应加入 Agent prompt。",
                "",
                "```diff",
                inspection.test_patch.rstrip(),
                "```",
            ]
        )
    else:
        lines.append("- Official test patch：隐藏；使用 `--show-test-patch` 在复盘时查看。")
    if show_gold_patch:
        summary = inspection.gold_patch_summary
        lines.extend(
            [
                "",
                "以下 gold patch 是参考答案，只能在运行结束后复盘，不能用于调试该 case。",
                "",
                f"- Files：{', '.join(f'`{item}`' for item in summary.files) or 'none'}",
                f"- Hunks：{summary.hunks}",
                f"- Changed lines：+{summary.additions}/-{summary.deletions}",
                "",
                "```diff",
                inspection.gold_patch.rstrip(),
                "```",
            ]
        )
    else:
        lines.append("- Gold patch：隐藏；使用 `--show-gold` 仅做运行后的根因复盘。")
    return "\n".join(lines).rstrip() + "\n"


def _render_tests(
    label: str,
    tests: tuple[str, ...],
    show_all: bool,
) -> list[str]:
    limit = len(tests) if show_all else min(12, len(tests))
    lines = [f"### {label}（{len(tests)}）", ""]
    if not tests:
        return [*lines, "- 数据集中没有记录。"]
    lines.extend(f"- `{test}`" for test in tests[:limit])
    remaining = len(tests) - limit
    if remaining:
        lines.append(f"- ... 其余 {remaining} 个；使用 `--all-tests` 展开。")
    return lines

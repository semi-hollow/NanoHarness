from __future__ import annotations

import json
from typing import Any

from agent_forge.bench.domain.case_inspection import (
    BenchmarkCaseInspection,
    BenchmarkCaseProfile,
    PatchSummary,
)
from agent_forge.bench.domain.models import BenchCase


class InspectBenchCase:
    """把原始数据集字段转换成人类可读、默认不泄漏答案的 case 契约。"""

    # 主要入口：把一个数据集 case 转换成可展示的验收契约。
    @staticmethod
    def execute(
        case: BenchCase,
        *,
        profile: BenchmarkCaseProfile | None = None,
    ) -> BenchmarkCaseInspection:
        """解析 issue、测试集合和仅供复盘的 patch 元数据。"""

        return BenchmarkCaseInspection(
            instance_id=case.instance_id,
            repo=case.repo,
            base_commit=case.base_commit,
            version=str(case.raw.get("version") or ""),
            problem_statement=case.problem_statement,
            hints_text=case.hints_text,
            fail_to_pass=_string_tuple(case.raw.get("FAIL_TO_PASS")),
            pass_to_pass=_string_tuple(case.raw.get("PASS_TO_PASS")),
            profile=profile,
            test_patch=case.test_patch,
            gold_patch=str(case.raw.get("patch") or ""),
            gold_patch_summary=_summarize_patch(str(case.raw.get("patch") or "")),
        )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return (value,) if value.strip() else ()
        value = decoded
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def _summarize_patch(patch: str) -> PatchSummary:
    files: list[str] = []
    hunks = additions = deletions = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                files.append(parts[2].removeprefix("a/"))
        elif line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return PatchSummary(
        files=tuple(dict.fromkeys(files)),
        hunks=hunks,
        additions=additions,
        deletions=deletions,
    )

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = PROJECT_ROOT / "benchmarks" / "experiments"
EXPECTED_EXPERIMENTS = (
    "01-runtime-preset-50x2",
    "02-context-budget-exploration",
    "03-runtime-quality-golden-10",
    "04-operation-ledger-replay",
    "05-quality-selection-v1",
    "06-tool-aci-golden-20",
    "07-tool-aci-r2-minimal-surface",
)


def test_experiment_index_and_one_directory_per_experiment() -> None:
    index = (EXPERIMENT_ROOT / "README.md").read_text(encoding="utf-8")
    directories = tuple(
        path.name for path in sorted(EXPERIMENT_ROOT.iterdir()) if path.is_dir()
    )
    assert directories == EXPECTED_EXPERIMENTS
    for experiment in EXPECTED_EXPERIMENTS:
        record = EXPERIMENT_ROOT / experiment / "README.md"
        assert record.is_file()
        assert f"{experiment}/README.md" in index


def test_each_experiment_records_identity_result_decision_and_evidence() -> None:
    required_headings = ("## 实验身份", "## 决策", "## 证据定位")
    for experiment in EXPECTED_EXPERIMENTS:
        content = (EXPERIMENT_ROOT / experiment / "README.md").read_text(
            encoding="utf-8"
        )
        for heading in required_headings:
            assert heading in content, f"{experiment} missing {heading}"
        assert re.search(r"[0-9a-f]{40}", content), experiment


def test_current_experiment_evidence_links_resolve() -> None:
    records = (
        EXPERIMENT_ROOT / "05-quality-selection-v1" / "README.md",
        EXPERIMENT_ROOT / "06-tool-aci-golden-20" / "README.md",
        EXPERIMENT_ROOT / "07-tool-aci-r2-minimal-surface" / "README.md",
    )
    markdown_link = re.compile(r"\[[^]]+]\((?P<target>[^)#]+)(?:#[^)]*)?\)")
    for record in records:
        content = record.read_text(encoding="utf-8")
        for match in markdown_link.finditer(content):
            target = match.group("target")
            if "://" in target:
                continue
            assert (record.parent / target).resolve().is_file(), (
                record.relative_to(PROJECT_ROOT),
                target,
            )


def test_index_separates_completed_experiments_from_unrun_plans() -> None:
    index = (EXPERIMENT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "仅有计划、尚无结果的资产" in index
    assert "尚未发布 `X/50`" in index
    assert "invalid_no_winner" in index
    assert "不能横向比较百分比" in index

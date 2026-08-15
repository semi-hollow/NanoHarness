from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmarks"
EXPERIMENT_ROOT = BENCHMARK_ROOT / "experiments"
ARCHIVE_ROOT = BENCHMARK_ROOT / "archive" / "legacy-benchmarks"
ACTIVE_EXPERIMENTS = ("tool-aci-r1", "tool-aci-r2")
ARCHIVED_EXPERIMENTS = (
    "01-runtime-preset-50x2",
    "02-context-budget-exploration",
    "03-runtime-quality-golden-10",
    "04-operation-ledger-replay",
    "05-quality-selection-v1",
)


def _local_markdown_targets(record: Path) -> tuple[Path, ...]:
    pattern = re.compile(r"\[[^]]+]\((?P<target>[^)#]+)(?:#[^)]*)?\)")
    targets: list[Path] = []
    for match in pattern.finditer(record.read_text(encoding="utf-8")):
        target = match.group("target")
        if "://" not in target:
            targets.append((record.parent / target).resolve())
    return tuple(targets)


def test_active_experiment_surface_contains_only_r1_and_r2() -> None:
    directories = tuple(
        path.name for path in sorted(EXPERIMENT_ROOT.iterdir()) if path.is_dir()
    )
    assert directories == ACTIVE_EXPERIMENTS

    index = (EXPERIMENT_ROOT / "README.md").read_text(encoding="utf-8")
    for experiment in ACTIVE_EXPERIMENTS:
        assert f"{experiment}/README.md" in index
        root = EXPERIMENT_ROOT / experiment
        assert (root / "README.md").is_file()
        assert (root / "plan.json").is_file()
        assert (root / "result.json").is_file()
        assert (root / "report.md").is_file()


def test_active_experiment_links_and_machine_artifacts_resolve() -> None:
    for experiment in ACTIVE_EXPERIMENTS:
        root = EXPERIMENT_ROOT / experiment
        for target in _local_markdown_targets(root / "README.md"):
            assert target.is_file(), target
        json.loads((root / "plan.json").read_text(encoding="utf-8"))
        json.loads((root / "result.json").read_text(encoding="utf-8"))
        execution_indexes = tuple(root.glob("*.execution.json"))
        assert execution_indexes
        for index in execution_indexes:
            json.loads(index.read_text(encoding="utf-8"))


def test_r2_record_contains_exact_code_identity_and_decision() -> None:
    record = (EXPERIMENT_ROOT / "tool-aci-r2" / "README.md").read_text(encoding="utf-8")

    assert "563a99fe72b078fa91bfb682d60d6d19f398a864" in record
    assert "92f4de56a1391b58e8e249471ebd4ec04102f60b" in record
    assert "find_files" in record
    assert "repo_outline" in record
    assert "validation" in record
    assert "14/20" in record
    assert "Reject" in record


def test_legacy_experiments_are_archived_instead_of_deleted() -> None:
    archived_root = ARCHIVE_ROOT / "experiments"
    assert (
        tuple(path.name for path in sorted(archived_root.iterdir()) if path.is_dir())
        == ARCHIVED_EXPERIMENTS
    )
    for experiment in ARCHIVED_EXPERIMENTS:
        assert (archived_root / experiment / "README.md").is_file()

    archive_index = (BENCHMARK_ROOT / "archive" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "不再拥有可运行的专用 runner" in archive_index
    assert "当前入口也不会把它们作为产品能力" in archive_index

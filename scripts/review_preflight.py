#!/usr/bin/env python3
"""只读检查本机 Evidence Review 是否具备完整、可追溯的演示输入。"""

from __future__ import annotations

import hashlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_forge.workbench.adapters.evidence_files import FileEvidenceCatalog
from agent_forge.workbench.application.review_projection import (
    REVIEW_MANIFEST,
    build_lab1_review,
    build_lab2_review,
    build_mini50_review,
    load_review_manifest,
)
from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    ForgeUiHandler,
    _render_workspace_view,
)


EVIDENCE_TREE_ALGORITHM = "sha256-relative-path-and-content-v1"


@dataclass(frozen=True)
class Check:
    section: str
    name: str
    passed: bool
    detail: str


def main() -> int:
    before = _raw_evidence_fingerprints()
    checks = [
        *_check_docs(),
        *_check_workbench(),
        *_check_lab1(),
        *_check_lab2(),
        *_check_mini50(),
        *_check_boundary(),
    ]
    after = _raw_evidence_fingerprints()
    checks.append(
        Check(
            "Boundary",
            "Preflight is read-only",
            before == after,
            "critical raw evidence-tree hashes unchanged",
        )
    )
    current_section = ""
    for check in checks:
        if check.section != current_section:
            current_section = check.section
            print(f"\n[{current_section}]")
        mark = "PASS" if check.passed else "FAIL"
        print(f"{mark:4}  {check.name}: {check.detail}")
    failed = [check for check in checks if not check.passed]
    print("\nREADY" if not failed else f"\nNOT READY · {len(failed)} blocking issue(s)")
    return 0 if not failed else 1


def _check_docs() -> list[Check]:
    architecture = PROJECT_ROOT / "docs/架构导览.md"
    readme = PROJECT_ROOT / "README.md"
    architecture_text = _text(architecture)
    readme_text = _text(readme)
    main_headings = [
        line
        for line in architecture_text.splitlines()
        if line.startswith("# ") and line != "# 架构导览"
    ]
    expected_main_headings = [
        "# 1. 系统定位",
        "# 2. 总体主链",
        "# 3. Agent 运行数据模型",
        "# 4. LLM 输入的三块来源",
        "# 5. 提示窗口（Prompt Window）",
        "# 6. 运行治理（Runtime Governance）",
        "# 7. 持久化控制面（Durable Control Plane）",
        "# 8. 多 Agent 编排（Multi-Agent）",
        "# 9. 评测（Evaluation）",
        "# 10. 文档导航",
    ]
    stable_anchors = ("system", "context", "governance", "durability", "evaluation")
    return [
        Check("Docs", "Architecture Guide", architecture.is_file(), str(architecture)),
        Check(
            "Docs",
            "Canonical architecture sections",
            main_headings == expected_main_headings,
            " | ".join(main_headings),
        ),
        Check(
            "Docs",
            "Stable architecture anchors",
            all(
                f'<a id="{anchor}"></a>' in architecture_text
                for anchor in stable_anchors
            ),
            " / ".join(f"#{anchor}" for anchor in stable_anchors),
        ),
        Check(
            "Docs",
            "Canonical Workbench links",
            all(
                token in architecture_text + readme_text
                for token in (
                    "source=governed",
                    "source=orchestration",
                    "source=evaluation",
                )
            ),
            "Lab 1 / Lab 2 / Mini-50 deep links",
        ),
    ]


def _check_workbench() -> list[Check]:
    sources = FileEvidenceCatalog(PROJECT_ROOT).evidence_sources()
    rendered = {
        key: _render_workspace_view(
            PROJECT_ROOT,
            source_key=key,
            view="overview",
            sources=sources,
        )
        for key in ("governed", "orchestration", "evaluation")
    }
    return [
        Check(
            "Workbench",
            "Review projection render smoke",
            all(
                "QUESTION" in value and "OBSERVED ARTIFACT" in value
                for value in rendered.values()
            ),
            "three canonical overview pages rendered",
        ),
        Check(
            "Workbench",
            "Deep-link state",
            all(
                token in INDEX_HTML
                for token in (
                    "window.history[operation]",
                    "window.addEventListener('popstate'",
                    "copyReviewLink",
                    "pageParams.get('source')",
                )
            ),
            "source/view/focus restore + Back/Forward + Copy Link",
        ),
    ]


def _check_lab1() -> list[Check]:
    manifest = load_review_manifest(PROJECT_ROOT)
    configured = _source_config(manifest, "governed")
    source = _source("governed")
    review = build_lab1_review(PROJECT_ROOT, source)
    artifact = source.primary_path
    expected_hash = str(configured.get("canonical_sha256") or "")
    tree_ok, tree_detail = _evidence_tree_integrity(
        artifact.parent if artifact is not None else Path(),
        configured,
    )
    return [
        Check(
            "Lab 1",
            "Canonical full Run",
            source.run_key == configured.get("canonical_run")
            and review.status == "completed"
            and review.state_sequence
            == ("waiting_human", "waiting_approval", "completed"),
            " → ".join(review.state_sequence),
        ),
        Check(
            "Lab 1",
            "Canonical artifact hash",
            bool(artifact and artifact.is_file())
            and _sha256(artifact) == expected_hash,
            expected_hash[:12],
        ),
        Check(
            "Lab 1",
            "Critical evidence tree",
            tree_ok,
            tree_detail,
        ),
        Check(
            "Lab 1",
            "HumanInput / Approval / Ledger / Checkpoint / Trace",
            len(review.authorities) == 5
            and all(
                item.status not in {"not_observed", ""} for item in review.authorities
            ),
            ", ".join(f"{item.owner}={item.status}" for item in review.authorities),
        ),
        Check(
            "Lab 1",
            "Three control invariants",
            len(review.invariants) == 3
            and all(item.observed for item in review.invariants),
            f"{sum(item.observed for item in review.invariants)}/3 observed",
        ),
    ]


def _check_lab2() -> list[Check]:
    manifest = load_review_manifest(PROJECT_ROOT)
    configured = _source_config(manifest, "orchestration")
    source = _source("orchestration")
    review = build_lab2_review(PROJECT_ROOT, source)
    expected_hash = str(configured.get("canonical_sha256") or "")
    run_root = (
        source.primary_path.parent.parent if source.primary_path is not None else Path()
    )
    tree_ok, tree_detail = _evidence_tree_integrity(run_root, configured)
    task_ids = {task.task_id for task in review.tasks}
    return [
        Check(
            "Lab 2",
            "Canonical plan and summary",
            source.run_key == configured.get("canonical_run")
            and source.primary_path is not None
            and _sha256(source.primary_path) == expected_hash,
            f"{source.run_key} · {expected_hash[:12]}",
        ),
        Check(
            "Lab 2",
            "Critical evidence tree",
            tree_ok,
            tree_detail,
        ),
        Check(
            "Lab 2",
            "Batches and workers",
            task_ids == {"pricing-policy", "shipping-policy", "edge-case-verifier"}
            and review.batches
            == (("pricing-policy", "shipping-policy"), ("edge-case-verifier",)),
            "Batch 0 parallel · Batch 1 verifier",
        ),
        Check(
            "Lab 2",
            "Conflict gates and Finalizer",
            not review.conflicts
            and review.final_decision == "PASS"
            and review.finalizer_trace is not None,
            f"conflicts={len(review.conflicts)} · Finalizer={review.final_decision}",
        ),
    ]


def _check_mini50() -> list[Check]:
    sources = FileEvidenceCatalog(PROJECT_ROOT).evidence_sources()
    source = next(item for item in sources if item.key == "evaluation")
    review = build_mini50_review(PROJECT_ROOT, source, sources)
    cases = [
        item
        for item in sources
        if item.category_key == "evaluation" and item.item_key != "overview"
    ]
    revision_exists = _git_object_exists(review.evaluated_revision)
    return [
        Check(
            "Mini-50",
            "Canonical exactly 50",
            len(cases) == 50 and len({item.item_key for item in cases}) == 50,
            f"{len(cases)} unique terminal trajectories",
        ),
        Check(
            "Mini-50",
            "Canonical distribution",
            (review.resolved, review.unresolved, review.empty_patch) == (28, 16, 6),
            f"{review.resolved}/{review.unresolved}/{review.empty_patch}",
        ),
        Check(
            "Mini-50",
            "Representative cases",
            len(review.representatives) >= 3
            and all(item.source_key for item in review.representatives),
            ", ".join(item.case_id for item in review.representatives),
        ),
        Check(
            "Mini-50",
            "Evaluated revision exists",
            revision_exists,
            review.evaluated_revision,
        ),
        Check(
            "Mini-50",
            "Publish funnel",
            review.total_launches == 61 and not review.correctness_rerun,
            f"61 launches · correctness rerun={review.correctness_rerun}",
        ),
    ]


def _check_boundary() -> list[Check]:
    post_source = inspect.getsource(ForgeUiHandler.do_POST)
    evaluation_html = _render_workspace_view(
        PROJECT_ROOT,
        source_key="evaluation",
        view="overview",
    )
    return [
        Check(
            "Boundary",
            "Workbench mutation rejected",
            "METHOD_NOT_ALLOWED" in post_source and "_send_json" in post_source,
            "POST → 405",
        ),
        Check(
            "Boundary",
            "Revision provenance displayed",
            "EVALUATED REVISION" in evaluation_html
            and "CURRENT REPOSITORY HEAD" in evaluation_html
            and "correctness rerun not performed" in evaluation_html,
            "evaluated revision and current HEAD remain distinct",
        ),
    ]


def _source(key: str):
    return next(
        item
        for item in FileEvidenceCatalog(PROJECT_ROOT).evidence_sources()
        if item.key == key
    )


def _source_config(manifest: dict[str, object], key: str) -> dict[str, object]:
    sources = manifest.get("sources")
    sources = sources if isinstance(sources, dict) else {}
    value = sources.get(key)
    return value if isinstance(value, dict) else {}


def _raw_evidence_fingerprints() -> tuple[tuple[str, str], ...]:
    manifest = load_review_manifest(PROJECT_ROOT)
    values: list[tuple[str, str]] = []
    for key in ("governed", "orchestration"):
        configured = _source_config(manifest, key)
        run_name = str(configured.get("canonical_run") or "")
        root = PROJECT_ROOT / ".agent_forge/runs/showcases" / run_name
        count, digest = _evidence_tree(root, _evidence_tree_patterns(configured))
        values.append((str(root), f"{count}:{digest}"))
    return tuple(values)


def _evidence_tree_integrity(
    root: Path,
    configured: dict[str, object],
) -> tuple[bool, str]:
    tree = configured.get("evidence_tree")
    if not isinstance(tree, dict):
        return False, "manifest evidence_tree is missing"
    raw_patterns = tree.get("include")
    patterns = _evidence_tree_patterns(configured)
    if (
        not isinstance(raw_patterns, list)
        or not raw_patterns
        or len(patterns) != len(raw_patterns)
    ):
        return False, "manifest evidence_tree.include is invalid"
    count, digest = _evidence_tree(root, patterns)
    expected_count = tree.get("file_count")
    expected_digest = str(tree.get("sha256") or "")
    passed = (
        tree.get("algorithm") == EVIDENCE_TREE_ALGORITHM
        and isinstance(expected_count, int)
        and count == expected_count
        and digest == expected_digest
    )
    expected = expected_count if isinstance(expected_count, int) else "missing"
    return (
        passed,
        f"{count}/{expected} files · sha256 {digest[:12]}"
        + ("" if digest == expected_digest else f" (expected {expected_digest[:12]})"),
    )


def _evidence_tree_patterns(configured: dict[str, object]) -> tuple[str, ...]:
    tree = configured.get("evidence_tree")
    tree = tree if isinstance(tree, dict) else {}
    include = tree.get("include")
    include = include if isinstance(include, list) else []
    return tuple(item for item in include if isinstance(item, str) and item)


def _evidence_tree(root: Path, patterns: tuple[str, ...]) -> tuple[int, str]:
    files = {
        path for pattern in patterns for path in root.glob(pattern) if path.is_file()
    }
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return len(files), digest.hexdigest()


def _git_object_exists(revision: str) -> bool:
    if not revision:
        return False
    import subprocess

    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


if __name__ == "__main__":
    raise SystemExit(main())

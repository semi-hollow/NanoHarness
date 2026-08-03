import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
HAN_CHARACTER = re.compile(r"[\u3400-\u9fff]")
LATIN_CHARACTER = re.compile(r"[A-Za-z]")

CHINESE_FIRST_DOCS = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "FORGE.md",
    "SECURITY.md",
    "agent_forge/README.md",
    "docs/CAPABILITY_REALITY_MATRIX.md",
    "docs/FEATURE_EVOLUTION.md",
    "docs/ROADMAP.md",
    "docs/case-studies/astropy-12907.md",
    "docs/evaluation/failure-taxonomy.md",
    "docs/evaluation/mini-cases/README.md",
    "docs/evaluation/regression-set.md",
    "examples/debug_lab/README.md",
)

PUBLIC_DOC_LINE_BUDGETS = {
    "README.md": 250,
    "CONTRIBUTING.md": 140,
    "docs/ARCHITECTURE.md": 420,
    "docs/CAPABILITY_REALITY_MATRIX.md": 120,
    "docs/FEATURE_EVOLUTION.md": 260,
    "docs/ROADMAP.md": 120,
    "examples/debug_lab/README.md": 210,
}

CANONICAL_README_LINKS = (
    "docs/FEATURE_EVOLUTION.md",
    "docs/ARCHITECTURE.md",
    "docs/CAPABILITY_REALITY_MATRIX.md",
    "docs/evaluation/failure-driven-improvements.md",
    "docs/evaluation/regression-set.md",
    "examples/debug_lab/README.md",
)

STUDY_NOTES_CONTROL_PLANE = (
    "https://github.com/semi-hollow/NanoHarness-Study-Notes"
)

PROTECTED_PUBLIC_RECORDS = {
    "docs/evaluation/failure-driven-improvements.md": 56,
}

ALLOWED_DOC_SURFACES = (
    "docs/adr/",
    "docs/architecture/",
    "docs/case-studies/",
    "docs/evaluation/",
)

ALLOWED_TOP_LEVEL_DOCS = {
    "docs/ARCHITECTURE.md",
    "docs/CAPABILITY_REALITY_MATRIX.md",
    "docs/FEATURE_EVOLUTION.md",
    "docs/ROADMAP.md",
}


class DocumentationLanguageTest(unittest.TestCase):
    def test_public_documents_are_chinese_first(self) -> None:
        self.maxDiff = None
        violations: list[str] = []
        for relative_path in CHINESE_FIRST_DOCS:
            path = PROJECT_ROOT / relative_path
            self.assertTrue(path.exists(), f"missing public document: {relative_path}")
            in_fence = False
            for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                line = raw_line.strip()
                if line.startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence or not line:
                    continue
                if line.startswith("[![") or re.fullmatch(r"\|?[\s:|-]+\|?", line):
                    continue
                visible = re.sub(r"`[^`]*`", "", line)
                visible = re.sub(r"\[[^]]*]\([^)]*\)", "", visible)
                is_project_name = relative_path == "README.md" and line_number == 1
                if line.startswith("#") and not is_project_name and not HAN_CHARACTER.search(visible):
                    violations.append(f"{relative_path}:{line_number}: heading has no Chinese: {line}")
                    continue
                latin_count = len(LATIN_CHARACTER.findall(visible))
                if latin_count >= 40 and not HAN_CHARACTER.search(visible):
                    violations.append(f"{relative_path}:{line_number}: English prose remains: {line}")
        self.assertEqual(violations, [], "Public documentation must be Chinese-first")

    def test_public_document_control_plane_stays_focused(self) -> None:
        violations: list[str] = []
        for relative_path, line_budget in PUBLIC_DOC_LINE_BUDGETS.items():
            path = PROJECT_ROOT / relative_path
            self.assertTrue(path.exists(), f"missing canonical document: {relative_path}")
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > line_budget:
                violations.append(
                    f"{relative_path}: {line_count} lines exceeds {line_budget}-line budget"
                )

        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for relative_path in CANONICAL_README_LINKS:
            if relative_path not in readme:
                violations.append(f"README does not link canonical document: {relative_path}")
        if STUDY_NOTES_CONTROL_PLANE not in readme or "学习顺序只认" not in readme:
            violations.append(
                "README must defer learning order to the Study Notes control plane"
            )

        lab_path = PROJECT_ROOT / "examples/debug_lab/README.md"
        lab = lab_path.read_text(encoding="utf-8")
        required_lab_contracts = (
            "NanoHarness Lab 1 - Governed Repair",
            "NanoHarness Lab 2 - Coordinated Agents",
            "NanoHarness Lab 3 - Complex Live Repair",
            "scripts/install_pycharm_debug_lab.py",
            "Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence",
            "自然修复",
            "上下文压力",
            "人工控制与恢复",
            "六问不过，就不算学会",
        )
        for contract in required_lab_contracts:
            if contract not in lab:
                violations.append(f"Debug Lab lost learning contract: {contract}")
        for relative_path in (
            "examples/debug_lab/run.py",
            "examples/debug_lab/support.py",
            "examples/debug_lab/repository/calculator.py",
            "examples/debug_lab/repository/test_calculator.py",
            "examples/debug_lab/multi_agent_repository/pricing.py",
            "examples/debug_lab/multi_agent_repository/shipping.py",
            "examples/debug_lab/multi_agent_repository/test_checkout.py",
            "examples/debug_lab/complex_repository/settlement/service.py",
            "examples/debug_lab/complex_repository/tests/test_reconciliation.py",
            "examples/debug_lab/complex_repository/tests/test_atomicity.py",
            "examples/operator_console.py",
            "scripts/install_pycharm_debug_lab.py",
            "scripts/interview_demo.sh",
            ".run/NanoHarness Lab 1 - Governed Repair.run.xml",
            ".run/NanoHarness Lab 2 - Coordinated Agents.run.xml",
            ".run/NanoHarness Lab 3 - Complex Live Repair.run.xml",
        ):
            if not (PROJECT_ROOT / relative_path).is_file():
                violations.append(f"Debug Lab support is missing: {relative_path}")
        for obsolete in (
            "scripts/learning_session.sh",
            "scripts/learning_debug.py",
            "docs/runbooks/从命令到Evidence全链路实操.md",
        ):
            if (PROJECT_ROOT / obsolete).exists():
                violations.append(f"obsolete learning surface returned: {obsolete}")

        for relative_path, minimum_case_count in PROTECTED_PUBLIC_RECORDS.items():
            path = PROJECT_ROOT / relative_path
            if not path.exists():
                violations.append(f"protected first-party record was deleted: {relative_path}")
                continue
            case_count = len(
                re.findall(r"^### \d+\.", path.read_text(encoding="utf-8"), re.MULTILINE)
            )
            if case_count < minimum_case_count:
                violations.append(
                    f"{relative_path}: protected cases fell from {minimum_case_count} "
                    f"to {case_count}"
                )

        result = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "docs/*.md",
                "docs/**/*.md",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # 未暂存重命名时，git 仍会列出已从工作区删除的旧路径；治理只检查当前公开树。
        tracked_docs = {
            path
            for path in result.stdout.splitlines()
            if path and (PROJECT_ROOT / path).is_file()
        }
        if len(tracked_docs) > 18:
            violations.append(
                f"docs tree has {len(tracked_docs)} Markdown files; consolidate before adding more"
            )
        for relative_path in sorted(tracked_docs):
            if relative_path in ALLOWED_TOP_LEVEL_DOCS:
                continue
            if not relative_path.startswith(ALLOWED_DOC_SURFACES):
                violations.append(f"public document has no approved owner surface: {relative_path}")

        self.assertEqual(violations, [], "Public documentation control plane has drifted")


if __name__ == "__main__":
    unittest.main()

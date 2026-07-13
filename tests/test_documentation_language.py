import re
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
    "scripts/README.md",
    "docs/AgentForge总体架构与运行链路.md",
    "docs/CAPABILITY_REALITY_MATRIX.md",
    "docs/ROADMAP.md",
    "docs/多Agent协作机制与对比评测说明.md",
    "docs/architecture/evaluation-experiments-and-oci-execution.md",
    "docs/architecture/feedback-evaluation-loop.md",
    "docs/architecture/human-input-and-live-fanout.md",
    "docs/architecture/runtime-capability-guide.md",
    "docs/architecture/核心组件索引与职责边界.md",
    "docs/case-studies/astropy-12907.md",
    "docs/evaluation/failure-taxonomy.md",
    "docs/evaluation/mini-cases/README.md",
    "docs/evaluation/regression-set.md",
    "docs/evaluation/评测目录说明与SWE-bench使用入口.md",
    "docs/guides/code-reading-map.md",
    "docs/guides/runtime-learning-path.md",
)


class DocumentationLanguageTest(unittest.TestCase):
    def test_teaching_documents_are_chinese_first(self) -> None:
        self.maxDiff = None
        violations: list[str] = []
        for relative_path in CHINESE_FIRST_DOCS:
            path = PROJECT_ROOT / relative_path
            self.assertTrue(path.exists(), f"missing teaching document: {relative_path}")
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
        self.assertEqual(violations, [], "Teaching documentation must be Chinese-first")


if __name__ == "__main__":
    unittest.main()

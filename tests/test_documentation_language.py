import ast
import re
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
HAN_CHARACTER = re.compile(r"[\u3400-\u9fff]")
LATIN_CHARACTER = re.compile(r"[A-Za-z]")

CHINESE_FIRST_DOCS = (
    "README.md",
    "FORGE.md",
    "SECURITY.md",
    "agent_forge/README.md",
    "docs/核心能力与代码入口.md",
    "docs/核心运行机制与代码索引.md",
    "docs/项目架构与代码导航.md",
    "docs/能力实现状态与使用边界.md",
    "docs/运行生命周期与异常处理机制.md",
    "docs/功能演进与设计取舍.md",
    "docs/architecture/代码结构演进与可读性治理.md",
    "docs/evaluation/典型故障与系统调优记录.md",
    "docs/evaluation/失败分类规则与诊断流程.md",
    "docs/evaluation/回归测试与评测范围.md",
    "examples/debug_lab/README.md",
)

PUBLIC_DOC_LINE_BUDGETS = {
    "README.md": 250,
    "docs/核心能力与代码入口.md": 120,
    "docs/核心运行机制与代码索引.md": 100,
    "docs/项目架构与代码导航.md": 420,
    "docs/能力实现状态与使用边界.md": 120,
    "docs/功能演进与设计取舍.md": 270,
    "examples/debug_lab/README.md": 210,
}

CANONICAL_README_LINKS = (
    "docs/核心能力与代码入口.md",
    "docs/核心运行机制与代码索引.md",
    "docs/运行生命周期与异常处理机制.md",
    "docs/项目架构与代码导航.md",
    "docs/能力实现状态与使用边界.md",
    "docs/evaluation/典型故障与系统调优记录.md",
    "docs/evaluation/回归测试与评测范围.md",
    "examples/debug_lab/README.md",
)

PROTECTED_PUBLIC_RECORDS = {
    "docs/evaluation/典型故障与系统调优记录.md": 15,
}

REQUIRED_FAILURE_RECORD_TOPICS = (
    "相同 ToolCall",
    "ToolRouter",
    "Context",
    "长期记忆",
    "`ask_human`",
    "operation key",
    "操作状态表",
    "Checkpoint",
    "Multi-Agent",
    "Failure Taxonomy",
    "Usage",
    "数据飞轮",
)

LOW_VALUE_FAILURE_HEADING_MARKERS = (
    "Workbench",
    "PyCharm",
    "Windows",
    "Docker",
    "浏览器",
    "窄屏",
)

ALLOWED_DOC_SURFACES = (
    "docs/architecture/",
    "docs/evaluation/",
)

ALLOWED_TOP_LEVEL_DOCS = {
    "docs/核心能力与代码入口.md",
    "docs/核心运行机制与代码索引.md",
    "docs/项目架构与代码导航.md",
    "docs/能力实现状态与使用边界.md",
    "docs/运行生命周期与异常处理机制.md",
    "docs/功能演进与设计取舍.md",
}

PUBLIC_POSITIONING_FORBIDDEN = (
    "面" + "试",
    "求" + "职",
    "简" + "历",
    "inter" + "view",
    "offer" + "-readiness",
)

# 公开仓库可以提供教程和渐进式代码导航，但必须保持项目文档口径，且不能依赖未作为
# 公开项目组成部分交付的外部资料仓库。字符串拆分可避免本测试扫描自身。
PUBLIC_NON_PROJECT_LANGUAGE_FORBIDDEN = (
    "NanoHarness-Study" + "-Notes",
    "追问时" + "才展开",
    "追问时" + "再展开",
    "被追问时" + "应",
    "不要整页" + "背诵",
    "不需要先" + "背诵",
    "技术讲解" + "主口径",
    "唯一动态" + "学习入口",
    "学会" + "标准",
    "六问" + "不过",
    "最短学习" + "路径",
    "面向学习" + "者",
    "复杂结算修复" + "学习场",
    "对外怎么" + "讲",
    "准备技术" + "追问",
    "第一遍" + "只读",
    "首遍" + "只读",
    "首次阅读" + "只看",
    "首次学习" + "可",
    "学习主线" + "时",
    "必须亲手" + "完成",
    "亲手" + "练习",
    "才算" + "掌握",
    "复杂任务深度" + "练习",
    "学习目标" + "：",
    "闭卷" + "自检",
    "90 秒架构" + "说明",
    "可以诚实承认的" + "不足",
)


class DocumentationLanguageTest(unittest.TestCase):
    def test_explanatory_document_filenames_are_chinese_first(self) -> None:
        """面向读者的 docs 文件名必须直接说明用途，不再使用模糊英文组合词。"""

        violations = [
            path.relative_to(PROJECT_ROOT).as_posix()
            for path in (PROJECT_ROOT / "docs").rglob("*.md")
            if not HAN_CHARACTER.search(path.name)
        ]
        self.assertEqual(violations, [], "Explanatory Markdown filenames must be Chinese-first")

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

    def test_public_repository_uses_project_facing_language(self) -> None:
        """公开源码只描述项目范围、工程事实、操作方法和验证标准。"""

        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        text_suffixes = {
            ".md",
            ".py",
            ".sh",
            ".json",
            ".xml",
            ".toml",
            ".yaml",
            ".yml",
        }
        violations: list[str] = []
        for relative_path in result.stdout.splitlines():
            path = PROJECT_ROOT / relative_path
            if path.suffix.lower() not in text_suffixes or not path.is_file():
                continue
            content = path.read_text(encoding="utf-8").casefold()
            for forbidden in (
                *PUBLIC_POSITIONING_FORBIDDEN,
                *PUBLIC_NON_PROJECT_LANGUAGE_FORBIDDEN,
            ):
                if forbidden.casefold() in content:
                    violations.append(
                        f"{relative_path}: contains non-project positioning: {forbidden}"
                    )
        self.assertEqual(violations, [], "Public repository language has drifted")

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

        # 两份索引都提供可点击 Owner；路径、符号和精确行号必须与当前源码一致。
        owner_index_paths = (
            PROJECT_ROOT / "docs/核心能力与代码入口.md",
            PROJECT_ROOT / "docs/核心运行机制与代码索引.md",
        )
        parsed_symbols: dict[Path, dict[str, int]] = {}
        for owner_index_path in owner_index_paths:
            owner_index = owner_index_path.read_text(encoding="utf-8")
            owner_links = list(
                re.finditer(
                    r"\[\s*`(?P<symbol>[^`]+)`\s*\]\("
                    r"(?P<target>\.\./agent_forge/[^)#]+\.py)"
                    r"(?:#L(?P<line>[1-9][0-9]*))?\)",
                    owner_index,
                )
            )
            if not owner_links:
                violations.append(f"{owner_index_path.name} 没有可校验的 Owner 链接")
            for owner_link in owner_links:
                symbol = owner_link.group("symbol")
                target = owner_link.group("target")
                linked_line = owner_link.group("line")
                if linked_line is None:
                    violations.append(f"Owner 链接缺少精确行号: {target}::{symbol}")
                    continue
                source_path = (owner_index_path.parent / target).resolve()
                try:
                    source_path.relative_to(PROJECT_ROOT / "agent_forge")
                except ValueError:
                    violations.append(f"Owner 链接逃逸 agent_forge: {target}")
                    continue
                if not source_path.is_file():
                    violations.append(f"Owner 链接不存在: {target}")
                    continue
                if source_path not in parsed_symbols:
                    tree = ast.parse(source_path.read_text(encoding="utf-8"))
                    available: dict[str, int] = {}
                    for node in tree.body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            available[node.name] = node.lineno
                        if isinstance(node, ast.ClassDef):
                            for child in node.body:
                                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                    available[f"{node.name}.{child.name}"] = child.lineno
                    parsed_symbols[source_path] = available
                if symbol not in parsed_symbols[source_path]:
                    violations.append(f"Owner 符号不存在: {target}::{symbol}")
                elif int(linked_line) != parsed_symbols[source_path][symbol]:
                    violations.append(
                        f"Owner 行号已漂移: {target}::{symbol} "
                        f"#L{linked_line} != #L{parsed_symbols[source_path][symbol]}"
                    )
        lab_path = PROJECT_ROOT / "examples/debug_lab/README.md"
        lab = lab_path.read_text(encoding="utf-8")
        required_lab_contracts = (
            "NanoHarness Lab 1 - Governed Repair",
            "NanoHarness Lab 2 - Coordinated Agents",
            "NanoHarness Lab 3 - Complex Live Repair",
            "NanoHarness Evidence Workbench - Read Only",
            "scripts/install_pycharm_debug_lab.py",
            "Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence",
            "自然修复",
            "上下文压力",
            "人工控制与恢复",
            "以上结论必须由本次 Trace 支撑",
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
            "scripts/showcase_demo.sh",
            ".run/NanoHarness Lab 1 - Governed Repair.run.xml",
            ".run/NanoHarness Lab 2 - Coordinated Agents.run.xml",
            ".run/NanoHarness Lab 3 - Complex Live Repair.run.xml",
            ".run/NanoHarness Evidence Workbench - Read Only.run.xml",
        ):
            if not (PROJECT_ROOT / relative_path).is_file():
                violations.append(f"Debug Lab support is missing: {relative_path}")
        for obsolete in (
            "scripts/learning_session.sh",
            "scripts/learning_debug.py",
            "scripts/setup_macos_local.sh",
            "scripts/setup_windows_local.ps1",
            "scripts/setup_wsl_local.sh",
            "scripts/start_workbench.command",
            "scripts/verify.ps1",
            "scripts/verify_mcp.ps1",
            "docs/runbooks/从命令到Evidence全链路实操.md",
        ):
            if (PROJECT_ROOT / obsolete).exists():
                violations.append(f"obsolete learning surface returned: {obsolete}")

        for relative_path, minimum_case_count in PROTECTED_PUBLIC_RECORDS.items():
            path = PROJECT_ROOT / relative_path
            if not path.exists():
                violations.append(f"protected first-party record was deleted: {relative_path}")
                continue
            record_text = path.read_text(encoding="utf-8")
            case_headings = re.findall(
                r"^### \d+\. .+$",
                record_text,
                re.MULTILINE,
            )
            case_count = len(case_headings)
            if case_count < minimum_case_count:
                violations.append(
                    f"{relative_path}: curated runtime cases fell below "
                    f"{minimum_case_count}: {case_count}"
                )
            for topic in REQUIRED_FAILURE_RECORD_TOPICS:
                if topic not in record_text:
                    violations.append(
                        f"{relative_path}: required runtime topic is missing: {topic}"
                    )
            for marker in LOW_VALUE_FAILURE_HEADING_MARKERS:
                if any(marker in heading for heading in case_headings):
                    violations.append(
                        f"{relative_path}: low-value delivery issue returned as a case: "
                        f"{marker}"
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

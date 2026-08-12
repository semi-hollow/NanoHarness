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
    "examples/debug_lab/README.md",
)

PUBLIC_DOC_LINE_BUDGETS = {
    "README.md": 250,
    "docs/核心能力与代码入口.md": 120,
    "docs/核心运行机制与代码索引.md": 100,
    "examples/debug_lab/README.md": 210,
}

CANONICAL_README_LINKS = (
    "docs/核心能力与代码入口.md",
    "docs/核心运行机制与代码索引.md",
    "examples/debug_lab/README.md",
)

ALLOWED_DOC_SURFACES: tuple[str, ...] = ()

ALLOWED_TOP_LEVEL_DOCS = {
    "docs/核心能力与代码入口.md",
    "docs/核心运行机制与代码索引.md",
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

        # 能力索引只保留可搜索 Owner，避免把 Markdown 文件链接误写成精确定义跳转。
        capability_index = (
            PROJECT_ROOT / "docs/核心能力与代码入口.md"
        ).read_text(encoding="utf-8")
        if re.search(r"\]\(\.\./agent_forge/", capability_index):
            violations.append("核心能力索引不应包含本地源码链接")
        if re.search(r"`L[1-9][0-9]*`", capability_index):
            violations.append("核心能力索引不应维护易漂移行号")

        # 机制索引仍保留代码证据链接；路径、符号和行号必须与源码一致。
        owner_index_paths = (
            PROJECT_ROOT / "docs/核心运行机制与代码索引.md",
        )
        parsed_symbols: dict[Path, dict[str, int]] = {}
        for owner_index_path in owner_index_paths:
            owner_index = owner_index_path.read_text(encoding="utf-8")
            owner_links = list(
                re.finditer(
                    r"\[\s*`(?P<symbol>[^`]+)`\s*\]\("
                    r"(?P<target>\.\./agent_forge/[^)#]+\.py)\)"
                    r"\s*·\s*`L(?P<line>[1-9][0-9]*)`",
                    owner_index,
                )
            )
            if not owner_links:
                violations.append(f"{owner_index_path.name} 没有可校验的 Owner 链接")
            for owner_link in owner_links:
                symbol = owner_link.group("symbol")
                target = owner_link.group("target")
                linked_line = owner_link.group("line")
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
        if len(tracked_docs) > 2:
            violations.append(
                f"docs tree has {len(tracked_docs)} Markdown files; public surface allows 2"
            )
        for relative_path in sorted(tracked_docs):
            if relative_path in ALLOWED_TOP_LEVEL_DOCS:
                continue
            if not relative_path.startswith(ALLOWED_DOC_SURFACES):
                violations.append(f"public document has no approved owner surface: {relative_path}")

        self.assertEqual(violations, [], "Public documentation control plane has drifted")

    def test_cheatsheet_owners_have_explanatory_docstrings(self) -> None:
        """两个代码索引中的每个 Owner 都必须能定位，且入口自身能够解释职责。"""

        capability_path = PROJECT_ROOT / "docs/核心能力与代码入口.md"
        capability_text = capability_path.read_text(encoding="utf-8")
        owner_columns = {
            "最小主链": (1,),
            "核心能力代码索引": (1, 2),
            "按需能力代码索引": (1, 2),
        }
        owner_symbol_pattern = re.compile(
            r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`"
        )
        owner_symbols: set[str] = set()
        active_section = ""
        for line in capability_text.splitlines():
            if line.startswith("## "):
                active_section = line.removeprefix("## ").strip()
                continue
            if active_section not in owner_columns or not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            for column in owner_columns[active_section]:
                if column < len(cells):
                    owner_symbols.update(owner_symbol_pattern.findall(cells[column]))

        mechanism_text = (
            PROJECT_ROOT / "docs/核心运行机制与代码索引.md"
        ).read_text(encoding="utf-8")
        owner_symbols.update(
            re.findall(
                r"\[\s*`([^`]+)`\s*\]\(\.\./agent_forge/[^)#]+\.py\)",
                mechanism_text,
            )
        )

        source_index: dict[
            str,
            list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]],
        ] = {}
        for source_path in (PROJECT_ROOT / "agent_forge").rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    source_index.setdefault(node.name, []).append((source_path, node))
                if isinstance(node, ast.ClassDef):
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            qualified_name = f"{node.name}.{child.name}"
                            source_index.setdefault(qualified_name, []).append(
                                (source_path, child)
                            )

        violations: list[str] = []
        for owner_symbol in sorted(owner_symbols):
            candidates = source_index.get(owner_symbol, [])
            if len(candidates) != 1:
                violations.append(
                    f"Owner 必须唯一对应源码定义: {owner_symbol} ({len(candidates)} matches)"
                )
                continue
            source_path, node = candidates[0]
            docstring = ast.get_docstring(node, clean=True) or ""
            visible_length = len(re.sub(r"\s+", "", docstring))
            if visible_length < 24:
                relative_path = source_path.relative_to(PROJECT_ROOT).as_posix()
                violations.append(
                    f"Owner 注释不足以说明职责: {owner_symbol} "
                    f"({relative_path}:{node.lineno})"
                )

        self.assertGreaterEqual(len(owner_symbols), 60, "Cheat Sheet Owner 提取结果异常")
        self.assertEqual(violations, [], "Cheat Sheet Owner 必须可定位且可直接理解")


if __name__ == "__main__":
    unittest.main()

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
    "docs/架构导览.md",
    "docs/Agent运行数据结构与模型输入.md",
    "docs/上下文工程.md",
    "docs/上下文压缩与长任务设计.md",
    "docs/运行治理与工具执行.md",
    "docs/多Agent编排.md",
    "docs/生产化边界与扩展.md",
    "docs/核心能力与代码入口.md",
    "docs/运行产物与持久化契约.md",
    "docs/DOCUMENTATION_RULES.md",
    "examples/debug_lab/README.md",
)

PUBLIC_DOC_LINE_BUDGETS = {
    "README.md": 250,
    "docs/架构导览.md": 450,
    "docs/Agent运行数据结构与模型输入.md": 430,
    "docs/上下文工程.md": 350,
    "docs/上下文压缩与长任务设计.md": 320,
    "docs/运行治理与工具执行.md": 620,
    "docs/多Agent编排.md": 420,
    "docs/生产化边界与扩展.md": 220,
    "docs/核心能力与代码入口.md": 280,
    "docs/运行产物与持久化契约.md": 340,
    "docs/DOCUMENTATION_RULES.md": 180,
    "examples/debug_lab/README.md": 210,
}

CANONICAL_README_LINKS = (
    "docs/架构导览.md",
    "docs/Agent运行数据结构与模型输入.md",
    "docs/上下文工程.md",
    "docs/上下文压缩与长任务设计.md",
    "docs/运行治理与工具执行.md",
    "docs/多Agent编排.md",
    "docs/核心能力与代码入口.md",
    "docs/运行产物与持久化契约.md",
    "examples/debug_lab/README.md",
    "benchmarks/experiments/README.md",
)

ALLOWED_DOC_SURFACES: tuple[str, ...] = ()

APPROVED_TECHNICAL_DOC_NAMES = {"docs/DOCUMENTATION_RULES.md"}

ALLOWED_TOP_LEVEL_DOCS = {
    "docs/架构导览.md",
    "docs/Agent运行数据结构与模型输入.md",
    "docs/上下文工程.md",
    "docs/上下文压缩与长任务设计.md",
    "docs/运行治理与工具执行.md",
    "docs/多Agent编排.md",
    "docs/生产化边界与扩展.md",
    "docs/核心能力与代码入口.md",
    "docs/运行产物与持久化契约.md",
    "docs/DOCUMENTATION_RULES.md",
}

MAX_PUBLIC_DOCS = len(ALLOWED_TOP_LEVEL_DOCS)

REVIEW_FIRST_DOCS = {
    "docs/上下文工程.md": ("# 2. 当前 Model Step 的输入构造", 70),
    "docs/运行治理与工具执行.md": ("# 1. 主链", 70),
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
            and path.relative_to(PROJECT_ROOT).as_posix()
            not in APPROVED_TECHNICAL_DOC_NAMES
        ]
        self.assertEqual(
            violations, [], "Explanatory Markdown filenames must be Chinese-first"
        )

    def test_public_documents_are_chinese_first(self) -> None:
        self.maxDiff = None
        violations: list[str] = []
        for relative_path in CHINESE_FIRST_DOCS:
            path = PROJECT_ROOT / relative_path
            self.assertTrue(path.exists(), f"missing public document: {relative_path}")
            in_fence = False
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
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
                if (
                    line.startswith("#")
                    and not is_project_name
                    and not HAN_CHARACTER.search(visible)
                ):
                    violations.append(
                        f"{relative_path}:{line_number}: heading has no Chinese: {line}"
                    )
                    continue
                latin_count = len(LATIN_CHARACTER.findall(visible))
                if latin_count >= 40 and not HAN_CHARACTER.search(visible):
                    violations.append(
                        f"{relative_path}:{line_number}: English prose remains: {line}"
                    )
        self.assertEqual(violations, [], "Public documentation must be Chinese-first")

    def test_review_documents_put_the_runtime_chain_before_details(self) -> None:
        """核心文档必须先给出真实主链，不能从字段或 package 清单开始。"""

        for relative_path, (chain_heading, line_limit) in REVIEW_FIRST_DOCS.items():
            lines = (
                (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").splitlines()
            )
            self.assertIn(chain_heading, lines)
            self.assertLessEqual(
                lines.index(chain_heading) + 1,
                line_limit,
                f"{relative_path} 在主链前堆积了过多背景",
            )

    def test_core_docs_use_searchable_canonical_code_symbols(self) -> None:
        """数据流和运行链必须能用文档中的真实名称直接定位源码。"""

        required_symbols = {
            "docs/上下文工程.md": (
                "AgentRunSession.messages",
                "conversation_history = list(session.messages)",
                "PromptWindowRequest.conversation_history",
                "PromptWindowManager.prepare()",
                "PromptWindowResult.llm_messages",
                "PreparedModelStep.llm_messages",
                "ModelPort.chat(...)",
                "TurnSystemContextBuildReport",
                "turn_system_message",
            ),
            "docs/运行治理与工具执行.md": (
                "ToolExecutionPipeline.execute_calls()",
                "ConversationItem(role=assistant, complete batch)",
                "TaskCheckpoint.pending_execution",
                "ToolExecutionPipeline.resume_pending_calls()",
                "ToolExecutionPipeline._execute_call()",
                "OperationTracker.build_operation_intent()",
                "ToolAuthorizationGate.authorize()",
                "ToolExecutionPipeline._run_tool()",
                "ToolGateway.execute()",
                "HookManager.after_tool()",
                "OperationTracker.record_execution_result()",
                "ToolFeedback.append_tool_observation()",
                "ConversationItem(role=tool)",
            ),
        }
        missing: list[str] = []
        for relative_path, symbols in required_symbols.items():
            content = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
            missing.extend(
                f"{relative_path}: {symbol}"
                for symbol in symbols
                if symbol not in content
            )
        self.assertEqual(missing, [], "Canonical CodeSymbol 文档映射发生漂移")

    def test_public_repository_uses_project_facing_language(self) -> None:
        """公开源码只描述项目范围、工程事实、操作方法和验证标准。"""

        result = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
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
            self.assertTrue(
                path.exists(), f"missing canonical document: {relative_path}"
            )
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > line_budget:
                violations.append(
                    f"{relative_path}: {line_count} lines exceeds {line_budget}-line budget"
                )

        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        for relative_path in CANONICAL_README_LINKS:
            if relative_path not in readme:
                violations.append(
                    f"README does not link canonical document: {relative_path}"
                )

        architecture = (PROJECT_ROOT / "docs/架构导览.md").read_text(encoding="utf-8")
        main_headings = [
            line
            for line in architecture.splitlines()
            if line.startswith("# ") and line != "# 架构导览"
        ]
        expected_headings = [
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
        if main_headings != expected_headings:
            violations.append("架构导览必须保持十段唯一主链")
        for contract in (
            '<a id="system"></a>',
            '<a id="context"></a>',
            '<a id="governance"></a>',
            '<a id="durability"></a>',
            '<a id="evaluation"></a>',
            "Model proposes",
            "Runtime decides",
            "Fixed Case Cohort",
            "AdaptivePlanner.decide()",
            "build_live_fanout()",
            "FanoutCoordinator.run()",
            "_run_plan()",
            "HARD: integrated-state readiness",
            "LIVE: semantic early readiness",
            "生产化边界与扩展.md",
        ):
            if contract not in architecture:
                violations.append(f"架构导览缺少审阅契约: {contract}")
        for retired in ("docs/系统概览与核心设计.md", "docs/核心运行机制与代码索引.md"):
            if (PROJECT_ROOT / retired).exists():
                violations.append(f"重复公开文档重新出现: {retired}")

        # 能力索引保持精简，但必须明确列出主链和数据 owner，不维护易漂移行号。
        capability_path = PROJECT_ROOT / "docs/核心能力与代码入口.md"
        capability_index = capability_path.read_text(encoding="utf-8")
        if re.search(r"`L[1-9][0-9]*`", capability_index):
            violations.append("核心能力索引不应维护易漂移行号")
        for owner_symbol in (
            "Harness.run",
            "AgentLoop.run",
            "RunPreparation.prepare_run",
            "ModelStepPreparation.prepare_model_step",
            "ModelGateway.chat",
            "ToolExecutionPipeline._execute_call",
            "FinalAnswerBuilder.build_stop_request",
            "RunLifecycle.finalize_run",
        ):
            if owner_symbol not in capability_index:
                violations.append(f"核心能力索引缺少主链 Owner: {owner_symbol}")

        lab_path = PROJECT_ROOT / "examples/debug_lab/README.md"
        lab = lab_path.read_text(encoding="utf-8")
        required_lab_contracts = (
            "NanoHarness Lab 1 - Governed Repair",
            "NanoHarness Lab 2 - Coordinated Agents",
            "NanoHarness Evidence Workbench - Read Only",
            "scripts/install_pycharm_debug_lab.py",
            "Debugger 看动态因果；Workbench 看最终留下的可验证 Evidence",
            "Pause",
            "Cancel",
            "不可变 Run",
            "SWE-bench Verified Mini-50",
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
            "scripts/install_pycharm_debug_lab.py",
            "scripts/showcase_demo.sh",
            ".run/NanoHarness Lab 1 - Governed Repair.run.xml",
            ".run/NanoHarness Lab 2 - Coordinated Agents.run.xml",
            ".run/NanoHarness Evidence Workbench - Read Only.run.xml",
        ):
            if not (PROJECT_ROOT / relative_path).is_file():
                violations.append(f"Debug Lab support is missing: {relative_path}")
        for obsolete in (
            "examples/operator_console.py",
            "examples/debug_lab/complex_repository",
            ".run/NanoHarness Lab 3 - Complex Live Repair.run.xml",
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
        if len(tracked_docs) > MAX_PUBLIC_DOCS:
            violations.append(
                f"docs tree has {len(tracked_docs)} Markdown files; "
                f"public surface allows {MAX_PUBLIC_DOCS}"
            )
        for relative_path in sorted(tracked_docs):
            if relative_path in ALLOWED_TOP_LEVEL_DOCS:
                continue
            if not relative_path.startswith(ALLOWED_DOC_SURFACES):
                violations.append(
                    f"public document has no approved owner surface: {relative_path}"
                )

        self.assertEqual(
            violations, [], "Public documentation control plane has drifted"
        )

    def test_cheatsheet_owners_have_explanatory_docstrings(self) -> None:
        """代码导航文档中的每个 Owner 都必须能定位，且入口自身能够解释职责。"""

        capability_path = PROJECT_ROOT / "docs/核心能力与代码入口.md"
        capability_text = capability_path.read_text(encoding="utf-8")
        owner_columns = {
            "1. Single-Agent 主链只记 8 个 Owner": (0,),
            "2. 最重要的数据结构": (1,),
        }
        owner_symbol_pattern = re.compile(
            r"`([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`"
        )
        owner_symbols: set[str] = set()
        active_section = ""
        for line in capability_text.splitlines():
            if line.startswith("# "):
                active_section = line.removeprefix("# ").strip()
                continue
            if active_section not in owner_columns or not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            for column in owner_columns[active_section]:
                if column < len(cells):
                    owner_symbols.update(owner_symbol_pattern.findall(cells[column]))

        source_index: dict[
            str,
            list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]],
        ] = {}
        for source_path in (PROJECT_ROOT / "agent_forge").rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
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

        self.assertGreaterEqual(len(owner_symbols), 16, "核心 Owner 提取结果异常")
        self.assertEqual(violations, [], "Cheat Sheet Owner 必须可定位且可直接理解")

    def test_runtime_artifact_contract_separates_authoritative_state_and_evidence(
        self,
    ) -> None:
        """持久化文档必须区分恢复真相、事件证据和 schema migration。"""

        text = (PROJECT_ROOT / "docs/运行产物与持久化契约.md").read_text(
            encoding="utf-8"
        )
        required_contracts = (
            "Authoritative Conversation",
            "TaskCheckpoint v4",
            "Normal follow-up\n= same Thread + new Turn + new Run",
            "Resume\n= same Thread + same Turn + new Run",
            "append complete assistant batch",
            "append exactly one Tool Observation",
            "Trace 不保存完整 model prompt 或 raw Conversation",
            "Production Runtime loader 只接受当前 canonical v4",
            "read-only presentation compatibility reader",
            "model_step_started",
        )
        missing = [contract for contract in required_contracts if contract not in text]
        self.assertEqual(missing, [], "运行产物契约缺少权威边界或迁移原则")


if __name__ == "__main__":
    unittest.main()

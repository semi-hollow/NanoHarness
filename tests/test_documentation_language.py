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
    "docs/单Agent运行链路.md",
    "docs/运行治理与副作用.md",
    "docs/上下文工程与长任务.md",
    "docs/多Agent编排.md",
    "docs/持久化与恢复.md",
    "docs/核心能力与代码入口.md",
    "docs/DOCUMENTATION_RULES.md",
    "examples/debug_lab/README.md",
)

PUBLIC_DOC_LINE_BUDGETS = {
    "README.md": 250,
    "docs/架构导览.md": 250,
    "docs/单Agent运行链路.md": 330,
    "docs/运行治理与副作用.md": 480,
    "docs/上下文工程与长任务.md": 430,
    "docs/多Agent编排.md": 440,
    "docs/持久化与恢复.md": 480,
    "docs/核心能力与代码入口.md": 120,
    "docs/DOCUMENTATION_RULES.md": 540,
    "examples/debug_lab/README.md": 210,
}

CANONICAL_README_LINKS = (
    "docs/架构导览.md",
    "docs/单Agent运行链路.md",
    "docs/运行治理与副作用.md",
    "docs/上下文工程与长任务.md",
    "docs/多Agent编排.md",
    "docs/持久化与恢复.md",
    "docs/核心能力与代码入口.md",
    "docs/DOCUMENTATION_RULES.md",
    "examples/debug_lab/README.md",
    "benchmarks/experiments/README.md",
)

ALLOWED_DOC_SURFACES: tuple[str, ...] = ()

APPROVED_TECHNICAL_DOC_NAMES = {"docs/DOCUMENTATION_RULES.md"}

ALLOWED_TOP_LEVEL_DOCS = {
    "docs/架构导览.md",
    "docs/单Agent运行链路.md",
    "docs/运行治理与副作用.md",
    "docs/上下文工程与长任务.md",
    "docs/多Agent编排.md",
    "docs/持久化与恢复.md",
    "docs/核心能力与代码入口.md",
    "docs/DOCUMENTATION_RULES.md",
}

MAX_PUBLIC_DOCS = len(ALLOWED_TOP_LEVEL_DOCS)

REVIEW_FIRST_DOCS = {
    "docs/架构导览.md": ("# 1. 系统全景", 20),
    "docs/单Agent运行链路.md": ("# 1. 总体流程", 20),
    "docs/运行治理与副作用.md": ("# 1. 总体主链", 20),
    "docs/上下文工程与长任务.md": ("# 1. 总体模型", 20),
    "docs/多Agent编排.md": ("# 1. 总体流程", 20),
    "docs/持久化与恢复.md": ("# 1. 磁盘总图", 20),
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
        """文件标题和叙述正文中文优先；canonical technical heading 可保留英文。"""

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
                    line_number == 1
                    and line.startswith("#")
                    and not is_project_name
                    and not HAN_CHARACTER.search(line)
                ):
                    violations.append(
                        f"{relative_path}:{line_number}: document title has no Chinese: {line}"
                    )
                    continue
                if line.startswith("#"):
                    continue
                latin_count = len(LATIN_CHARACTER.findall(visible))
                if latin_count >= 40 and not HAN_CHARACTER.search(line):
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

    def test_canonical_markdown_links_resolve(self) -> None:
        """Canonical docs 与 README 的本地链接必须指向当前仓库中的真实目标。"""

        broken: list[str] = []
        for relative_path in ("README.md", *sorted(ALLOWED_TOP_LEVEL_DOCS)):
            source = PROJECT_ROOT / relative_path
            content = source.read_text(encoding="utf-8")
            for raw_target in re.findall(r"\]\(([^)]+)\)", content):
                target = raw_target.split("#", 1)[0]
                if not target or "://" in target:
                    continue
                resolved = (source.parent / target).resolve()
                if not resolved.exists():
                    broken.append(f"{relative_path} -> {raw_target}")
        self.assertEqual(broken, [], "Canonical Markdown contains dead local links")

    def test_execute_call_comment_stages_match_governance_doc(self) -> None:
        """核心 ToolCall 源码注释与治理文档必须保持同一七阶段顺序。"""

        stages = (
            "Route / Guardrail",
            "Special protocol / provenance",
            "Build OperationIntent",
            "Ledger replay / crash idempotency",
            "Repeat guard",
            "Authorization",
            "Execute + durable result",
        )
        source = (
            PROJECT_ROOT / "agent_forge/runtime/application/tool_execution.py"
        ).read_text(encoding="utf-8")
        method_start = source.index("    def _execute_call(")
        method_end = source.index("    # region 分支与证据叶子", method_start)
        method = source[method_start:method_end]
        document = (PROJECT_ROOT / "docs/运行治理与副作用.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            [
                method.index(f"# region {index}. {stage}")
                for index, stage in enumerate(stages, 1)
            ],
            sorted(
                method.index(f"# region {index}. {stage}")
                for index, stage in enumerate(stages, 1)
            ),
        )
        self.assertEqual(
            [document.casefold().index(stage.casefold()) for stage in stages],
            sorted(document.casefold().index(stage.casefold()) for stage in stages),
        )

    def test_core_docs_use_searchable_canonical_code_symbols(self) -> None:
        """数据流和运行链必须能用文档中的真实名称直接定位源码。"""

        required_symbols = {
            "docs/单Agent运行链路.md": (
                "Harness.run()",
                "Harness.resume()",
                "RunPreparation.create_session()",
                "RunPreparation.build_stable_turn_context_snapshot()",
                "RunPreparation.prepare_run()",
                "AgentLoop.run()",
                "ModelStepPreparation.prepare_model_step()",
                "RunLifecycle",
            ),
            "docs/运行治理与副作用.md": (
                "ToolExecutionPipeline.execute_calls()",
                "ToolExecutionPipeline.resume_pending_calls()",
                "ToolExecutionPipeline._continue_pending_batch()",
                "ToolExecutionPipeline._execute_call()",
                "ToolExecutionPipeline._run_tool()",
                "OperationTracker",
                "ToolAuthorizationGate.authorize()",
                "ToolFeedback.append_tool_observation()",
            ),
            "docs/上下文工程与长任务.md": (
                "build_stable_turn_context()",
                "PromptWindowManager.prepare()",
                "_build_digest()",
                "_merge_digest()",
                "ConversationHistoryDigest",
                "ThreadContextState",
                "LongTermMemoryService",
            ),
            "docs/多Agent编排.md": (
                "AdaptivePlanner.decide()",
                "FanoutPlan",
                "FanoutCoordinator._execute_plan()",
                "FanoutCoordinator._integrate_candidate()",
                "LocalAgentWorkerAdapter.run_worker()",
                "LiveHandoffRuntime",
                "FanoutCoordinator._restore_hard_prefix()",
            ),
            "docs/持久化与恢复.md": (
                "JsonConversationThreadRepository",
                "JsonTaskStateRepository",
                "JsonApprovalRepository",
                "JsonHumanInputRepository",
                "JsonOperationLedgerRepository",
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
            "# 1. 系统全景",
            "# 2. 运行身份",
            "# 3. Single-Agent",
            "# 4. Run Governance",
            "# 5. Context Engineering",
            "# 6. Multi-Agent",
            "# 7. Durable Control Plane",
            "# 8. 最有区分度的设计",
            "# 9. 专题关系",
        ]
        if main_headings != expected_headings:
            violations.append("架构导览必须保持九段全局模型与专题关系")
        for contract in (
            "Model proposes",
            "Runtime governs",
            "Durable facts drive recovery",
            "Trusted state has explicit ownership",
            "单Agent运行链路.md",
            "运行治理与副作用.md",
            "上下文工程与长任务.md",
            "多Agent编排.md",
            "持久化与恢复.md",
        ):
            if contract not in architecture:
                violations.append(f"架构导览缺少审阅契约: {contract}")
        for retired in (
            "docs/Agent运行数据结构与模型输入.md",
            "docs/上下文工程.md",
            "docs/上下文压缩与长任务设计.md",
            "docs/运行治理与工具执行.md",
            "docs/运行产物与持久化契约.md",
            "docs/生产化边界与扩展.md",
            "docs/系统概览与核心设计.md",
            "docs/核心运行机制与代码索引.md",
        ):
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
            "RunLifecycle.finalize_run",
            "PromptWindowManager.prepare",
            "FanoutCoordinator._execute_plan",
            "FanoutCoordinator._integrate_candidate",
        ):
            if owner_symbol not in capability_index:
                violations.append(f"核心能力索引缺少主链 Owner: {owner_symbol}")

        lab_path = PROJECT_ROOT / "examples/debug_lab/README.md"
        lab = lab_path.read_text(encoding="utf-8")
        required_lab_contracts = (
            "NanoHarness Lab 1 - Governed Repair",
            "NanoHarness Evidence Workbench - Read Only",
            "scripts/run_multi_agent_v1_smoke.py",
            "strict integration frontier",
            "本次不可变 Run",
        )
        for contract in required_lab_contracts:
            if contract not in lab:
                violations.append(f"Debug Lab lost learning contract: {contract}")
        for relative_path in (
            "examples/debug_lab/run.py",
            "examples/debug_lab/support.py",
            "examples/debug_lab/repository/calculator.py",
            "examples/debug_lab/repository/test_calculator.py",
            "scripts/install_pycharm_debug_lab.py",
            "scripts/showcase_demo.sh",
            ".run/NanoHarness Lab 1 - Governed Repair.run.xml",
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

    def test_capability_index_owners_resolve_to_source(self) -> None:
        """能力索引中的 canonical Owner 必须仍能唯一定位到当前源码。"""

        capability_text = (
            PROJECT_ROOT / "docs/核心能力与代码入口.md"
        ).read_text(encoding="utf-8")
        owner_symbols = {
            "Harness.run",
            "Harness.resume",
            "RunPreparation.create_session",
            "RunPreparation.build_stable_turn_context_snapshot",
            "RunPreparation.prepare_run",
            "AgentLoop.run",
            "ModelStepPreparation.prepare_model_step",
            "ModelGateway.chat",
            "RunLifecycle.finalize_run",
            "ToolExecutionPipeline.execute_calls",
            "ToolExecutionPipeline.resume_pending_calls",
            "ToolExecutionPipeline._execute_call",
            "ToolExecutionPipeline._run_tool",
            "OperationTracker",
            "ToolAuthorizationGate.authorize",
            "ToolFeedback.append_tool_observation",
            "ToolRouter.route",
            "build_stable_turn_context",
            "PromptWindowManager.prepare",
            "_build_digest",
            "_merge_digest",
            "ConversationHistoryDigest",
            "LongTermMemoryService",
            "AdaptivePlanner.decide",
            "FanoutPlan",
            "FanoutCoordinator._execute_plan",
            "FanoutCoordinator._integrate_candidate",
            "LocalAgentWorkerAdapter.run_worker",
            "LiveHandoffRuntime",
            "FanoutCoordinator._restore_hard_prefix",
        }

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
            if owner_symbol not in capability_text:
                violations.append(f"能力索引缺少 canonical Owner: {owner_symbol}")
                continue
            candidates = source_index.get(owner_symbol, [])
            if len(candidates) != 1:
                violations.append(
                    f"Owner 必须唯一对应源码定义: {owner_symbol} ({len(candidates)} matches)"
                )
        self.assertEqual(violations, [], "Capability Owner 必须存在且唯一可定位")

    def test_runtime_artifact_contract_separates_authoritative_state_and_evidence(
        self,
    ) -> None:
        """持久化文档必须区分 Conversation、Context、Run 与副作用状态。"""

        text = (PROJECT_ROOT / "docs/持久化与恢复.md").read_text(
            encoding="utf-8"
        )
        required_contracts = (
            "conversation.jsonl",
            "context_state.json",
            "task_state/<run_id>.json",
            "approvals/<operation_key>.json",
            "human_input/<request_id>.json",
            "operation_ledger/<operation_key>.json",
            "TaskCheckpoint\n= Run 跑到哪里",
            "OperationRecord\n= 某个 side effect 跑到哪里",
            "Trace 解释过程，但不成为 Resume authority",
            "ContextState = 7\nCheckpoint = 6",
            "Checkpoint = 7\nContextState = 6",
            "authority state 先持久化，pointer 后更新",
        )
        missing = [contract for contract in required_contracts if contract not in text]
        self.assertEqual(missing, [], "持久化文档缺少 authoritative state 边界")


if __name__ == "__main__":
    unittest.main()

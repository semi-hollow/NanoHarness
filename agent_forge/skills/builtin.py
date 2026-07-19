from __future__ import annotations

from pathlib import Path

from .registry import SkillRegistry, SkillSpec


def built_in_skill_specs() -> list[SkillSpec]:

    return [
        SkillSpec(
            name="repo_orientation",
            version="1.0.0",
            description="Understand an unfamiliar repository before editing.",
            entrypoint="builtin:repo_orientation",
            owner="agent-forge",
            permissions=["read:repo"],
            dependencies=["git:working-tree"],
            tags=["coding", "read-only", "onboarding"],
            activation_terms=["read project", "understand", "overview", "architecture", "调用链", "项目结构", "怎么看"],
            tool_names=["list_files", "read_file", "grep", "grep_search", "git_status"],
            operating_procedure=[
                "Start with git_status and a shallow file map before reading individual files.",
                "Prefer README, FORGE.md, pyproject, package config, and module entrypoints.",
                "When explaining a function, trace from public CLI or test entrypoint back to the method.",
                "Do not edit files unless the user explicitly asks for code changes.",
            ],
            done_criteria=[
                "Answer with concrete file paths and the main call chain.",
                "Separate confirmed code facts from inferred design intent.",
            ],
            failure_modes=[
                "If search results are noisy, narrow to entrypoint names or class/function identifiers.",
                "If no entrypoint is obvious, report the missing signal instead of guessing.",
            ],
        ),
        SkillSpec(
            name="targeted_code_edit",
            version="1.0.0",
            description="Implement a scoped code change in the current repository.",
            entrypoint="builtin:targeted_code_edit",
            owner="agent-forge",
            permissions=["read:repo", "write:repo", "run:validation"],
            dependencies=["git:working-tree", "python:local-venv"],
            tags=["coding", "edit", "default"],
            activation_terms=["implement", "change", "modify", "add", "补", "改", "实现", "优化", "接入"],
            tool_names=[
                "git_status",
                "list_files",
                "grep",
                "grep_search",
                "read_file",
                "apply_patch",
                "write_file",
                "run_command",
                "git_diff",
                "diagnostics",
            ],
            operating_procedure=[
                "Inspect the current worktree and nearby code patterns before editing.",
                "Make the smallest coherent change that satisfies the user goal.",
                "Use apply_patch for manual edits and keep unrelated files untouched.",
                "Run the narrowest meaningful validation command, then inspect git_diff.",
            ],
            done_criteria=[
                "The changed behavior is implemented, not only documented.",
                "A validation command or explicit blocker is reported.",
                "The final answer names changed files and remaining risk.",
            ],
            failure_modes=[
                "If a patch fails, reread the target file and regenerate a smaller patch.",
                "If validation fails, classify whether the failure is caused by this change or existing state.",
            ],
        ),
        SkillSpec(
            name="bug_fix",
            version="1.0.0",
            description="Diagnose and fix a failing behavior or failing test.",
            entrypoint="builtin:bug_fix",
            owner="agent-forge",
            permissions=["read:repo", "write:repo", "run:validation"],
            dependencies=["git:working-tree", "python:local-venv"],
            tags=["coding", "debug", "repair"],
            activation_terms=["bug", "fail", "failing", "error", "traceback", "修复", "报错", "失败", "不通过"],
            tool_names=[
                "diagnostics",
                "run_command",
                "grep",
                "grep_search",
                "read_file",
                "apply_patch",
                "git_diff",
                "git_status",
            ],
            operating_procedure=[
                "Reproduce or inspect the failure evidence before editing.",
                "Find the smallest code path that explains the failure.",
                "Patch the cause, not the symptom, and avoid broad rewrites.",
                "Rerun the failing command or the closest available validation.",
            ],
            done_criteria=[
                "The failure cause is named in the final answer.",
                "The validation result is included with command and outcome.",
            ],
            failure_modes=[
                "If reproduction is impossible, explain the missing dependency or environment blocker.",
                "If max steps are running out, preserve the exact next diagnostic action.",
            ],
        ),
        SkillSpec(
            name="test_failure_triage",
            version="1.0.0",
            description="Turn test output into a prioritized repair plan.",
            entrypoint="builtin:test_failure_triage",
            owner="agent-forge",
            permissions=["read:repo", "write:repo", "run:validation"],
            dependencies=["git:working-tree", "python:local-venv"],
            tags=["coding", "test", "diagnostics"],
            activation_terms=["test", "unittest", "pytest", "verify", "验证", "测试", "跑通"],
            tool_names=["run_command", "diagnostics", "grep", "grep_search", "read_file", "apply_patch", "git_diff"],
            operating_procedure=[
                "Run or inspect the user-provided test command.",
                "Group failures by root cause instead of editing every failing assertion separately.",
                "Patch one root cause at a time and rerun the targeted command.",
            ],
            done_criteria=[
                "Report pass/fail with the exact command.",
                "If still failing, list the next root cause with evidence.",
            ],
            failure_modes=[
                "If the test command is unsafe or unavailable, use diagnostics and explain the limitation.",
            ],
        ),
        SkillSpec(
            name="safe_refactor",
            version="1.0.0",
            description="Refactor code while preserving behavior and call-site compatibility.",
            entrypoint="builtin:safe_refactor",
            owner="agent-forge",
            permissions=["read:repo", "write:repo", "run:validation"],
            dependencies=["git:working-tree", "python:local-venv"],
            tags=["coding", "refactor", "safety"],
            activation_terms=["refactor", "cleanup", "readability", "可读性", "重构", "合并", "删除冗余"],
            tool_names=["git_status", "grep", "grep_search", "read_file", "apply_patch", "run_command", "git_diff"],
            operating_procedure=[
                "Identify call sites before changing a public function, class, or file layout.",
                "Keep public behavior stable unless the user explicitly asks to change it.",
                "Prefer moving or simplifying one responsibility at a time.",
                "Use git_diff to review whether the refactor stayed scoped.",
            ],
            done_criteria=[
                "Call-site impact is described.",
                "Validation covers the touched behavior or a blocker is stated.",
            ],
            failure_modes=[
                "If call sites are unclear, stop and report the risky unknown before deleting code.",
            ],
        ),
        SkillSpec(
            name="docs_update",
            version="1.0.0",
            description="Update documentation so the project remains learnable after code changes.",
            entrypoint="builtin:docs_update",
            owner="agent-forge",
            permissions=["read:repo", "write:docs"],
            dependencies=["git:working-tree"],
            tags=["documentation", "learning", "maintenance"],
            activation_terms=["docs", "readme", "document", "guide", "文档", "教程", "解释"],
            tool_names=["grep", "grep_search", "read_file", "apply_patch", "write_file", "git_diff"],
            operating_procedure=[
                "Locate the shortest existing doc that matches the user's learning path.",
                "Update docs to reflect actual code behavior, not aspirational design.",
                "Prefer linking to code paths and commands the user can run.",
            ],
            done_criteria=[
                "The updated doc has a clear reader path and no duplicate explanation.",
                "Commands and file paths match the current repo structure.",
            ],
            failure_modes=[
                "If docs conflict with code, trust code and mark the old doc as stale or update it.",
            ],
        ),
    ]


def build_default_skill_registry(manifest_paths: list[str] | None = None) -> SkillRegistry:

    registry = SkillRegistry()
    for spec in built_in_skill_specs():
        registry.register(spec)
    for path in manifest_paths or []:
        if Path(path).exists():
            registry.load_manifest(path)
        else:
            raise ValueError(f"skill manifest not found: {path}")
    return registry

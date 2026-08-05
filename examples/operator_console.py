#!/usr/bin/env python3
"""PyCharm 一键启动的复杂真实模型学习场景。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = PROJECT_ROOT / "examples" / "debug_lab" / "complex_repository"
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
DEFAULT_TASK = (
    "Repair the settlement reconciliation service in this repository. Provider retries "
    "must be idempotent even when provider and event identifiers differ by case or "
    "surrounding whitespace. Capture amounts must use the business currency rounding "
    "rule, partial captures must remain partial until the expected amount is reached, "
    "and every rejected capture must leave account state, the operation-key set, and "
    "the ledger unchanged so the same event can be retried with a corrected payload. "
    "Do not modify tests. First map the repository and run the focused "
    "tests/test_reconciliation.py tests. After the focused behavior passes, run the "
    "complete test suite, inspect the final git diff, and only then finish."
)
WORKBENCH_LAUNCHER = PROJECT_ROOT / "scripts" / "showcase_demo.sh"
sys.path.insert(0, str(PROJECT_ROOT))

from examples.debug_lab.support import (  # noqa: E402
    artifact_from_pointer,
    load_or_create_workspace,
    load_or_store_deepseek_key,
    publish_latest,
)


@dataclass(frozen=True, kw_only=True)
class PracticeProfile:
    """同一复杂任务的一种学习条件；不改变待修复仓库。"""

    key: str
    title: str
    purpose: str
    max_context_chars: int
    max_steps: int = 24
    auto_approve_writes: bool = False
    allow_human_question: bool = True
    operator_drill: tuple[str, ...] = ()


PRACTICE_PROFILES = (
    PracticeProfile(
        key="natural",
        title="自然修复",
        purpose="先不干预，完整观察模型怎样检索、修改、失败和收敛。",
        max_context_chars=16_000,
    ),
    PracticeProfile(
        key="context-pressure",
        title="上下文压力",
        purpose="降低上下文预算，观察压缩、文件选择、重复检索和信息丢失。",
        max_context_chars=6_500,
    ),
    PracticeProfile(
        key="operator-control",
        title="人工控制与恢复",
        purpose="亲自 steer、pause、approve 和 resume，掌握控制面的真实时序。",
        max_context_chars=16_000,
        operator_drill=(
            "运行中输入 steer：Prioritize failure atomicity before editing.",
            "模型调用期间按 F6；观察它在下一个安全边界暂停，而不是中断 HTTP。",
            "点击“继续”恢复；遇到写操作后检查目标与参数，再点击“批准”。",
        ),
    ),
    PracticeProfile(
        key="full-auto",
        title="全自动修复",
        purpose=(
            "隔离工作区内自动批准普通写操作，连续观察完整修复；"
            "越界路径、网络与危险命令仍被拒绝。"
        ),
        max_context_chars=16_000,
        max_steps=40,
        auto_approve_writes=True,
        allow_human_question=False,
    ),
)


def select_practice_profile() -> PracticeProfile:
    """从环境变量或运行前菜单选择练习；直接回车使用首次推荐模式。"""

    configured_key = os.environ.get("NANOHARNESS_PRACTICE_PROFILE", "").strip()
    if configured_key:
        for profile in PRACTICE_PROFILES:
            if profile.key == configured_key:
                return profile
        raise SystemExit(f"未知练习模式: {configured_key}")

    print("\nNanoHarness Lab 3 · 复杂任务深度练习")
    for index, profile in enumerate(PRACTICE_PROFILES, start=1):
        default_hint = "（默认，直接回车）" if index == 1 else ""
        print(f"  {index}. {profile.title}{default_hint}：{profile.purpose}")
    selected = input("请输入模式编号 1、2、3 或 4：").strip() or "1"
    try:
        return PRACTICE_PROFILES[int(selected) - 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit("请输入 1、2、3 或 4。") from exc


def print_operator_drill(profile: PracticeProfile) -> None:
    """在进入全屏 TUI 前说明本次要亲手完成的动作。"""

    print(f"\n本次模式：{profile.title}")
    print(f"学习目标：{profile.purpose}")
    if profile.operator_drill:
        print("必须亲手完成：")
        for index, instruction in enumerate(profile.operator_drill, start=1):
            print(f"  {index}. {instruction}")
    input("按 Enter 进入 Operator Console...")


def main() -> None:
    """准备多模块练习仓库和 API Key，再进入真实 TUI。"""

    from agent_forge.cli.dispatch import main as forge_main
    from agent_forge.observability.api import refresh_run_manifest

    os.chdir(PROJECT_ROOT)
    profile = select_practice_profile()
    print_operator_drill(profile)
    load_or_store_deepseek_key(KEYCHAIN_SERVICE)
    workspace = load_or_create_workspace(
        f"complex-{profile.key}",
        template_root=TEMPLATE_ROOT,
        state_root=STATE_ROOT,
    )
    write_approval_flag = (
        "--auto-approve-writes"
        if profile.auto_approve_writes
        else "--no-auto-approve-writes"
    )
    runtime_arguments = [
        "console",
        DEFAULT_TASK,
        "--workspace",
        str(workspace),
        "--output-root",
        str(RUNS_ROOT),
        "--provider",
        "deepseek",
        "--model",
        "deepseek-v4-pro",
        "--thinking",
        "enabled",
        "--reasoning-effort",
        "max",
        "--max-steps",
        str(profile.max_steps),
        "--max-context-chars",
        str(profile.max_context_chars),
        "--approval-mode",
        "on-write",
        write_approval_flag,
        "--tool-routing",
        "task-aware",
        "--skills",
        "auto",
        "--memory-root",
        str(PROJECT_ROOT / ".agent_forge" / "memory"),
        "--memory-recall-limit",
        "12",
    ]
    if not profile.allow_human_question:
        # 全自动模式仍使用同一 Runtime，只是不向模型暴露人工提问协议。
        # 普通写操作自动批准；路径、命令、网络和隔离边界继续生效。
        runtime_arguments.extend(
            [
                "--runtime-instructions",
                (
                    "Work autonomously until the requested focused and full validations pass. "
                    "Do not ask the operator to run tests or provide implementation decisions; "
                    "use the available repository and validation tools."
                ),
            ]
        )
        for tool_name in (
            "list_files",
            "read_file",
            "grep",
            "grep_search",
            "git_status",
            "git_diff",
            "replace_text",
            "write_file",
            "python_validation",
            "run_command",
        ):
            runtime_arguments.extend(["--tool", tool_name])
    forge_main(runtime_arguments)
    workspace_pointer = workspace / ".agent_forge" / "latest" / "run.txt"
    if workspace_pointer.is_file():
        artifact_dir = artifact_from_pointer(workspace_pointer)
        (artifact_dir / "practice_profile.json").write_text(
            json.dumps(
                {
                    "key": profile.key,
                    "title": profile.title,
                    "purpose": profile.purpose,
                    "max_context_chars": profile.max_context_chars,
                    "max_steps": profile.max_steps,
                    "auto_approve_writes": profile.auto_approve_writes,
                    "allow_human_question": profile.allow_human_question,
                    "operator_drill": list(profile.operator_drill),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        refresh_run_manifest(artifact_dir)
        publish_latest(
            artifact_dir,
            project_root=PROJECT_ROOT,
            state_root=STATE_ROOT,
            scenario="complex",
        )
        print(f"COMPLEX LAB ARTIFACT: {artifact_dir}")
        subprocess.run(
            [str(WORKBENCH_LAUNCHER), "--show-complex"],
            cwd=PROJECT_ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()

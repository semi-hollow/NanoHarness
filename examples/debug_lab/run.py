#!/usr/bin/env python3
"""固定输入、可重复运行的 NanoHarness 内部调试实验场。"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = Path(__file__).resolve().parent / "repository"
STATE_ROOT = PROJECT_ROOT / ".agent_forge" / "debug-lab"
RUNS_ROOT = PROJECT_ROOT / ".agent_forge" / "runs"
KEYCHAIN_SERVICE = "NanoHarness DeepSeek API"
ASTROPY_INSTANCE = "astropy__astropy-12907"
SWEBENCH_REPOSITORY = "https://github.com/SWE-bench/SWE-bench.git"
SWEBENCH_REVISION = "f7bbbb2ccdf479001d6467c9e34af59e44a840f9"
TASK = (
    "修复 calculator.py 的 add：2 + 3 必须等于 5。不要修改测试；"
    "修改后必须调用 diagnostics，kind=pytest，target=test_calculator.py。"
)
sys.path.insert(0, str(PROJECT_ROOT))


def _forge_main(argv: list[str]) -> None:
    from agent_forge.cli.dispatch import main as dispatch_main

    dispatch_main(argv)


def _run_git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _new_workspace(scenario: str) -> Path:
    """从同一只读模板创建新 base，保证每次实验输入完全一致。"""

    identity = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    workspace = STATE_ROOT / scenario / identity / "workspace"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE_ROOT, workspace)
    _run_git(workspace, "init", "-q")
    _run_git(workspace, "add", ".")
    _run_git(
        workspace,
        "-c",
        "user.name=NanoHarness Learner",
        "-c",
        "user.email=learner@local.invalid",
        "commit",
        "-q",
        "-m",
        "create fixed debug fixture",
    )
    state = STATE_ROOT / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{scenario}_workspace.txt").write_text(
        str(workspace.resolve()),
        encoding="utf-8",
    )
    return workspace


def _remember_artifact(scenario: str, artifact_dir: Path) -> None:
    state = STATE_ROOT / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / f"{scenario}_artifact.txt").write_text(
        str(artifact_dir.resolve()),
        encoding="utf-8",
    )


def _publish_latest(artifact_dir: Path, *, scenario: str = "") -> None:
    latest = PROJECT_ROOT / ".agent_forge" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "run.txt").write_text(
        str(artifact_dir.resolve()),
        encoding="utf-8",
    )
    # Workbench 会在 canonical runs 中按 mtime 选择；让刚发布的嵌套 continuation
    # artifact 胜过它的 showcase owner 目录。
    os.utime(artifact_dir, None)
    if scenario:
        _remember_artifact(scenario, artifact_dir)


def _artifact_from_pointer(pointer: Path) -> Path:
    target = Path(pointer.read_text(encoding="utf-8").strip())
    if not target.is_absolute():
        target = pointer.parent / target
    return target.resolve()


def _remember_root_pointer(scenario: str, pointer_name: str) -> None:
    pointer = PROJECT_ROOT / ".agent_forge" / "latest" / pointer_name
    _remember_artifact(scenario, _artifact_from_pointer(pointer))


def restore_evidence(scenario: str) -> None:
    state = STATE_ROOT / "state" / f"{scenario}_artifact.txt"
    if not state.is_file():
        raise SystemExit(f"没有已保存的 {scenario} Evidence；先运行对应 Debug Lab。")
    artifact = Path(state.read_text(encoding="utf-8").strip()).resolve()
    try:
        artifact.relative_to(RUNS_ROOT.resolve())
    except ValueError as exc:
        raise SystemExit(f"拒绝发布 runs 目录外的 Evidence: {artifact}") from exc
    if not artifact.is_dir():
        raise SystemExit(f"已保存的 Evidence 不存在: {artifact}")
    latest = PROJECT_ROOT / ".agent_forge" / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    pointer_name = "bench.txt" if scenario == "astropy" else "run.txt"
    (latest / pointer_name).write_text(str(artifact), encoding="utf-8")
    os.utime(artifact, None)
    print(f"RESTORED {scenario.upper()} EVIDENCE: {artifact}")


class DeterministicRepairModel:
    """只固定模型意图；Runtime、工具、pytest、状态和 Evidence 都是真实实现。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[object], tools: list[object]) -> object:
        from agent_forge.extensions import AgentResponse, ToolCall

        self.calls += 1
        calls = {
            1: ToolCall("lab-read-source", "read_file", {"path": "calculator.py"}),
            2: ToolCall(
                "lab-read-test",
                "read_file",
                {"path": "test_calculator.py"},
            ),
            3: ToolCall(
                "lab-apply-patch",
                "apply_patch",
                {
                    "path": "calculator.py",
                    "old": "return a - b",
                    "new": "return a + b",
                },
            ),
            4: ToolCall(
                "lab-pytest",
                "diagnostics",
                {"kind": "pytest", "target": "test_calculator.py"},
            ),
        }
        if self.calls in calls:
            return AgentResponse(None, [calls[self.calls]])
        return AgentResponse(
            "PASS\nfixed input -> governed patch -> focused pytest -> evidence",
            [],
        )


def _prompt_key_on_macos() -> str:
    """使用系统隐藏输入框，避免 Debug Console 回显凭据。"""

    script = (
        'display dialog "首次运行：请输入 DeepSeek API Key。它只会保存到 macOS '
        'Keychain。" default answer "" with hidden answer buttons {"取消", "保存"} '
        'default button "保存"\ntext returned of result'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit("未保存 DeepSeek API Key，Live Debug 已取消。")
    return result.stdout.strip()


def _load_or_store_deepseek_key() -> None:
    """从 macOS Keychain 加载；首次缺失时通过系统隐藏输入框保存。"""

    if os.environ.get("DEEPSEEK_API_KEY"):
        return
    if sys.platform != "darwin":
        raise SystemExit("Live/Astropy Debug 仅在 macOS 执行。")
    account = os.environ.get("USER") or getpass.getuser()
    result = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    key = result.stdout.strip()
    if not key:
        key = _prompt_key_on_macos()
        if not key:
            raise SystemExit("DeepSeek API Key 为空。")
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
                key,
            ],
            check=True,
            capture_output=True,
        )
    os.environ["DEEPSEEK_API_KEY"] = key


def _ensure_docker() -> None:
    if shutil.which("docker") is None:
        raise SystemExit("LAB 4 需要 Docker Desktop；安装一次后重新点击 Debug。")
    if subprocess.run(
        ["docker", "info"],
        check=False,
        capture_output=True,
    ).returncode == 0:
        return
    print("Docker 尚未启动，正在打开 Docker Desktop……")
    subprocess.run(["open", "-a", "Docker"], check=False, capture_output=True)
    for _ in range(30):
        time.sleep(2)
        if subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
        ).returncode == 0:
            return
    raise SystemExit("Docker Desktop 在 60 秒内未就绪；启动完成后重新点击 Debug。")


def _ensure_swebench() -> None:
    """按固定 revision 准备 official harness；该准备层不进入项目主流程。"""

    if importlib.util.find_spec("datasets") is None:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".[bench]"],
            cwd=PROJECT_ROOT,
            check=True,
        )
        importlib.invalidate_caches()
    tool_root = STATE_ROOT / "tools" / "SWE-bench"
    if not (tool_root / ".git").is_dir():
        tool_root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", SWEBENCH_REPOSITORY, str(tool_root)],
            check=True,
        )
    commit_exists = subprocess.run(
        ["git", "-C", str(tool_root), "cat-file", "-e", f"{SWEBENCH_REVISION}^{{commit}}"],
        check=False,
        capture_output=True,
    ).returncode == 0
    if not commit_exists:
        _run_git(tool_root, "fetch", "origin", SWEBENCH_REVISION)
    _run_git(tool_root, "checkout", "--detach", SWEBENCH_REVISION)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(tool_root)],
        check=True,
    )
    sys.path.insert(0, str(tool_root))
    current = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(tool_root) + (os.pathsep + current if current else "")
    importlib.invalidate_caches()


def run_control() -> None:
    from agent_forge.showcase import run_governed_demo

    print("LAB 1/4: approval -> checkpoint -> continuation")
    result = run_governed_demo("approval", output_root=RUNS_ROOT)
    _publish_latest(result.inspect_target, scenario="control")
    print(f"STATUS: {result.waiting_status} -> {result.completed_status}")
    print(f"ARTIFACT: {result.inspect_target}")


def run_fixed() -> None:
    from agent_forge import Harness, HarnessConfig

    workspace = _new_workspace("fixed")
    print(
        "LAB 2/4: read -> read -> patch -> pytest -> final\n"
        f"FIXED INPUT: {workspace}"
    )
    result = Harness(
        model=DeterministicRepairModel(),
        config=HarnessConfig(
            workspace=str(workspace),
            output_root=str(RUNS_ROOT),
            max_steps=6,
            approval_mode="on-write",
            auto_approve_writes=True,
            enabled_tools=("read_file", "apply_patch", "diagnostics"),
            tool_routing_mode="all",
            skill_mode="none",
            memory_recall_limit=0,
        ),
    ).run(TASK)
    _publish_latest(result.artifact_dir, scenario="fixed")
    print(f"STATUS: {result.status.value}\nARTIFACT: {result.artifact_dir}")


def run_live() -> None:
    _load_or_store_deepseek_key()
    workspace = _new_workspace("live")
    print(f"LAB 3/4: real DeepSeek, same input\nFIXED INPUT: {workspace}")
    _forge_main(
        [
            "run",
            TASK,
            "--workspace",
            str(workspace),
            "--output-root",
            str(RUNS_ROOT),
            "--provider",
            "deepseek",
            "--model",
            "deepseek-chat",
            "--max-steps",
            "8",
        ]
    )
    artifact = _artifact_from_pointer(workspace / ".agent_forge" / "latest" / "run.txt")
    _publish_latest(artifact, scenario="live")


def run_astropy() -> None:
    _load_or_store_deepseek_key()
    _ensure_docker()
    _ensure_swebench()
    print(f"LAB 4/4: {ASTROPY_INSTANCE} -> local evidence -> official oracle")
    _forge_main(
        [
            "bench",
            "swebench",
            "--instance-id",
            ASTROPY_INSTANCE,
            "--provider",
            "deepseek",
            "--model",
            "deepseek-chat",
            "--max-steps",
            "8",
            "--timeout-seconds",
            "900",
            "--evaluate",
            "--max-workers",
            "1",
            "--output-root",
            str(RUNS_ROOT),
        ]
    )
    _remember_root_pointer("astropy", "bench.txt")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario",
        choices=(
            "control",
            "fixed",
            "live",
            "astropy",
            "show-live",
            "show-astropy",
        ),
    )
    args = parser.parse_args()
    os.chdir(PROJECT_ROOT)
    {
        "control": run_control,
        "fixed": run_fixed,
        "live": run_live,
        "astropy": run_astropy,
        "show-live": lambda: restore_evidence("live"),
        "show-astropy": lambda: restore_evidence("astropy"),
    }[args.scenario]()


if __name__ == "__main__":
    main()

"""Debug Lab 的环境准备、固定模型和 Evidence 路径工具。

这些代码只服务本地教学实验，不属于 NanoHarness Runtime 主流程。
"""

from __future__ import annotations

import getpass
import importlib.util
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


class DeterministicRepairModel:
    """固定 read/read/patch/pytest 意图；Runtime 和工具仍使用真实实现。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: list[object], tools: list[object]) -> object:
        from agent_forge.extensions import AgentResponse, ToolCall

        self.calls += 1
        scripted_calls = {
            1: ToolCall(
                "lab-read-source",
                "read_file",
                {"path": "calculator.py"},
            ),
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
        if self.calls in scripted_calls:
            return AgentResponse(None, [scripted_calls[self.calls]])
        return AgentResponse(
            "PASS\nfixed input -> governed patch -> focused pytest -> evidence",
            [],
        )


def create_workspace(
    scenario: str,
    *,
    template_root: Path,
    state_root: Path,
) -> Path:
    """从同一模板创建带初始 commit 的隔离实验仓库。"""

    workspace_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    workspace = state_root / scenario / workspace_id / "workspace"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_root, workspace)
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
    state_dir = state_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{scenario}_workspace.txt").write_text(
        str(workspace.resolve()),
        encoding="utf-8",
    )
    return workspace


def publish_latest(
    artifact_dir: Path,
    *,
    project_root: Path,
    state_root: Path,
    scenario: str = "",
) -> None:
    """发布 Workbench latest 指针，并按场景保存可恢复 Evidence。"""

    latest_dir = project_root / ".agent_forge" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "run.txt").write_text(
        str(artifact_dir.resolve()),
        encoding="utf-8",
    )
    os.utime(artifact_dir, None)
    if scenario:
        _remember_artifact(scenario, artifact_dir, state_root=state_root)


def artifact_from_pointer(pointer: Path) -> Path:
    """把相对或绝对 latest 指针统一解析成 artifact 目录。"""

    target = Path(pointer.read_text(encoding="utf-8").strip())
    if not target.is_absolute():
        target = pointer.parent / target
    return target.resolve()


def remember_root_pointer(
    scenario: str,
    pointer_name: str,
    *,
    project_root: Path,
    state_root: Path,
) -> None:
    """保存根级 run/bench 指针指向的 Evidence。"""

    pointer = project_root / ".agent_forge" / "latest" / pointer_name
    _remember_artifact(
        scenario,
        artifact_from_pointer(pointer),
        state_root=state_root,
    )


def restore_evidence(
    scenario: str,
    *,
    project_root: Path,
    state_root: Path,
    runs_root: Path,
) -> None:
    """恢复已保存场景的 latest 指针，不重新执行模型或评测。"""

    saved_pointer = state_root / "state" / f"{scenario}_artifact.txt"
    if not saved_pointer.is_file():
        raise SystemExit(f"没有已保存的 {scenario} Evidence；先运行对应 Debug Lab。")
    artifact_dir = Path(saved_pointer.read_text(encoding="utf-8").strip()).resolve()
    try:
        artifact_dir.relative_to(runs_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"拒绝发布 runs 目录外的 Evidence: {artifact_dir}") from exc
    if not artifact_dir.is_dir():
        raise SystemExit(f"已保存的 Evidence 不存在: {artifact_dir}")

    latest_dir = project_root / ".agent_forge" / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    pointer_name = "bench.txt" if scenario == "astropy" else "run.txt"
    (latest_dir / pointer_name).write_text(
        str(artifact_dir),
        encoding="utf-8",
    )
    os.utime(artifact_dir, None)
    print(f"RESTORED {scenario.upper()} EVIDENCE: {artifact_dir}")


def load_or_store_deepseek_key(keychain_service: str) -> None:
    """从 Keychain 加载 API Key；首次缺失时使用隐藏输入框保存。"""

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
            keychain_service,
            "-w",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    api_key = result.stdout.strip()
    if not api_key:
        api_key = _prompt_key_on_macos()
        if not api_key:
            raise SystemExit("DeepSeek API Key 为空。")
        subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-a",
                account,
                "-s",
                keychain_service,
                "-w",
                api_key,
            ],
            check=True,
            capture_output=True,
        )
    os.environ["DEEPSEEK_API_KEY"] = api_key


def ensure_docker() -> None:
    """确保任意 Docker-compatible daemon 可用。"""

    if shutil.which("docker") is None:
        raise SystemExit(
            "LAB 4 需要 Docker-compatible runtime；请安装 Docker Desktop 或 Colima。"
        )
    if (
        subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    ):
        return

    docker_app = next(
        (
            path
            for path in (
                Path("/Applications/Docker.app"),
                Path.home() / "Applications" / "Docker.app",
            )
            if path.exists()
        ),
        None,
    )
    if docker_app is None:
        raise SystemExit(
            "Docker daemon 未就绪；请启动 Docker Desktop，或先执行 `colima start`。"
        )

    print("Docker daemon 尚未启动，正在打开 Docker Desktop……")
    subprocess.run(["open", str(docker_app)], check=False, capture_output=True)
    for _ in range(30):
        time.sleep(2)
        if (
            subprocess.run(
                ["docker", "info"],
                check=False,
                capture_output=True,
            ).returncode
            == 0
        ):
            return
    raise SystemExit("Docker daemon 在 60 秒内未就绪；启动完成后重新点击 Debug。")


def ensure_swebench(
    *,
    project_root: Path,
    state_root: Path,
    repository: str,
    revision: str,
) -> None:
    """按固定 revision 准备 official harness。"""

    if importlib.util.find_spec("datasets") is None:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".[bench]"],
            cwd=project_root,
            check=True,
        )
        importlib.invalidate_caches()
    tool_root = state_root / "tools" / "SWE-bench"
    if not (tool_root / ".git").is_dir():
        tool_root.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", repository, str(tool_root)],
            check=True,
        )
    commit_exists = (
        subprocess.run(
            [
                "git",
                "-C",
                str(tool_root),
                "cat-file",
                "-e",
                f"{revision}^{{commit}}",
            ],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )
    if not commit_exists:
        _run_git(tool_root, "fetch", "origin", revision)
    _run_git(tool_root, "checkout", "--detach", revision)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(tool_root)],
        check=True,
    )
    sys.path.insert(0, str(tool_root))
    current_python_path = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(tool_root) + (
        os.pathsep + current_python_path if current_python_path else ""
    )
    importlib.invalidate_caches()


def _remember_artifact(
    scenario: str,
    artifact_dir: Path,
    *,
    state_root: Path,
) -> None:
    state_dir = state_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{scenario}_artifact.txt").write_text(
        str(artifact_dir.resolve()),
        encoding="utf-8",
    )


def _prompt_key_on_macos() -> str:
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


def _run_git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=True,
        capture_output=True,
        text=True,
    )

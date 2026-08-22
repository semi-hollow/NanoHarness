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

from agent_forge.contracts import ToolSchema
from agent_forge.runtime.domain.conversation import AgentResponse, Message, ToolCall
from agent_forge.infrastructure.storage_layout import INDEX_ROOT


class DeterministicRepairModel:
    """固定 read/read/replace/pytest 意图；Runtime 和工具仍使用真实实现。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
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
                "lab-replace-text",
                "replace_text",
                {
                    "path": "calculator.py",
                    "old": "return a - b",
                    "new": "return a + b",
                },
            ),
            4: ToolCall(
                "lab-pytest",
                "python_validation",
                {
                    "check_type": "pytest",
                    "validation_target": "test_calculator.py",
                },
            ),
        }
        if self.calls in scripted_calls:
            return AgentResponse(None, [scripted_calls[self.calls]])
        return AgentResponse(
            "PASS\nfixed input -> governed edit -> focused pytest -> evidence",
            [],
        )


class DeterministicFanoutModel:
    """固定并行修复、依赖验证和 finalizer 意图；编排与执行均为真实实现。"""

    last_usage = None

    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
    ) -> AgentResponse:
        self.calls += 1
        prompt = "\n".join(
            str(getattr(message, "content", "") or "") for message in messages
        )
        if "FanoutFinalizer" in prompt:
            if self.calls == 1:
                return AgentResponse(
                    None,
                    [
                        ToolCall(
                            "lab-finalizer-diff",
                            "git_diff",
                            {},
                        )
                    ],
                )
            if self.calls == 2:
                return AgentResponse(
                    None,
                    [
                        ToolCall(
                            "lab-finalizer-pytest",
                            "python_validation",
                            {
                                "check_type": "pytest",
                                "validation_target": "test_checkout.py",
                            },
                        )
                    ],
                )
            return AgentResponse(
                "PASS\nintegrated pricing and shipping policies satisfy the edge-case suite",
                [],
            )

        task_id = (
            "pricing-policy"
            if "task_id=pricing-policy" in prompt
            else "shipping-policy"
            if "task_id=shipping-policy" in prompt
            else "edge-case-verifier"
        )
        if task_id == "edge-case-verifier":
            if self.calls == 1:
                return AgentResponse(
                    None,
                    [
                        ToolCall(
                            "lab-edge-case-pytest",
                            "python_validation",
                            {
                                "check_type": "pytest",
                                "validation_target": "test_checkout.py",
                            },
                        )
                    ],
                )
            return AgentResponse(
                "completed edge-case-verifier: invalid pricing, expedited shipping, "
                "and unknown-region cases passed",
                [],
            )
        if self.calls == 1:
            target = "pricing.py" if task_id == "pricing-policy" else "shipping.py"
            return AgentResponse(
                None,
                [
                    ToolCall(
                        f"lab-read-{task_id}",
                        "read_file",
                        {"path": target},
                    )
                ],
            )
        if self.calls == 2 and task_id == "pricing-policy":
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "lab-patch-pricing-policy",
                        "replace_text",
                        {
                            "path": "pricing.py",
                            "old": (
                                "def final_price(subtotal: int, discount: int) -> int:\n"
                                '    """Return the payable subtotal after validating abnormal inputs."""\n\n'
                                "    return subtotal\n"
                            ),
                            "new": (
                                "def final_price(subtotal: int, discount: int) -> int:\n"
                                '    """Return the payable subtotal after validating abnormal inputs."""\n\n'
                                "    if subtotal < 0:\n"
                                '        raise ValueError("subtotal must be non-negative")\n'
                                "    if discount < 0 or discount > subtotal:\n"
                                '        raise ValueError("discount must be within subtotal")\n'
                                "    return subtotal - discount\n"
                            ),
                        },
                    )
                ],
            )
        if self.calls == 2:
            return AgentResponse(
                None,
                [
                    ToolCall(
                        "lab-patch-shipping-policy",
                        "replace_text",
                        {
                            "path": "shipping.py",
                            "old": (
                                "def shipping_fee(region: str, subtotal: int, *, expedited: bool = False) -> int:\n"
                                '    """Return a route-aware fee without silently accepting unknown regions."""\n\n'
                                "    return 0\n"
                            ),
                            "new": (
                                "def shipping_fee(region: str, subtotal: int, *, expedited: bool = False) -> int:\n"
                                '    """Return a route-aware fee without silently accepting unknown regions."""\n\n'
                                "    if subtotal < 0:\n"
                                '        raise ValueError("subtotal must be non-negative")\n'
                                '    if region not in {"domestic", "international"}:\n'
                                '        raise ValueError(f"unsupported region: {region}")\n'
                                "    if expedited:\n"
                                '        return 15 if region == "domestic" else 30\n'
                                '    if region == "domestic":\n'
                                "        return 0 if subtotal >= 100 else 5\n"
                                "    return 20\n"
                            ),
                        },
                    )
                ],
            )
        return AgentResponse(f"completed {task_id}", [])


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


def load_or_create_workspace(
    scenario: str,
    *,
    template_root: Path,
    state_root: Path,
) -> Path:
    """优先复用该场景最近的 Workspace，避免每次打开 Console 都制造新目录。

    这是操作体验策略，不改变 Run 的不可变语义。需要全新现场时仍可显式调用
    ``create_workspace``；普通重新打开 Lab 则回到上次会话所在代码现场。
    """

    pointer = state_root / "state" / f"{scenario}_workspace.txt"
    if pointer.is_file():
        workspace = Path(pointer.read_text(encoding="utf-8").strip())
        if workspace.is_dir():
            return workspace.resolve()
    return create_workspace(
        scenario,
        template_root=template_root,
        state_root=state_root,
    )


def publish_latest(
    artifact_dir: Path,
    *,
    project_root: Path,
    state_root: Path,
    scenario: str = "",
) -> None:
    """发布 Workbench latest 指针，并按场景保存可恢复 Evidence。"""

    latest_dir = project_root / INDEX_ROOT
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "run.txt").write_text(
        str(artifact_dir.resolve()),
        encoding="utf-8",
    )
    os.utime(artifact_dir, None)
    if scenario:
        _remember_artifact(scenario, artifact_dir, state_root=state_root)
        source_key = {
            "control": "governed",
            "coordinated": "orchestration",
            "complex": "complex",
        }.get(scenario)
        if source_key:
            (state_root / "state" / "workbench_source.txt").write_text(
                source_key,
                encoding="utf-8",
            )


def artifact_from_pointer(pointer: Path) -> Path:
    """把相对或绝对 latest 指针统一解析成 artifact 目录。"""

    target = Path(pointer.read_text(encoding="utf-8").strip())
    if not target.is_absolute():
        target = pointer.parent / target
    return target.resolve()


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

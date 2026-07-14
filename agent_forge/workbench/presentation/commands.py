from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from typing import Any

from agent_forge.workbench.domain.models import WorkbenchCommand
from agent_forge.workbench.ports import EvidenceCatalogPort
from agent_forge.workbench.wiring import build_evidence_catalog

# 主要入口：下方定义承接该模块的核心调用。
def build_workbench_command(
    action: str,
    payload: dict[str, Any],
    *,
    project_dir: Path | None = None,
    evidence: EvidenceCatalogPort | None = None,
) -> WorkbenchCommand:
    """把 UI 动作转换为经过白名单约束的 CLI 命令。"""

    python = sys.executable
    if action == "doctor":
        return WorkbenchCommand("Doctor", [python, "-m", "agent_forge", "doctor"])
    if action == "verify":
        return WorkbenchCommand("Verify", ["bash", "scripts/verify.sh"])
    if action == "agent_run":
        return build_agent_run_command(python, payload)
    if action == "swebench_sample":
        return build_swebench_command(python, payload, regression=False)
    if action == "swebench_regression":
        return build_swebench_command(python, payload, regression=True)
    if action == "report":
        return WorkbenchCommand(
            "Latest Report",
            [python, "-m", "agent_forge", "report", "latest"],
        )
    if action == "replay":
        return WorkbenchCommand(
            "Latest Replay",
            [python, "-m", "agent_forge", "replay", "latest"],
        )
    if action == "feedback":
        catalog = _catalog(project_dir, "feedback", evidence)
        trace_path = catalog.latest_trace_path()
        if trace_path is None:
            raise ValueError("no trace artifact is available for feedback")
        outcome = payload_choice(
            payload,
            "feedbackOutcome",
            {"accepted", "needs_work", "rejected"},
            "needs_work",
        )
        command = [
            python,
            "-m",
            "agent_forge",
            "eval",
            "feedback",
            str(trace_path),
            "--outcome",
            outcome,
            "--reviewer",
            "workbench",
        ]
        for label in payload_csv(payload, "feedbackLabels"):
            command.extend(["--label", label])
        append_optional(command, "--note", payload_text(payload, "feedbackNote", ""))
        return WorkbenchCommand("Record Human Feedback", command, command[:])
    if action == "export_dataset":
        catalog = _catalog(project_dir, "dataset export", evidence)
        run_dir = catalog.latest_run_dir()
        if run_dir is None:
            raise ValueError("no run artifact is available for dataset export")
        command = [
            python,
            "-m",
            "agent_forge",
            "eval",
            "export-dataset",
            str(run_dir),
            "--output",
            ".agent_forge/evaluation/evidence_dataset.jsonl",
        ]
        if payload_bool(payload, "requireFeedback", True):
            command.append("--require-feedback")
        return WorkbenchCommand("Export Evidence Dataset", command, command[:])
    raise ValueError(f"unsupported action: {action}")


def build_agent_run_command(
    python: str,
    payload: dict[str, Any],
) -> WorkbenchCommand:
    task = payload_text(
        payload,
        "task",
        "检查当前仓库结构，找出一个小而安全的改进点，完成修改并保留 trace 和 usage 证据。",
    )
    if len(task) < 6:
        raise ValueError("Task is too short. Describe what you want the agent to do.")
    provider = payload_choice(
        payload,
        "provider",
        {"deepseek", "openai", "openai-compatible"},
        "deepseek",
    )
    command = [python, "-m", "agent_forge", "run", task]
    command.extend(["--workspace", payload_text(payload, "workspace", ".")])
    command.extend(["--provider", provider])
    append_optional(command, "--model", payload_text(payload, "model", ""))
    append_optional(command, "--base-url", payload_text(payload, "baseUrl", ""))
    command.extend(["--max-steps", str(payload_int(payload, "maxSteps", 16, 1, 80))])
    command.extend(
        [
            "--max-context-chars",
            str(payload_int(payload, "maxContextChars", 12000, 1000, 120000)),
        ]
    )
    command.extend(
        [
            "--approval-mode",
            payload_choice(
                payload,
                "approvalMode",
                {"trusted", "on-write", "on-risk", "locked", "dry-run"},
                "trusted",
            ),
        ]
    )
    execution_mode = payload_choice(
        payload,
        "executionMode",
        {"local", "worktree", "container"},
        "worktree",
    )
    network_policy = payload_choice(
        payload,
        "networkPolicy",
        {"deny", "allow"},
        "deny",
    )
    tool_routing = payload_choice(
        payload,
        "toolRouting",
        {"task-aware", "all"},
        "task-aware",
    )
    if not payload_bool(payload, "autoApproveWrites", False):
        command.append("--no-auto-approve-writes")
    command.extend(["--network-policy", network_policy, "--tool-routing", tool_routing])
    command.extend(
        ["--output-root", payload_text(payload, "outputRoot", ".agent_forge/runs")]
    )
    agent_mode = payload_choice(
        payload,
        "runAgentMode",
        {"single", "multi", "fanout"},
        "single",
    )
    command.extend(["--agent-mode", agent_mode])
    if agent_mode == "multi":
        command.extend(["--profile", "coding_fix", "--max-revision-rounds", "2"])
    elif agent_mode == "fanout":
        command.extend(
            [
                "--fanout-plan",
                payload_project_path(
                    payload,
                    "fanoutPlan",
                    "examples/fanout-plan.sample.json",
                ),
            ]
        )
        resume_path = payload_project_path(
            payload,
            "fanoutResume",
            "",
            required=False,
        )
        if resume_path:
            command.extend(["--fanout-resume", resume_path])
        command.extend(
            [
                "--max-workers",
                str(payload_int(payload, "fanoutMaxWorkers", 4, 1, 8)),
                "--execution-mode",
                "worktree",
                "--no-keep-worktree",
            ]
        )
    else:
        command.extend(["--execution-mode", execution_mode])
        command.append(
            "--keep-worktree"
            if payload_bool(payload, "keepWorktree", False)
            else "--no-keep-worktree"
        )

    skills = payload_text(payload, "skills", "auto")
    if skills:
        command.extend(["--skills", skills])
    for manifest in payload_csv(payload, "skillManifests"):
        command.extend(["--skill-manifest", manifest])
    mcp_config = payload_text(payload, "mcpConfig", "")
    if mcp_config:
        command.extend(["--mcp-config", mcp_config])
    for tool_name in payload_csv(payload, "mcpTools"):
        command.extend(["--mcp-tool", tool_name])

    return WorkbenchCommand(
        title=f"Agent Run · {provider}",
        command=command,
        display_command=command[:],
        env=api_key_env(payload, provider),
    )


def build_swebench_command(
    python: str,
    payload: dict[str, Any],
    *,
    regression: bool,
) -> WorkbenchCommand:
    provider = payload_choice(
        payload,
        "provider",
        {"deepseek", "openai", "openai-compatible"},
        "deepseek",
    )
    command = [python, "-m", "agent_forge", "bench", "swebench"]
    if regression:
        command.extend(["--regression-set", "core"])
    else:
        command.extend(
            ["--showcase", "--limit", str(payload_int(payload, "limit", 1, 1, 20))]
        )
    command.extend(["--provider", provider])
    append_optional(command, "--model", payload_text(payload, "model", ""))
    append_optional(command, "--base-url", payload_text(payload, "baseUrl", ""))
    command.extend(["--max-steps", str(payload_int(payload, "maxSteps", 40, 1, 80))])
    command.extend(
        [
            "--max-context-chars",
            str(payload_int(payload, "maxContextChars", 18000, 1000, 120000)),
        ]
    )
    command.extend(
        ["--output-root", payload_text(payload, "outputRoot", ".agent_forge/runs")]
    )
    command.extend(
        [
            "--execution-mode",
            payload_choice(
                payload,
                "executionMode",
                {"local", "worktree", "container"},
                "worktree",
            ),
            "--network-policy",
            payload_choice(payload, "networkPolicy", {"deny", "allow"}, "deny"),
            "--tool-routing",
            payload_choice(
                payload,
                "toolRouting",
                {"task-aware", "all"},
                "task-aware",
            ),
        ]
    )
    command.append(
        "--keep-worktree"
        if payload_bool(payload, "keepWorktree", False)
        else "--no-keep-worktree"
    )
    agent_mode = payload_choice(
        payload,
        "benchAgentMode",
        {"single", "multi", "compare"},
        "compare",
    )
    command.extend(
        [
            "--agent-mode",
            agent_mode,
            "--profile",
            "coding_fix",
            "--max-revision-rounds",
            "2",
        ]
    )
    if payload_bool(payload, "directBaseline", True):
        command.append("--direct-baseline")
    if payload_bool(payload, "officialEvaluate", False):
        command.append("--evaluate")
        command.extend(
            ["--max-workers", str(payload_int(payload, "maxWorkers", 1, 1, 8))]
        )
    title = "SWE-bench Regression Set" if regression else "SWE-bench Reference Case"
    return WorkbenchCommand(
        title=f"{title} · {provider}",
        command=command,
        display_command=command[:],
        env=api_key_env(payload, provider),
    )


def payload_text(payload: dict[str, Any], key: str, default: str) -> str:
    value = payload.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def payload_project_path(
    payload: dict[str, Any],
    key: str,
    default: str,
    *,
    required: bool = True,
) -> str:
    text = str(payload.get(key) or default).strip().replace("\\", "/")
    if not text and not required:
        return ""
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or text.startswith("~")
        or (path.parts and ":" in path.parts[0])
    ):
        raise ValueError(f"{key} must be a relative project path")
    return path.as_posix()


def payload_int(
    payload: dict[str, Any],
    key: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    try:
        value = int(payload.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(value, max_value))


def payload_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def payload_choice(
    payload: dict[str, Any],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    value = payload_text(payload, key, default)
    if value not in allowed:
        raise ValueError(f"Unsupported {key}: {value}")
    return value


def payload_csv(payload: dict[str, Any], key: str) -> list[str]:
    raw = str(payload.get(key) or "")
    return [
        piece.strip()
        for piece in raw.replace("\n", ",").split(",")
        if piece.strip()
    ]


def append_optional(command: list[str], flag: str, value: str) -> None:
    if value:
        command.extend([flag, value])


def api_key_env(payload: dict[str, Any], provider: str) -> dict[str, str]:
    api_key = str(payload.get("apiKey") or "").strip()
    if not api_key:
        return {}
    env = {"AGENT_FORGE_API_KEY": api_key}
    if provider == "deepseek":
        env["DEEPSEEK_API_KEY"] = api_key
    elif provider == "openai":
        env["OPENAI_API_KEY"] = api_key
    return env


def _catalog(
    project_dir: Path | None,
    operation: str,
    evidence: EvidenceCatalogPort | None = None,
) -> EvidenceCatalogPort:
    if evidence is not None:
        return evidence
    if project_dir is None:
        raise ValueError(f"project directory is required for {operation}")
    return build_evidence_catalog(project_dir)

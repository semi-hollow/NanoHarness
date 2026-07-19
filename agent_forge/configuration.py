"""``forge run --config`` 的版本化、受控配置边界。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from agent_forge.contracts import JsonObject


CONFIG_SCHEMA_VERSION = 1
_SECRET_KEYS = {"api_key", "password", "secret", "access_token", "auth_token"}


@dataclass(frozen=True)
class RunConfigDocument:
    """已经验证并转换成 CLI 目标字段的配置文档。"""

    source: Path
    source_sha256: str
    values: dict[str, object]


@dataclass(frozen=True)
class _FieldSpec:
    target: str
    kind: str
    choices: frozenset[object] = frozenset()


_SECTIONS: dict[str, dict[str, _FieldSpec]] = {
    "run": {
        "task": _FieldSpec("task", "str"),
        "workspace": _FieldSpec("workspace", "str"),
        "output_root": _FieldSpec("output_root", "str"),
        "agent_mode": _FieldSpec(
            "agent_mode",
            "str",
            frozenset({"single", "multi", "fanout"}),
        ),
    },
    "model": {
        "provider": _FieldSpec("provider", "str"),
        "base_url": _FieldSpec("base_url", "str"),
        "model": _FieldSpec("model", "str"),
        "temperature": _FieldSpec("temperature", "float"),
        "context_window": _FieldSpec("model_context_window", "int"),
        "native_tool_calling": _FieldSpec("native_tool_calling", "bool"),
        "parallel_tool_calls": _FieldSpec("parallel_tool_calls", "bool"),
        "structured_output": _FieldSpec("structured_output", "bool"),
        "reasoning_tokens": _FieldSpec("reasoning_tokens", "bool"),
        "prompt_cache": _FieldSpec("prompt_cache", "bool"),
        "supports_images": _FieldSpec("supports_images", "bool"),
    },
    "runtime": {
        "max_steps": _FieldSpec("max_steps", "int"),
        "max_context_chars": _FieldSpec("max_context_chars", "int"),
        "max_prompt_tokens": _FieldSpec("max_prompt_tokens", "int"),
        "reserved_output_tokens": _FieldSpec("reserved_output_tokens", "int"),
        "max_tool_calls_per_turn": _FieldSpec("max_tool_calls_per_turn", "int"),
        "timeout_seconds": _FieldSpec("timeout_seconds", "float"),
        "cost_budget_usd": _FieldSpec("cost_budget_usd", "float"),
    },
    "execution": {
        "mode": _FieldSpec(
            "execution_mode",
            "str",
            frozenset({"local", "worktree", "container"}),
        ),
        "network_policy": _FieldSpec(
            "network_policy",
            "str",
            frozenset({"deny", "allow"}),
        ),
        "keep_worktree": _FieldSpec("keep_worktree", "bool"),
        "container_runtime": _FieldSpec("container_runtime", "str"),
        "container_image": _FieldSpec("container_image", "str"),
        "container_cpus": _FieldSpec("container_cpus", "float"),
        "container_memory": _FieldSpec("container_memory", "str"),
        "container_pids_limit": _FieldSpec("container_pids_limit", "int"),
        "container_read_only": _FieldSpec("container_read_only", "bool"),
    },
    "policy": {
        "approval_mode": _FieldSpec(
            "approval_mode",
            "str",
            frozenset({"trusted", "on-write", "on-risk", "locked", "dry-run"}),
        ),
        "auto_approve_writes": _FieldSpec("auto_approve_writes", "bool"),
    },
    "state": {
        "resume_state": _FieldSpec("resume_state", "str"),
        "approval_root": _FieldSpec("approval_root", "str"),
        "human_input_root": _FieldSpec("human_input_root", "str"),
        "operation_ledger_root": _FieldSpec("operation_ledger_root", "str"),
        "memory_root": _FieldSpec("memory_root", "str"),
        "memory_recall_limit": _FieldSpec("memory_recall_limit", "int"),
    },
    "tools": {
        "routing": _FieldSpec(
            "tool_routing",
            "str",
            frozenset({"task-aware", "all"}),
        ),
        "enabled": _FieldSpec("enabled_tools", "str_list"),
        "mcp_config": _FieldSpec("mcp_config", "str"),
        "mcp_allowed": _FieldSpec("mcp_tool", "str_list"),
    },
    "skills": {
        "selection": _FieldSpec("skills", "selection"),
        "manifests": _FieldSpec("skill_manifest", "str_list"),
    },
    "instructions": {
        "target": _FieldSpec("instruction_target", "str"),
        "global_files": _FieldSpec("global_instruction_file", "str_list"),
        "runtime_override": _FieldSpec("runtime_instructions", "str"),
        "max_bytes": _FieldSpec("instruction_max_bytes", "int"),
    },
    "multi_agent": {
        "profile": _FieldSpec("profile", "str"),
        "max_revision_rounds": _FieldSpec("max_revision_rounds", "int"),
        "fanout_plan": _FieldSpec("fanout_plan", "str"),
        "fanout_resume": _FieldSpec("fanout_resume", "str"),
        "max_workers": _FieldSpec("max_workers", "int"),
    },
}


_RUN_DEFAULTS: dict[str, object] = {
    "workspace": ".",
    "provider": "deepseek",
    "model": None,
    "base_url": None,
    "api_key": None,
    "temperature": 0.0,
    "max_steps": 16,
    "max_context_chars": 12_000,
    "max_prompt_tokens": 32_768,
    "reserved_output_tokens": 4_096,
    "model_context_window": 32_768,
    "native_tool_calling": True,
    "parallel_tool_calls": True,
    "structured_output": False,
    "reasoning_tokens": False,
    "prompt_cache": False,
    "supports_images": False,
    "approval_mode": "trusted",
    "auto_approve_writes": True,
    "approval_root": ".agent_forge/approvals",
    "human_input_root": ".agent_forge/human_input",
    "operation_ledger_root": ".agent_forge/operation_ledger",
    "memory_root": ".agent_forge/memory",
    "memory_recall_limit": 6,
    "max_tool_calls_per_turn": 4,
    "cost_budget_usd": None,
    "timeout_seconds": 900.0,
    "resume_state": "",
    "output_root": ".agent_forge/runs",
    "agent_mode": "single",
    "profile": "coding_fix",
    "max_revision_rounds": 2,
    "fanout_plan": "",
    "fanout_resume": "",
    "max_workers": 4,
    "skills": "auto",
    "skill_manifest": [],
    "mcp_config": None,
    "mcp_tool": [],
    "enabled_tools": None,
    "tool_routing": "task-aware",
    "execution_mode": "local",
    "network_policy": "deny",
    "keep_worktree": True,
    "container_runtime": "docker",
    "container_image": "python:3.11-slim",
    "container_cpus": 1.0,
    "container_memory": "1g",
    "container_pids_limit": 256,
    "container_read_only": True,
    "instruction_target": "",
    "global_instruction_file": [],
    "runtime_instructions": "",
    "instruction_max_bytes": 2_600,
}


# 主要入口：解析 YAML/JSON，拒绝未知字段、密钥和不支持的 schema。
def load_run_config(path: str | Path) -> RunConfigDocument:
    """返回只包含已知 argparse 目标字段的配置文档。"""

    source = Path(path)
    raw_text = source.read_text(encoding="utf-8")
    suffix = source.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            loaded = yaml.safe_load(raw_text)
        elif suffix == ".json":
            loaded = json.loads(raw_text)
        else:
            raise ValueError("run config must use .yaml, .yml, or .json")
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid run config syntax: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("run config root must be an object")
    _reject_secret_keys(loaded)
    version = loaded.get("schema_version")
    if version != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported run config schema_version: {version!r}; expected 1"
        )

    unknown_root = set(loaded) - {"schema_version", *_SECTIONS}
    if unknown_root:
        names = ", ".join(sorted(str(name) for name in unknown_root))
        raise ValueError(f"unknown run config sections: {names}")

    values: dict[str, object] = {}
    for section_name, fields in _SECTIONS.items():
        section = loaded.get(section_name, {})
        if not isinstance(section, dict):
            raise ValueError(f"run config section '{section_name}' must be an object")
        unknown_fields = set(section) - set(fields)
        if unknown_fields:
            names = ", ".join(sorted(str(name) for name in unknown_fields))
            raise ValueError(f"unknown fields in '{section_name}': {names}")
        for field_name, value in section.items():
            spec = fields[field_name]
            values[spec.target] = _normalize_value(
                value,
                spec,
                location=f"{section_name}.{field_name}",
            )
    return RunConfigDocument(
        source=source.resolve(),
        source_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        values=values,
    )


# 主要入口：执行 CLI > 模型环境变量 > config > defaults 的确定性合并。
def resolve_run_arguments(args: argparse.Namespace) -> RunConfigDocument | None:
    """就地补全 ``forge run`` Namespace，并返回可审计的源配置。"""

    document = load_run_config(args.config) if getattr(args, "config", None) else None
    _apply_model_environment(args)
    if document is not None:
        for target, value in document.values.items():
            if getattr(args, target, None) is None:
                setattr(args, target, value)
    for target, default in _RUN_DEFAULTS.items():
        if getattr(args, target, None) is None:
            setattr(args, target, list(default) if isinstance(default, list) else default)

    _validate_run_arguments(args)
    task = getattr(args, "task", None)
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required as a positional argument or run.task in config")
    return document


def resolved_run_config(
    args: argparse.Namespace,
    document: RunConfigDocument | None,
) -> JsonObject:
    """构造不含凭据、可随 run 发布的最终配置快照。"""

    values: JsonObject = {}
    for target in _RUN_DEFAULTS:
        if target in {"api_key", "runtime_instructions"}:
            continue
        value = getattr(args, target, None)
        if isinstance(value, (str, int, float, bool)) or value is None:
            values[target] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            values[target] = list(value)
    values["task"] = str(args.task)
    runtime_instructions = str(getattr(args, "runtime_instructions", "") or "")
    values["runtime_instructions_configured"] = bool(runtime_instructions)
    values["runtime_instructions_sha256"] = (
        hashlib.sha256(runtime_instructions.encode("utf-8")).hexdigest()
        if runtime_instructions
        else ""
    )
    source: JsonObject = {
        "path": str(document.source) if document else "",
        "sha256": document.source_sha256 if document else "",
    }
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "precedence": "cli > model_environment > config > defaults",
        "source": source,
        "api_key_configured": bool(
            getattr(args, "api_key", None)
            or os.getenv("AGENT_FORGE_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        ),
        "values": values,
    }


def _apply_model_environment(args: argparse.Namespace) -> None:
    environment_values: dict[str, str] = {}
    provider = os.getenv("AGENT_FORGE_DEFAULT_LLM")
    if provider:
        environment_values["provider"] = provider
    base_url = _first_environment(
        "AGENT_FORGE_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "OPENAI_BASE_URL",
    )
    if base_url:
        environment_values["base_url"] = base_url
    model = _first_environment(
        "AGENT_FORGE_MODEL",
        "DEEPSEEK_MODEL",
        "OPENAI_MODEL",
    )
    if model:
        environment_values["model"] = model
    temperature = os.getenv("AGENT_FORGE_TEMPERATURE")
    if temperature:
        try:
            environment_values["temperature"] = str(float(temperature))
        except ValueError as exc:
            raise ValueError("AGENT_FORGE_TEMPERATURE must be numeric") from exc

    for target, value in environment_values.items():
        if getattr(args, target, None) is None:
            setattr(args, target, float(value) if target == "temperature" else value)


def _first_environment(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def _validate_run_arguments(args: argparse.Namespace) -> None:
    """在创建 run artifact 前拒绝无意义或互相冲突的最终值。"""

    positive_fields = (
        "max_steps",
        "max_context_chars",
        "max_prompt_tokens",
        "max_tool_calls_per_turn",
        "timeout_seconds",
        "container_cpus",
        "container_pids_limit",
        "instruction_max_bytes",
    )
    for field_name in positive_fields:
        if getattr(args, field_name) <= 0:
            raise ValueError(f"{field_name} must be positive")
    non_negative_fields = (
        "reserved_output_tokens",
        "memory_recall_limit",
        "max_revision_rounds",
    )
    for field_name in non_negative_fields:
        if getattr(args, field_name) < 0:
            raise ValueError(f"{field_name} must not be negative")
    if args.cost_budget_usd is not None and args.cost_budget_usd < 0:
        raise ValueError("cost_budget_usd must not be negative")
    if args.reserved_output_tokens >= args.max_prompt_tokens:
        raise ValueError("reserved_output_tokens must be below max_prompt_tokens")
    if not 0.0 <= args.temperature <= 2.0:
        raise ValueError("temperature must be between 0.0 and 2.0")
    if not 1 <= args.max_workers <= 8:
        raise ValueError("max_workers must be between 1 and 8")
    if args.model_context_window < 1_024:
        raise ValueError("model_context_window must be at least 1024")


def _normalize_value(value: object, spec: _FieldSpec, *, location: str) -> object:
    if spec.kind == "str":
        if not isinstance(value, str):
            raise ValueError(f"{location} must be a string")
        normalized: object = value
    elif spec.kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{location} must be an integer")
        normalized = value
    elif spec.kind == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{location} must be numeric")
        normalized = float(value)
    elif spec.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{location} must be a boolean")
        normalized = value
    elif spec.kind == "str_list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{location} must be a list of strings")
        normalized = list(value)
    elif spec.kind == "selection":
        if isinstance(value, str):
            normalized = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            normalized = ",".join(value)
        else:
            raise ValueError(f"{location} must be auto, none, or a list of names")
    else:  # pragma: no cover - schema table is static
        raise AssertionError(f"unknown config kind: {spec.kind}")
    if spec.choices and normalized not in spec.choices:
        choices = ", ".join(sorted(str(item) for item in spec.choices))
        raise ValueError(f"{location} must be one of: {choices}")
    return normalized


def _reject_secret_keys(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            location = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in _SECRET_KEYS:
                raise ValueError(
                    f"secret field '{location}' is forbidden; use environment variables"
                )
            _reject_secret_keys(child, location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "RunConfigDocument",
    "load_run_config",
    "resolve_run_arguments",
    "resolved_run_config",
]

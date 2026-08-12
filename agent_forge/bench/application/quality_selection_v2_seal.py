"""封存 v2 正式槽位前的源码、凭据与动态预检输入。"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from agent_forge.bench.application.campaign_lifecycle import ExactImageIdentity
from agent_forge.bench.application.quality_selection_v2_evidence import (
    QualitySelectionV2EvidenceRefused,
    V2DynamicEvidence,
    compose_v2_campaign_inputs,
    validate_v2_campaign_inputs,
)
from agent_forge.bench.ports.campaign import TaggedSourceIdentityPort
from agent_forge.runtime.llm_config import (
    LLMConfig,
    LLMConfigRequest,
    resolve_llm_config,
)


class QualitySelectionV2SealRefused(RuntimeError):
    """源码、凭据或动态预检证据不能形成唯一安全 seal。"""


@dataclass(frozen=True)
class QualitySelectionV2CredentialIdentity:
    """不含密钥值的候选连接身份。"""

    candidate_id: str
    provider: str
    base_url: str
    model: str
    credential_source: str


@dataclass(frozen=True)
class QualitySelectionV2CampaignInputs:
    """Runner 与 evidence plan 共用的只读动态输入身份。"""

    campaign_inputs_path: Path
    campaign_inputs_sha256: str
    payload: Mapping[str, Any]
    launch_source: Mapping[str, object]
    candidate_observed_models: Mapping[str, str]
    image_identities: tuple[ExactImageIdentity, ...]
    pacing_ledger_prefix_sha256: str
    pacing_ledger_prefix_bytes: int
    pacing_last_sequence: int
    formal_command_argv_sha256: tuple[tuple[str, str], ...]


_CANDIDATES = ("v4-pro", "glm")


def verify_quality_selection_v2_source(
    project_root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    source_reader: TaggedSourceIdentityPort,
) -> dict[str, object]:
    """机械验证 annotated tag、HEAD、干净工作树与 tagged manifest blob。"""

    root = project_root.resolve()
    manifest_file = _under(root, manifest_path)
    if _read_json(manifest_file, "command manifest") != dict(manifest):
        raise QualitySelectionV2SealRefused("command manifest object drift")
    source = _object(manifest.get("source_identity"), "source identity")
    tag = str(source.get("expected_tag") or "")
    if (
        source.get("binding") != "external_annotated_git_tag"
        or source.get("require_clean_worktree_including_untracked") is not True
        or not tag
        or any(character.isspace() or ord(character) < 32 for character in tag)
    ):
        raise QualitySelectionV2SealRefused("invalid source identity policy")
    try:
        actual = source_reader.verify_tagged_manifest(manifest_file, tag)
    except (OSError, RuntimeError, ValueError) as exc:
        raise QualitySelectionV2SealRefused(str(exc)) from exc
    revision = str(actual.get("revision") or "")
    branch = actual.get("branch")
    if not isinstance(branch, str) or not branch:
        raise QualitySelectionV2SealRefused("launch branch identity is empty")
    return {
        "revision": revision,
        "branch": branch,
        "dirty": False,
        "working_tree_sha256": "",
    }


def verify_quality_selection_v2_credentials(
    manifest: Mapping[str, Any],
    *,
    resolver: Callable[[LLMConfigRequest], LLMConfig] = resolve_llm_config,
) -> tuple[QualitySelectionV2CredentialIdentity, ...]:
    """确认两候选仅解析到 OpenCode Go；结果永不包含密钥值。"""

    policy = _object(manifest.get("credential_preflight"), "credential policy")
    forbidden = policy.get("forbidden_fallback_sources")
    if (
        policy.get("required_present_nonempty") != "OPENCODE_GO_API_KEY"
        or policy.get("required_absent") != "AGENT_FORGE_API_KEY"
        or forbidden != ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"]
        or policy.get("resolver_required_credential_source") != "OPENCODE_GO_API_KEY"
        or policy.get("record_key_value") is not False
    ):
        raise QualitySelectionV2SealRefused("credential policy drift")
    if not os.environ.get("OPENCODE_GO_API_KEY"):
        raise QualitySelectionV2SealRefused(
            "required subscription credential is absent"
        )
    if "AGENT_FORGE_API_KEY" in os.environ:
        raise QualitySelectionV2SealRefused("forbidden credential source is present")
    entries = _objects(manifest.get("capability_probes"), "capability commands")
    if tuple(str(entry.get("candidate_id") or "") for entry in entries) != _CANDIDATES:
        raise QualitySelectionV2SealRefused("candidate credential order drift")
    formal_models = _formal_models(manifest)
    identities: list[QualitySelectionV2CredentialIdentity] = []
    for entry in entries:
        candidate = str(entry["candidate_id"])
        argv = _argv(entry.get("argv"), "capability argv")
        provider = _flag(argv, "--provider")
        base_url = _flag(argv, "--base-url").rstrip("/")
        model = _flag(argv, "--model")
        if provider != "opencode-go" or formal_models.get(candidate) != model:
            raise QualitySelectionV2SealRefused("candidate provider/model drift")
        try:
            config = resolver(
                LLMConfigRequest(
                    provider=provider,
                    base_url=base_url,
                    model=model,
                    timeout=int(_flag(argv, "--timeout")),
                    temperature=0.0,
                    thinking_mode=_flag(argv, "--thinking"),
                    reasoning_effort=_flag(argv, "--reasoning-effort"),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            raise QualitySelectionV2SealRefused(
                "candidate credential resolution failed"
            ) from exc
        if (
            not config.is_configured()
            or config.provider != provider
            or config.base_url != base_url
            or config.model != model
            or config.credential_source != "OPENCODE_GO_API_KEY"
        ):
            raise QualitySelectionV2SealRefused("resolved candidate credential drift")
        identities.append(
            QualitySelectionV2CredentialIdentity(
                candidate, provider, base_url, model, config.credential_source
            )
        )
    connections = {
        item.candidate_id: (item.provider, item.base_url, item.model)
        for item in identities
    }
    for entry in _objects(
        manifest.get("qualification_commands"), "qualification commands"
    ):
        candidate = str(entry.get("candidate_id") or "")
        argv = _argv(entry.get("argv"), "qualification argv")
        actual = (
            _flag(argv, "--provider"),
            _flag(argv, "--base-url").rstrip("/"),
            _flag(argv, "--model"),
        )
        if connections.get(candidate) != actual:
            raise QualitySelectionV2SealRefused("qualification connection drift")
    return tuple(identities)


def seal_quality_selection_v2_campaign_inputs(
    *,
    project_root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    readiness_path: Path,
    image_seal_path: Path,
    output_path: Path,
    source_reader: TaggedSourceIdentityPort,
) -> QualitySelectionV2CampaignInputs:
    """验证全部 preflight 事实并以 create-once 方式发布 campaign inputs。"""

    root = project_root.resolve()
    output = _under(root, output_path)
    if output.exists() or output.is_symlink():
        raise QualitySelectionV2SealRefused("campaign inputs seal already exists")
    launch_source = verify_quality_selection_v2_source(
        root, manifest_path, manifest, source_reader=source_reader
    )
    verify_quality_selection_v2_credentials(manifest)
    try:
        dynamic = compose_v2_campaign_inputs(
            root,
            manifest_path,
            manifest,
            launch_source=launch_source,
            readiness_path=readiness_path,
            image_seal_path=image_seal_path,
        )
        encoded = _encode_json(dynamic.payload)
    except QualitySelectionV2EvidenceRefused as exc:
        raise QualitySelectionV2SealRefused(str(exc)) from exc
    _publish_exclusive(root, output, encoded)
    return read_quality_selection_v2_campaign_inputs(
        project_root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        campaign_inputs_path=output,
        source_reader=source_reader,
    )


def read_quality_selection_v2_campaign_inputs(
    *,
    project_root: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    campaign_inputs_path: Path,
    source_reader: TaggedSourceIdentityPort,
) -> QualitySelectionV2CampaignInputs:
    """只读重验 seal；允许 ledger 在冻结 prefix 后追加正式事件。"""

    root = project_root.resolve()
    launch_source = verify_quality_selection_v2_source(
        root, manifest_path, manifest, source_reader=source_reader
    )
    try:
        dynamic = validate_v2_campaign_inputs(
            root, manifest_path, manifest, campaign_inputs_path
        )
    except QualitySelectionV2EvidenceRefused as exc:
        raise QualitySelectionV2SealRefused(str(exc)) from exc
    if dict(dynamic.launch_source) != launch_source:
        raise QualitySelectionV2SealRefused("campaign inputs launch source drift")
    return _campaign_inputs(dynamic)


def _campaign_inputs(dynamic: V2DynamicEvidence) -> QualitySelectionV2CampaignInputs:
    if dynamic.campaign_inputs_path is None:
        raise QualitySelectionV2SealRefused("campaign inputs were not published")
    return QualitySelectionV2CampaignInputs(
        campaign_inputs_path=dynamic.campaign_inputs_path,
        campaign_inputs_sha256=dynamic.campaign_inputs_sha256,
        payload=dynamic.payload,
        launch_source=dynamic.launch_source,
        candidate_observed_models=dynamic.observed_models,
        image_identities=dynamic.image_identities,
        pacing_ledger_prefix_sha256=dynamic.pacing_sha256,
        pacing_ledger_prefix_bytes=dynamic.pacing_bytes,
        pacing_last_sequence=dynamic.pacing_last_sequence,
        formal_command_argv_sha256=dynamic.formal_argv_sha256,
    )


def _formal_models(manifest: Mapping[str, Any]) -> dict[str, str]:
    fixed = _argv(manifest.get("fixed_argv"), "formal fixed argv")
    models: dict[str, str] = {}
    for command in _objects(manifest.get("commands"), "formal commands"):
        candidate = str(command.get("candidate_id") or "")
        model = _flag(
            (*fixed, *_argv(command.get("argv_suffix"), "formal suffix")), "--model"
        )
        if candidate in models and models[candidate] != model:
            raise QualitySelectionV2SealRefused("formal candidate model drift")
        models[candidate] = model
    if tuple(models) != _CANDIDATES:
        raise QualitySelectionV2SealRefused("formal candidate order drift")
    return models


def _publish_exclusive(root: Path, path: Path, encoded: bytes) -> None:
    """持久化私有临时 inode，再以 hard-link O_EXCL 语义发布。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path = _under(root, path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise QualitySelectionV2SealRefused(
                "campaign inputs seal already exists"
            ) from exc
        linked = True
        _fsync_directory(path.parent)
    except OSError as exc:
        if isinstance(exc, QualitySelectionV2SealRefused):
            raise
        raise QualitySelectionV2SealRefused(
            "cannot publish campaign inputs seal"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            if not linked:
                raise
        if linked:
            _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _encode_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
            )
            + "\n"
        ).encode()
    except (TypeError, ValueError) as exc:
        raise QualitySelectionV2SealRefused(
            "campaign inputs are not strict JSON"
        ) from exc


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise QualitySelectionV2SealRefused(f"{label} is not a regular file")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=_reject_constant
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise QualitySelectionV2SealRefused(f"cannot read strict {label}") from exc
    return _object(value, label)


def _under(root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    candidate = path if path.is_absolute() else root / path
    if candidate.is_symlink():
        raise QualitySelectionV2SealRefused(f"path cannot be a symlink: {raw}")
    resolved = candidate.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise QualitySelectionV2SealRefused(f"path escapes project root: {raw}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise QualitySelectionV2SealRefused("path escapes project root") from exc


def _argv(raw: object, label: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(item, str) or not item for item in raw
    ):
        raise QualitySelectionV2SealRefused(f"{label} is invalid")
    return tuple(cast(Sequence[str], raw))


def _flag(argv: Sequence[str], name: str) -> str:
    positions = [index for index, item in enumerate(argv) if item == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise QualitySelectionV2SealRefused(f"command flag drift: {name}")
    return argv[positions[0] + 1]


def _object(raw: object, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise QualitySelectionV2SealRefused(f"{label} must be an object")
    return cast(dict[str, Any], raw)


def _objects(raw: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise QualitySelectionV2SealRefused(f"{label} must be an object list")
    return cast(list[dict[str, Any]], raw)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


__all__ = [
    "QualitySelectionV2CampaignInputs",
    "QualitySelectionV2CredentialIdentity",
    "QualitySelectionV2SealRefused",
    "read_quality_selection_v2_campaign_inputs",
    "seal_quality_selection_v2_campaign_inputs",
    "verify_quality_selection_v2_credentials",
    "verify_quality_selection_v2_source",
]

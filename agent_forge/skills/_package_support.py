"""Skill 包解析与资源读取的内部实现，不参与运行时选择决策。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_SKILL_DOCUMENT_NAME = "SKILL.md"
_MAX_SKILL_DOCUMENT_BYTES = 64 * 1024
_MAX_RESOURCE_CHARS = 4_000


@dataclass(frozen=True, kw_only=True)
class SkillResourceSpec:
    """Skill 包声明的资源索引；此时尚未读取资源正文。"""

    path: str
    description: str
    activation_terms: tuple[str, ...] = ()
    max_chars: int = 2_000


@dataclass(frozen=True, kw_only=True)
class LoadedSkillResource:
    """本次 Run 实际披露的资源正文及其审计身份。"""

    path: str
    description: str
    content: str
    sha256: str
    original_chars: int
    disclosed_chars: int
    truncated: bool


@dataclass(frozen=True, kw_only=True)
class ParsedSkillPackage:
    """从一个标准 ``SKILL.md`` 包解析出的中间结果。"""

    metadata: dict[str, Any]
    instructions: str
    resource_specs: tuple[SkillResourceSpec, ...]
    content_sha256: str
    source: str


def parse_skill_package(package_dir: str | Path) -> ParsedSkillPackage:
    """校验并解析标准 Skill 包，但不提前读取参考资源正文。"""

    package_root = Path(package_dir).resolve()
    skill_document = package_root / _SKILL_DOCUMENT_NAME
    raw_document = _read_bounded_utf8(
        skill_document,
        max_bytes=_MAX_SKILL_DOCUMENT_BYTES,
        kind="skill document",
    )
    metadata, instructions = _split_skill_document(
        raw_document,
        source=skill_document,
    )
    return ParsedSkillPackage(
        metadata=metadata,
        instructions=instructions,
        resource_specs=_parse_resource_specs(metadata, package_root=package_root),
        content_sha256=hashlib.sha256(raw_document.encode("utf-8")).hexdigest(),
        source=str(skill_document),
    )


def load_matching_resources(
    *,
    skill_document_path: str,
    resource_specs: tuple[SkillResourceSpec, ...],
    task: str,
    limit: int,
) -> tuple[LoadedSkillResource, ...]:
    """只读取任务命中的有限资源；没有命中时返回空集合。"""

    normalized_task = (task or "").lower()
    scored_resources: list[tuple[int, int, SkillResourceSpec]] = []
    for declaration_order, resource_spec in enumerate(resource_specs):
        score = sum(
            1
            for term in resource_spec.activation_terms
            if task_contains_activation_term(normalized_task, term.lower())
        )
        if score:
            scored_resources.append((score, declaration_order, resource_spec))
    scored_resources.sort(key=lambda item: (-item[0], item[1]))

    skill_document = Path(skill_document_path)
    package_root = skill_document.parent.resolve()
    return tuple(
        _load_resource(package_root, resource_spec)
        for _, _, resource_spec in scored_resources[: max(0, limit)]
    )


def task_contains_activation_term(task: str, term: str) -> bool:
    """匹配完整英文意图词；中文按连续文本匹配。"""

    if not term:
        return False
    if any("\u4e00" <= character <= "\u9fff" for character in term):
        return term in task
    return bool(
        re.search(
            rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
            task,
        )
    )


def _split_skill_document(
    raw_document: str,
    *,
    source: Path,
) -> tuple[dict[str, Any], str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)(.*)\Z", raw_document, re.DOTALL)
    if match is None:
        raise ValueError(f"skill document requires YAML frontmatter: {source}")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid skill frontmatter at {source}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"skill frontmatter must be an object: {source}")
    instructions = match.group(2).strip()
    if not instructions:
        raise ValueError(f"skill document requires instruction body: {source}")
    return metadata, instructions


def _parse_resource_specs(
    metadata: dict[str, Any],
    *,
    package_root: Path,
) -> tuple[SkillResourceSpec, ...]:
    raw_resources = metadata.get("resources", [])
    if not isinstance(raw_resources, list):
        raise ValueError("skill field resources must be a list of objects")

    resources: list[SkillResourceSpec] = []
    for raw_resource in raw_resources:
        if not isinstance(raw_resource, dict):
            raise ValueError("skill resource must be an object")
        relative_path = str(raw_resource.get("path") or "").strip()
        description = str(raw_resource.get("description") or "").strip()
        if not relative_path or not description:
            raise ValueError("skill resource requires path and description")
        _resolve_package_file(package_root, relative_path)
        activation_terms = raw_resource.get("activation_terms", [])
        if not isinstance(activation_terms, list) or not all(
            isinstance(term, str) for term in activation_terms
        ):
            raise ValueError("skill resource activation_terms must be strings")
        max_chars = int(raw_resource.get("max_chars", 2_000))
        if not 1 <= max_chars <= _MAX_RESOURCE_CHARS:
            raise ValueError(
                f"skill resource max_chars must be 1-{_MAX_RESOURCE_CHARS}"
            )
        resources.append(
            SkillResourceSpec(
                path=relative_path,
                description=description,
                activation_terms=tuple(activation_terms),
                max_chars=max_chars,
            )
        )
    return tuple(resources)


def _load_resource(
    package_root: Path,
    resource_spec: SkillResourceSpec,
) -> LoadedSkillResource:
    resource_path = _resolve_package_file(package_root, resource_spec.path)
    full_content = _read_bounded_utf8(
        resource_path,
        max_bytes=_MAX_SKILL_DOCUMENT_BYTES,
        kind="skill resource",
    ).strip()
    disclosed_content = full_content[: resource_spec.max_chars]
    return LoadedSkillResource(
        path=resource_spec.path,
        description=resource_spec.description,
        content=disclosed_content,
        sha256=hashlib.sha256(full_content.encode("utf-8")).hexdigest(),
        original_chars=len(full_content),
        disclosed_chars=len(disclosed_content),
        truncated=len(disclosed_content) < len(full_content),
    )


def _resolve_package_file(package_root: Path, relative_path: str) -> Path:
    requested_path = Path(relative_path)
    if requested_path.is_absolute():
        raise ValueError(f"skill resource path must be relative: {relative_path}")
    resolved_path = (package_root / requested_path).resolve()
    if package_root not in resolved_path.parents:
        raise ValueError(f"skill resource escapes package: {relative_path}")
    if not resolved_path.is_file():
        raise ValueError(f"skill resource not found: {relative_path}")
    return resolved_path


def _read_bounded_utf8(path: Path, *, max_bytes: int, kind: str) -> str:
    try:
        raw_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"{kind} not found: {path}") from exc
    if len(raw_bytes) > max_bytes:
        raise ValueError(f"{kind} exceeds {max_bytes} bytes: {path}")
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{kind} must be UTF-8: {path}") from exc

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SkillSpec:
    """Versioned contract for one reusable agent capability.

    Why this class exists:
        In production, "Skill" is not just a Python function. It needs a stable
        name, version, owner, input schema, dependency list, permission scope,
        and rollback target. Without this explicit contract, a prompt
        or tool update can silently break downstream agents and there is no
        reliable way to compare old/new behavior during a regression run.

    Field meanings:
        name: Stable capability id used by routing and audit logs.
        version: Semantic-ish version string. The registry keeps multiple
            versions side by side so canary and rollback are possible.
        description: Human/model-facing explanation of when the skill should be
            selected. Bad descriptions are a common source of tool misuse.
        entrypoint: Import path or service endpoint that implements the skill.
        input_schema: JSON Schema-like input contract. The runtime can validate
            or prompt-repair arguments before side effects happen.
        permissions: Least-privilege scopes required by this skill.
        dependencies: External systems, datasets, indexes, or tools the skill
            depends on. This makes rollout risk visible.
        rollback_to: Previous known-good version. Empty means no automatic
            rollback target is declared.
        owner: Team or person responsible for review and incident handling.
        tags: Optional routing labels such as customer-service/read-only.
        activation_terms: Task words/phrases that make the runtime select this
            skill automatically.
        tool_names: Tool allowlist this skill expects to use. The runtime feeds
            these into ToolRouter so a selected skill changes real execution.
        operating_procedure: Concrete steps shown in the prompt when the skill
            is active.
        done_criteria: Conditions the agent should satisfy before final answer.
        failure_modes: Known failure patterns and recovery guidance.
    """

    name: str
    version: str
    description: str
    entrypoint: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    rollback_to: str = ""
    owner: str = ""
    tags: list[str] = field(default_factory=list)
    activation_terms: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    operating_procedure: list[str] = field(default_factory=list)
    done_criteria: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SkillSpec":
        """Build a SkillSpec from manifest JSON with clear validation errors."""

        required = ["name", "version", "description", "entrypoint"]
        missing = [field_name for field_name in required if not data.get(field_name)]
        if missing:
            raise ValueError(f"skill manifest missing required field(s): {', '.join(missing)}")

        return cls(
            name=str(data["name"]),
            version=str(data["version"]),
            description=str(data["description"]),
            entrypoint=str(data["entrypoint"]),
            input_schema=_dict_field(data, "input_schema"),
            permissions=_list_field(data, "permissions"),
            dependencies=_list_field(data, "dependencies"),
            rollback_to=str(data.get("rollback_to", "")),
            owner=str(data.get("owner", "")),
            tags=_list_field(data, "tags"),
            activation_terms=_list_field(data, "activation_terms"),
            tool_names=_list_field(data, "tool_names"),
            operating_procedure=_list_field(data, "operating_procedure"),
            done_criteria=_list_field(data, "done_criteria"),
            failure_modes=_list_field(data, "failure_modes"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view used by CLI reports and audit snapshots."""

        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entrypoint": self.entrypoint,
            "input_schema": self.input_schema,
            "permissions": self.permissions,
            "dependencies": self.dependencies,
            "rollback_to": self.rollback_to,
            "owner": self.owner,
            "tags": self.tags,
            "activation_terms": self.activation_terms,
            "tool_names": self.tool_names,
            "operating_procedure": self.operating_procedure,
            "done_criteria": self.done_criteria,
            "failure_modes": self.failure_modes,
        }

    def prompt_card(self) -> str:
        """Render this skill as runtime instructions for the active task."""

        procedure = "\n".join(f"  {index}. {step}" for index, step in enumerate(self.operating_procedure, 1))
        done = "\n".join(f"  - {item}" for item in self.done_criteria)
        failure = "\n".join(f"  - {item}" for item in self.failure_modes)
        tools = ", ".join(self.tool_names) or "no extra tools"
        permissions = ", ".join(self.permissions) or "none declared"
        dependencies = ", ".join(self.dependencies) or "none declared"
        return (
            f"skill:{self.name}@{self.version}\n"
            f"description:{self.description}\n"
            f"permissions:{permissions}\n"
            f"dependencies:{dependencies}\n"
            f"tools:{tools}\n"
            f"procedure:\n{procedure or '  1. Follow the skill description.'}\n"
            f"done_criteria:\n{done or '  - Produce a grounded final answer.'}\n"
            f"failure_recovery:\n{failure or '  - If blocked, explain the blocker with evidence.'}"
        )


class SkillRegistry:
    """In-memory registry for versioned Skills.

    Why this class exists:
        Agent tool catalogs grow messy fast. A registry gives the runtime and
        the operator one place to answer: which skill version is active, what
        permissions it needs, what it depends on, and how to roll it back. This
        implementation is intentionally local-file based so the repo remains
        easy to run, while the contract maps cleanly to a DB/service registry in
        a larger platform.
    """

    def __init__(self) -> None:
        """Start with no skills; manifests make registry contents explicit."""

        self._skills: dict[str, list[SkillSpec]] = {}

    def register(self, spec: SkillSpec) -> None:
        """Register or replace one skill version.

        Registering the same name/version twice replaces the previous spec.
        That behavior is useful for local development, while immutable storage
        should be enforced by a production registry backend.
        """

        versions = [item for item in self._skills.get(spec.name, []) if item.version != spec.version]
        versions.append(spec)
        self._skills[spec.name] = sorted(versions, key=lambda item: _version_key(item.version))

    def load_manifest(self, path: str | Path) -> None:
        """Load one JSON manifest file.

        The file may contain either a single object or a list of objects. Keeping
        both shapes supported lets small projects start with one file and grow
        into a catalog without changing the CLI.
        """

        manifest_path = Path(path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"skill manifest not found: {manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid skill manifest JSON at {manifest_path}: {exc}") from exc

        items = data if isinstance(data, list) else [data]
        if not all(isinstance(item, dict) for item in items):
            raise ValueError(f"skill manifest must be an object or list of objects: {manifest_path}")
        for item in items:
            self.register(SkillSpec.from_mapping(item))

    def load_manifests(self, paths: list[str | Path]) -> None:
        """Load multiple manifests in order; later entries can replace versions."""

        for path in paths:
            self.load_manifest(path)

    def list_specs(self, *, name: str | None = None) -> list[SkillSpec]:
        """List registered skills, sorted by name then version."""

        if name:
            return list(self._skills.get(name, []))
        specs: list[SkillSpec] = []
        for skill_name in sorted(self._skills):
            specs.extend(self._skills[skill_name])
        return specs

    def resolve(self, name: str, version: str | None = None) -> SkillSpec:
        """Return an exact version or the latest registered version.

        The model/router usually asks for a skill by name. Runtime governance
        resolves that to a concrete immutable version so trace and rollback can
        reproduce the same behavior later.
        """

        versions = self._skills.get(name, [])
        if not versions:
            raise KeyError(f"unknown skill: {name}")
        if version is None:
            return versions[-1]
        for spec in versions:
            if spec.version == version:
                return spec
        raise KeyError(f"unknown skill version: {name}@{version}")

    def rollback_target(self, name: str, version: str | None = None) -> SkillSpec | None:
        """Return the declared rollback target for a skill version, if any."""

        current = self.resolve(name, version)
        if not current.rollback_to:
            return None
        return self.resolve(name, current.rollback_to)

    def select_for_task(
        self,
        task: str,
        *,
        names: list[str] | None = None,
        limit: int = 3,
    ) -> list[SkillSpec]:
        """Select concrete Skill versions for one agent run.

        This is the bridge from "registered capability" to "runtime behavior".
        If names are provided, exact latest versions are used. Otherwise the
        selector scores activation terms against the task and falls back to the
        general coding skill, so a real run always receives usable guidance.
        """

        if names:
            return [self.resolve(name) for name in names]

        task_lower = (task or "").lower()
        read_only_requested = _read_only_requested(task_lower)
        scored: list[tuple[int, SkillSpec]] = []
        for spec in self.list_specs():
            latest = self.resolve(spec.name)
            if latest.version != spec.version:
                continue
            if read_only_requested and _is_write_skill(spec):
                continue
            score = 0
            searchable = " ".join([spec.name, spec.description, *spec.activation_terms, *spec.tags]).lower()
            for term in spec.activation_terms:
                term_lower = term.lower()
                if term_lower and term_lower in task_lower:
                    score += 4
            for token in task_lower.replace("_", " ").replace("-", " ").split():
                if len(token) >= 3 and token in searchable:
                    score += 1
            if score:
                scored.append((score, spec))

        if scored:
            scored.sort(key=lambda item: (-item[0], item[1].name))
            return [spec for _, spec in scored[:limit]]

        # A user may ask an unusual coding task with no obvious keyword. Give
        # the runtime a practical default instead of an empty "skills" section.
        fallback = []
        fallback_names = ["repo_orientation"] if read_only_requested else ["targeted_code_edit", "repo_orientation"]
        for name in fallback_names:
            try:
                fallback.append(self.resolve(name))
            except KeyError:
                continue
        return fallback[:limit]

    def to_report(self) -> list[dict[str, Any]]:
        """Return compact audit rows instead of dumping full schemas by default."""

        rows = []
        for spec in self.list_specs():
            rows.append(
                {
                    "name": spec.name,
                    "version": spec.version,
                    "owner": spec.owner,
                    "entrypoint": spec.entrypoint,
                    "permissions": spec.permissions,
                    "dependencies": spec.dependencies,
                    "rollback_to": spec.rollback_to,
                    "tags": spec.tags,
                    "tool_names": spec.tool_names,
                }
            )
        return rows


def _dict_field(data: dict[str, Any], name: str) -> dict[str, Any]:
    """Validate a manifest object field."""

    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"skill manifest field {name} must be an object")
    return value


def _list_field(data: dict[str, Any], name: str) -> list[str]:
    """Validate a manifest list-of-string field."""

    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"skill manifest field {name} must be a list of strings")
    return list(value)


def _version_key(version: str) -> tuple[Any, ...]:
    """Sort semantic-ish versions while tolerating local labels.

    Ordering notes:
        1.10.0 sorts after 1.2.0.
        2.0.0-rc1 sorts near 2.0.0 without needing a semver dependency.
    """

    parts: list[Any] = []
    for token in re.split(r"[\.\-\+_]", version):
        if token.isdigit():
            parts.append((0, int(token)))
        else:
            parts.append((1, token))
    return tuple(parts)


def _read_only_requested(task_lower: str) -> bool:
    """Detect user intent to inspect/explain without editing."""

    markers = [
        "不要修改",
        "不修改",
        "不要改",
        "不改",
        "只读",
        "仅阅读",
        "不要写",
        "do not modify",
        "do not edit",
        "read only",
        "without editing",
    ]
    return any(marker in task_lower for marker in markers)


def _is_write_skill(spec: SkillSpec) -> bool:
    """Return whether a skill can write or run validation commands."""

    write_tools = {"apply_patch", "write_file", "run_command"}
    return any(tool in write_tools for tool in spec.tool_names) or any(
        permission.startswith("write:") for permission in spec.permissions
    )

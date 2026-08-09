"""Skill 的数据契约。

标准包 ``packages/swebench_repair/SKILL.md`` 展示磁盘格式；本文件中的三个
数据类型定义运行期契约。序列化、Prompt 渲染和 Port 适配不属于运行主链。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_forge.skills._package_support import LoadedSkillResource, SkillResourceSpec


# 核心数据 1/3：磁盘上的 Skill 定义被解析为这个不可变对象。
@dataclass(frozen=True, kw_only=True)
class SkillSpec:
    """一个版本化 Skill 的完整定义；正文和资源不进入 discovery metadata。"""

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
    required_tool_names: list[str] = field(default_factory=list)
    optional_tool_names: list[str] = field(default_factory=list)
    instructions: str = ""
    operating_procedure: list[str] = field(default_factory=list)
    done_criteria: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    resource_specs: tuple[SkillResourceSpec, ...] = ()
    content_sha256: str = ""
    source: str = ""

    # region 构造、序列化与 Prompt 渲染（实现细节）
    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any],
        *,
        source: str = "",
        instructions: str = "",
        resource_specs: tuple[SkillResourceSpec, ...] = (),
        content_sha256: str = "",
    ) -> "SkillSpec":
        """校验 manifest object，并保留实际来源路径。"""

        required = ["name", "version", "description", "entrypoint"]
        missing = [field_name for field_name in required if not data.get(field_name)]
        if missing:
            raise ValueError(f"skill manifest missing required field(s): {', '.join(missing)}")

        legacy_tool_names = _list_field(data, "tool_names")
        required_tool_names = _list_field(data, "required_tools")
        optional_tool_names = _list_field(data, "optional_tools")
        if not required_tool_names and not optional_tool_names:
            # 旧 manifest 的 tool_names 只表达路由偏好，没有“缺失即拒绝”的语义。
            optional_tool_names = list(legacy_tool_names)
        all_tool_names = _ordered_unique(
            [
                *legacy_tool_names,
                *required_tool_names,
                *optional_tool_names,
            ]
        )

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
            tool_names=all_tool_names,
            required_tool_names=required_tool_names,
            optional_tool_names=optional_tool_names,
            instructions=instructions,
            operating_procedure=_list_field(data, "operating_procedure"),
            done_criteria=_list_field(data, "done_criteria"),
            failure_modes=_list_field(data, "failure_modes"),
            resource_specs=resource_specs,
            content_sha256=content_sha256,
            source=source or str(data.get("source", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        """返回报告和 manifest hash 使用的完整定义。"""

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
            "required_tools": self.required_tool_names,
            "optional_tools": self.optional_tool_names,
            "instructions": self.instructions,
            "operating_procedure": self.operating_procedure,
            "done_criteria": self.done_criteria,
            "failure_modes": self.failure_modes,
            "resources": [
                {
                    "path": resource.path,
                    "description": resource.description,
                    "activation_terms": list(resource.activation_terms),
                    "max_chars": resource.max_chars,
                }
                for resource in self.resource_specs
            ],
            "content_sha256": self.content_sha256,
            "source": self.source,
        }

    def prompt_card(self) -> str:
        """只渲染模型执行当前工作流所需的信息。"""

        procedure = "\n".join(
            f"  {index}. {step}"
            for index, step in enumerate(self.operating_procedure, 1)
        )
        done = "\n".join(f"  - {item}" for item in self.done_criteria)
        failure = "\n".join(f"  - {item}" for item in self.failure_modes)
        sections = [
            f"skill:{self.name}@{self.version}",
            f"purpose:{self.description}",
        ]
        if self.instructions:
            sections.append(f"instructions:\n{self.instructions.strip()}")
        if procedure:
            sections.append(f"procedure:\n{procedure}")
        if done:
            sections.append(f"done_criteria:\n{done}")
        if failure:
            sections.append(f"failure_recovery:\n{failure}")
        return "\n".join(sections)

    def catalog_entry(self, *, reason: str = "") -> "SkillCatalogEntry":
        """只返回 discovery 所需 metadata，不展开操作步骤正文。"""

        return SkillCatalogEntry(
            name=self.name,
            version=self.version,
            description=self.description,
            tags=tuple(self.tags),
            activation_terms=tuple(self.activation_terms),
            source=self.source or self.entrypoint,
            selection_reason=reason,
        )
    # endregion 构造、序列化与 Prompt 渲染结束


# 核心数据 2/3：发现阶段只传轻量目录项，避免把所有 Skill 正文塞进 Context。
@dataclass(frozen=True)
class SkillCatalogEntry:
    """不包含 procedure、done criteria 或 failure modes 的轻量目录项。"""

    name: str
    version: str
    description: str
    tags: tuple[str, ...]
    activation_terms: tuple[str, ...]
    source: str
    selection_reason: str = ""


# 核心数据 3/3：一个 Run 最终固定并注入 Context 的 Skill 快照。
@dataclass(frozen=True, kw_only=True)
class ActivatedSkill:
    """已激活 Skill；``loaded_resources`` 只包含本任务实际披露的资源。"""

    spec: SkillSpec
    selection_reason: str
    loaded_resources: tuple[LoadedSkillResource, ...] = ()

    # region Runtime SkillView Port 适配属性（实现细节）
    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def version(self) -> str:
        return self.spec.version

    @property
    def entrypoint(self) -> str:
        return self.spec.entrypoint

    @property
    def source(self) -> str:
        return self.spec.source or self.spec.entrypoint

    @property
    def content_sha256(self) -> str:
        return self.spec.content_sha256

    @property
    def tool_names(self) -> list[str]:
        return list(self.spec.tool_names)

    @property
    def required_tool_names(self) -> list[str]:
        return list(self.spec.required_tool_names)

    @property
    def optional_tool_names(self) -> list[str]:
        return list(self.spec.optional_tool_names)

    def prompt_card(self) -> str:
        """渲染主指令，以及最多一份与当前任务匹配的参考资源。"""

        sections = [self.spec.prompt_card()]
        for resource in self.loaded_resources:
            sections.append(
                "\n".join(
                    [
                        f"selected_reference:{resource.path}",
                        f"reference_purpose:{resource.description}",
                        "reference_content:",
                        resource.content,
                    ]
                )
            )
        return "\n\n".join(section for section in sections if section)
    # endregion Runtime SkillView Port 适配属性结束


def _dict_field(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"skill manifest field {name} must be an object")
    return value


def _list_field(data: dict[str, Any], name: str) -> list[str]:
    value = data.get(name, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"skill manifest field {name} must be a list of strings")
    return list(value)


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))

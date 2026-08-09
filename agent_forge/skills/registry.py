"""Skill 运行时选择主链。

核心调用链由三个方法组成：
``select_for_task`` -> ``discover_for_task`` -> ``activate``。
JSON 兼容、版本目录和文件解析都是装配细节；标准包 I/O 已隔离到
``_package_support.py``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent_forge.contracts import WORKSPACE_WRITE_TOOL_NAMES
from agent_forge.runtime.ports.skills import SkillSelectorPort
from agent_forge.skills._package_support import (
    load_matching_resources,
    parse_skill_package,
    task_contains_activation_term,
)
from agent_forge.skills.models import ActivatedSkill, SkillCatalogEntry, SkillSpec
from agent_forge.tools.tool_router import task_requests_read_only


class SkillRegistry(SkillSelectorPort):
    """Runtime 使用的 Skill 目录与选择器。

    三个主链方法分别负责：

    1. ``select_for_task``：Runtime 唯一入口；串起发现和激活。
    2. ``discover_for_task``：只用 metadata 决定选谁。
    3. ``activate``：固定版本，并按需披露最多一份资源。

    后面的注册、JSON 兼容和版本查询只负责启动装配，可以整体折叠。
    """

    def __init__(self) -> None:
        self._skills: dict[str, list[SkillSpec]] = {}

    # region 核心主链：选择 -> 发现 -> 激活
    # 核心入口：Runtime 只调用这个方法，不需要知道 Skill 包如何解析。
    def select_for_task(
        self,
        task: str,
        *,
        names: list[str] | None = None,
        limit: int = 1,
    ) -> list[ActivatedSkill]:
        """为一个 Run 生成不可变 Skill 快照。

        上游是 ``RunPreparation._select_active_skills``；返回值会固定到
        ``AgentRunSession.active_skills``。这里先发现候选，再只激活候选正文，
        防止未选中的 Skill 占用模型上下文。
        """

        discovered_skills = self.discover_for_task(task, names=names, limit=limit)
        return [
            self.activate(discovered_skill, task=task)
            for discovered_skill in discovered_skills
        ]

    def discover_for_task(
        self,
        task: str,
        *,
        names: list[str] | None = None,
        limit: int = 1,
    ) -> list[SkillCatalogEntry]:
        """只根据轻量 metadata 选择 Skill，不展开主指令或参考资源。

        当前选择策略是可解释的确定性规则：显式指定优先；否则按激活词计分；
        没有命中时只选一个有界兜底。只读任务在选择阶段就排除写入型 Skill。
        """

        # 1. 显式指定用于配置固定实验或运行策略，不再做关键词猜测。
        if names:
            return [
                self.resolve(name).catalog_entry(reason="explicit invocation")
                for name in names
            ]

        # 2. 自动模式只比较最新版 metadata；完整英文词命中一次计 4 分。
        normalized_task = (task or "").lower()
        read_only_requested = task_requests_read_only(normalized_task)
        scored_skills: list[tuple[int, SkillSpec]] = []
        for skill_spec in self.list_specs():
            latest_skill_spec = self.resolve(skill_spec.name)
            if latest_skill_spec.version != skill_spec.version:
                continue
            if read_only_requested and _is_write_skill(skill_spec):
                continue
            activation_score = sum(
                4
                for activation_term in skill_spec.activation_terms
                if task_contains_activation_term(
                    normalized_task,
                    activation_term.lower(),
                )
            )
            if activation_score:
                scored_skills.append((activation_score, skill_spec))

        if scored_skills:
            scored_skills.sort(key=lambda item: (-item[0], item[1].name))
            return [
                skill_spec.catalog_entry(
                    reason=f"task metadata score={activation_score}"
                )
                for activation_score, skill_spec in scored_skills[:limit]
            ]

        # 3. 无命中时不枚举所有 Skill；只读任务只给仓库理解，写任务给定向编辑。
        fallback_skill_names = (
            ["repo_orientation"]
            if read_only_requested
            else ["targeted_code_edit", "repo_orientation"]
        )
        fallback_skills: list[SkillCatalogEntry] = []
        for skill_name in fallback_skill_names:
            try:
                fallback_skills.append(
                    self.resolve(skill_name).catalog_entry(reason="bounded fallback")
                )
            except KeyError:
                continue
        return fallback_skills[:limit]

    def activate(
        self,
        discovered_skill: SkillCatalogEntry,
        *,
        task: str = "",
    ) -> ActivatedSkill:
        """把目录命中转换为本 Run 使用的完整、不可变 Skill 快照。

        激活只做两件事：固定明确版本；按任务读取最多一份有界参考资料。
        它不会执行脚本、调用工具或授予权限，后续安全边界仍由 Runtime 负责。
        """

        selected_skill_spec = self.resolve(
            discovered_skill.name,
            discovered_skill.version,
        )
        disclosed_resources = load_matching_resources(
            skill_document_path=selected_skill_spec.source,
            resource_specs=selected_skill_spec.resource_specs,
            task=task,
            # 渐进式披露硬边界：一个 Run 最多加载一份任务匹配参考资料。
            limit=1,
        )
        return ActivatedSkill(
            spec=selected_skill_spec,
            selection_reason=discovered_skill.selection_reason,
            loaded_resources=disclosed_resources,
        )

    # endregion 核心主链结束

    # region 启动装配与目录管理（实现细节）
    def register(self, spec: SkillSpec) -> None:
        """按 name/version 覆盖注册，并保持可预测版本顺序。"""

        versions = [
            item
            for item in self._skills.get(spec.name, [])
            if item.version != spec.version
        ]
        versions.append(spec)
        self._skills[spec.name] = sorted(
            versions,
            key=lambda item: _version_key(item.version),
        )

    def load_manifest(self, path: str | Path) -> None:
        """从受信任的本地 JSON 文件加载定义；不会执行 entrypoint。"""

        manifest_path = Path(path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"skill manifest not found: {manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid skill manifest JSON at {manifest_path}: {exc}") from exc

        items = data if isinstance(data, list) else [data]
        if not all(isinstance(item, dict) for item in items):
            raise ValueError(
                "skill manifest must be an object or list of objects: "
                f"{manifest_path}"
            )
        for item in items:
            self.register(
                SkillSpec.from_mapping(item, source=str(manifest_path.resolve()))
            )

    def load_manifests(self, paths: list[str | Path]) -> None:
        """按调用方给出的顺序加载多个 manifest。"""

        for path in paths:
            self.load_manifest(path)

    # 主要入口：加载标准 Skill 包，但不把资源正文提前放进 catalog。
    def load_package(self, package_dir: str | Path) -> None:
        """装配标准 Skill 包；解析和路径校验由内部辅助模块负责。"""

        parsed_package = parse_skill_package(package_dir)
        self.register(
            SkillSpec.from_mapping(
                parsed_package.metadata,
                source=parsed_package.source,
                instructions=parsed_package.instructions,
                resource_specs=parsed_package.resource_specs,
                content_sha256=parsed_package.content_sha256,
            )
        )

    def list_specs(self, *, name: str | None = None) -> list[SkillSpec]:
        """返回完整定义，供管理面和测试使用，不应直接注入模型。"""

        if name:
            return list(self._skills.get(name, []))
        specs: list[SkillSpec] = []
        for skill_name in sorted(self._skills):
            specs.extend(self._skills[skill_name])
        return specs

    def resolve(self, name: str, version: str | None = None) -> SkillSpec:
        """解析指定版本；省略 version 时返回排序后的最新定义。"""

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
        """读取 manifest 声明的回滚版本，不执行回滚动作。"""

        current = self.resolve(name, version)
        if not current.rollback_to:
            return None
        return self.resolve(name, current.rollback_to)
    # endregion 启动装配与目录管理结束


def _version_key(version: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for token in re.split(r"[\.\-\+_]", version):
        if token.isdigit():
            parts.append((0, int(token)))
        else:
            parts.append((1, token))
    return tuple(parts)


def _is_write_skill(spec: SkillSpec) -> bool:
    write_tools = {*WORKSPACE_WRITE_TOOL_NAMES, "run_command"}
    return any(tool in write_tools for tool in spec.tool_names) or any(
        permission.startswith("write:") for permission in spec.permissions
    )

"""Skill 对外 API；运行主链入口是 ``SkillRegistry.select_for_task``。"""

from .builtin import build_default_skill_registry, built_in_skill_specs
from .models import ActivatedSkill, SkillCatalogEntry, SkillSpec
from .registry import (
    SkillRegistry,
)

__all__ = [
    "ActivatedSkill",
    "SkillCatalogEntry",
    "SkillRegistry",
    "SkillSpec",
    "build_default_skill_registry",
    "built_in_skill_specs",
]

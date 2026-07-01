"""Skill governance primitives.

Tools are low-level executable actions. A Skill is a product-facing capability
that can wrap tools, prompts, policies, and external dependencies behind a
versioned contract. Keeping this package separate lets interview reviewers see
that the project distinguishes "can call a function" from "can safely operate a
business capability over time".
"""

from .builtin import build_default_skill_registry, built_in_skill_specs
from .registry import SkillRegistry, SkillSpec

__all__ = ["SkillRegistry", "SkillSpec", "build_default_skill_registry", "built_in_skill_specs"]

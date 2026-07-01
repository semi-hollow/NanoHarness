"""Context engineering package.

Why this package exists:
    A coding agent fails quickly when prompt context is either too small
    (missing the target file) or too large (instruction dilution and noisy
    history). This package owns the policy for what enters each LLM turn:
    repository map, ranked files, source previews, lightweight retrieval,
    short-term memory, topic-shift filtering, and budget accounting.

Read first:
    ``context_builder.py`` shows the rendered prompt sections.
    ``context_strategy.py`` explains selection, compression, and inheritance.
    ``file_ranker.py`` and ``memory.py`` are implementation details.

If removed:
    ``AgentLoop`` would have to either dump the whole repository into the
    prompt or make blind model calls without code evidence.
"""

from .context_builder import build_context
from .context_strategy import ContextStrategy, build_context_strategy
from .repo_map import build_repo_map

__all__ = [
    "ContextStrategy",
    "build_context",
    "build_context_strategy",
    "build_repo_map",
]

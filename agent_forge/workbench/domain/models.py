from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkbenchCommand:
    """Validated browser action with a redacted display command."""

    title: str
    command: list[str]
    display_command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkbenchJob:
    """Observable state for one bounded background command."""

    id: str
    title: str
    command: list[str]
    display_command: list[str]
    env_overrides: dict[str, str] = field(default_factory=dict, repr=False)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    exit_code: int | None = None
    output: str = ""


UiCommand = WorkbenchCommand
UiJob = WorkbenchJob

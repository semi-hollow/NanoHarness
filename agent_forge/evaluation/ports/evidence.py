from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class CaseEvidenceReader(Protocol):

    def load_usage(self, case: dict[str, Any], run_dir: Path) -> dict[str, Any]: ...

    def load_environment(self, case: dict[str, Any], run_dir: Path) -> dict[str, Any]: ...

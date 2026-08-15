"""Benchmark campaign 的外部依赖契约。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from agent_forge.bench.domain.campaign import CampaignState
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary


class BenchmarkRunnerPort(Protocol):
    def __call__(self, request: SwebenchRunRequest) -> BenchRunSummary: ...


class SourceIdentityPort(Protocol):
    def read(self) -> dict[str, Any]: ...


class CampaignArtifactPort(Protocol):
    def campaign_dir(self, output_root: str, campaign_id: str) -> Path: ...

    def load_state(self, campaign_dir: Path) -> CampaignState | None: ...

    def save_state(self, campaign_dir: Path, state: CampaignState) -> Path: ...

    def read_scorecard(self, run_dir: Path) -> dict[str, Any]: ...

    def scorecard_sha256(self, run_dir: Path) -> str: ...

    def write_final_artifacts(
        self,
        campaign_dir: Path,
        state: CampaignState,
        summary: dict[str, Any],
    ) -> tuple[Path, Path]: ...

    def publish_public_bundle(
        self,
        publish_root: str,
        campaign_dir: Path,
        state: CampaignState,
        summary: dict[str, Any],
    ) -> Path: ...

    def update_latest_pointer(self, campaign_dir: Path) -> None: ...

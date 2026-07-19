"""Benchmark campaign 的本地 checkpoint、source identity 与公开证据导出。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.campaign import CampaignState
from agent_forge.bench.presentation.campaign_report import render_campaign_report


_SECRET_KEY = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)="
    r"([^&\s\"'<>]+)"
)
_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp)/[^\s\"'<>]*")


class GitSourceIdentity:
    """读取 campaign 所属代码快照；不读取 remote 或用户身份。"""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir.resolve()

    def read(self) -> dict[str, Any]:
        revision = self._git("rev-parse", "HEAD")
        branch = self._git("branch", "--show-current") or "detached"
        status = self._git("status", "--porcelain")
        dirty = bool(status)
        return {
            "revision": revision,
            "branch": branch,
            "dirty": dirty,
            "working_tree_sha256": self._working_tree_sha256(status) if dirty else "",
        }

    def _git(self, *args: str) -> str:
        process = subprocess.run(
            ["git", *args],
            cwd=self._project_dir,
            text=True,
            capture_output=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"cannot read benchmark source identity: {process.stderr.strip()}"
            )
        return process.stdout.strip()

    def _working_tree_sha256(self, status: str) -> str:
        """让显式 allow-dirty 的恢复仍能拒绝工作树内容漂移。"""

        digest = hashlib.sha256(status.encode("utf-8"))
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=self._project_dir,
            capture_output=True,
        )
        if diff.returncode != 0:
            raise RuntimeError("cannot hash dirty benchmark source")
        digest.update(diff.stdout)
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=self._project_dir,
            capture_output=True,
        )
        if untracked.returncode != 0:
            raise RuntimeError("cannot hash untracked benchmark source")
        for raw_name in sorted(item for item in untracked.stdout.split(b"\0") if item):
            digest.update(raw_name)
            path = self._project_dir / raw_name.decode("utf-8", errors="surrogateescape")
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()


class FileCampaignArtifacts:
    """本地状态保留完整 provenance；公开 bundle 只保留脱敏聚合与 scorecard。"""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir.resolve()

    def campaign_dir(self, output_root: str, campaign_id: str) -> Path:
        root = Path(output_root)
        if not root.is_absolute():
            root = self._project_dir / root
        directory = (root / campaign_id).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def load_state(self, campaign_dir: Path) -> CampaignState | None:
        path = campaign_dir / "campaign.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("campaign.json must contain an object")
        return CampaignState.from_dict(data)

    def save_state(self, campaign_dir: Path, state: CampaignState) -> Path:
        path = campaign_dir / "campaign.json"
        _write_json_atomic(path, state.to_dict())
        return path

    def read_scorecard(self, run_dir: Path) -> dict[str, Any]:
        path = run_dir / "scorecard.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def scorecard_sha256(self, run_dir: Path) -> str:
        path = run_dir / "scorecard.json"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""

    def write_final_artifacts(
        self,
        campaign_dir: Path,
        state: CampaignState,
        summary: dict[str, Any],
    ) -> tuple[Path, Path]:
        summary_path = campaign_dir / "campaign_summary.json"
        report_path = campaign_dir / "campaign.md"
        _write_json_atomic(summary_path, summary)
        _write_text_atomic(report_path, render_campaign_report(state, summary))
        return summary_path, report_path

    def publish_public_bundle(
        self,
        publish_root: str,
        campaign_dir: Path,
        state: CampaignState,
        summary: dict[str, Any],
    ) -> Path:
        root = Path(publish_root)
        if not root.is_absolute():
            root = self._project_dir / root
        destination = (root / state.campaign_id).resolve()
        destination.mkdir(parents=True, exist_ok=True)

        public_scorecards = {
            record.key: _sanitize(self.read_scorecard(Path(record.run_dir)))
            for record in state.records
            if record.status == "completed" and record.run_dir
        }
        public_hashes = {
            key: _json_sha256(scorecard)
            for key, scorecard in public_scorecards.items()
        }
        public_state = _public_state(state, public_hashes)
        public_summary = _sanitize(summary)
        _write_json_atomic(destination / "manifest.json", public_state.to_dict())
        _write_json_atomic(destination / "summary.json", public_summary)
        _write_text_atomic(
            destination / "README.md",
            render_campaign_report(public_state, public_summary, public=True),
        )

        for record in state.records:
            if record.status != "completed" or not record.run_dir:
                continue
            run_destination = destination / "runs" / record.key
            run_destination.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(
                run_destination / "scorecard.json",
                public_scorecards[record.key],
            )
            _write_json_atomic(
                run_destination / "result.json",
                {
                    "case_id": record.case_id,
                    "repetition": record.repetition,
                    "variant": record.variant,
                    "run_id": record.run_id,
                    "scorecard_sha256": public_hashes[record.key],
                    "evidence": _sanitize(record.evidence),
                },
            )
        return destination

    def update_latest_pointer(self, campaign_dir: Path) -> None:
        latest = self._project_dir / ".agent_forge" / "latest"
        latest.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(latest / "campaign.txt", str(campaign_dir))


def _public_state(
    state: CampaignState,
    public_scorecard_hashes: dict[str, str],
) -> CampaignState:
    records = []
    for record in state.records:
        item = record.to_dict()
        item["run_dir"] = f"runs/{record.key}" if record.status == "completed" else ""
        item["scorecard_sha256"] = public_scorecard_hashes.get(record.key, "")
        item["error"] = "run_failed" if record.status == "failed" else ""
        records.append(type(record).from_dict(_sanitize(item)))
    return CampaignState(
        campaign_id=state.campaign_id,
        config_digest=state.config_digest,
        config=_sanitize(state.config),
        source=_sanitize(state.source),
        created_at=state.created_at,
        updated_at=state.updated_at,
        records=records,
        status=state.status,
    )


def _sanitize(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item, key=key) for item in value]
    if isinstance(value, str):
        without_paths = _LOCAL_PATH.sub("<local-path>", value)
        return _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", without_paths)
    return value


def _write_json_atomic(path: Path, data: Any) -> None:
    _write_text_atomic(path, _json_text(data))


def _json_sha256(data: Any) -> str:
    return hashlib.sha256(_json_text(data).encode("utf-8")).hexdigest()


def _json_text(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)

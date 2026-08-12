"""Benchmark campaign 的本地 checkpoint、source identity 与公开证据导出。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.campaign import CampaignState
from agent_forge.bench.ports import (
    CampaignArtifactPort,
    CampaignJournalPort,
    TaggedSourceIdentityPort,
)
from agent_forge.bench.presentation.campaign_report import render_campaign_report


_SECRET_KEY = re.compile(
    r"(^|[_-])("
    r"api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|token|secret|password|authorization"
    r")($|[_-])",
    re.I,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|authorization)="
    r"([^&\s\"'<>]+)"
)
_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp)/[^\s\"'<>]*")


class GitSourceIdentity(TaggedSourceIdentityPort):
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
            path = self._project_dir / raw_name.decode(
                "utf-8", errors="surrogateescape"
            )
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def verify_tagged_manifest(
        self,
        manifest_path: Path,
        expected_tag: str,
    ) -> dict[str, Any]:
        """验证 annotated tag、HEAD、干净工作树与 manifest blob。"""

        manifest = manifest_path.resolve()
        if not manifest.is_relative_to(self._project_dir):
            raise RuntimeError("tagged manifest escapes the project directory")
        relative = manifest.relative_to(self._project_dir).as_posix()
        if ":" in relative or "\n" in relative:
            raise RuntimeError("tagged manifest path is invalid")
        reference = f"refs/tags/{expected_tag}"
        self._git("check-ref-format", reference)
        if self._git("cat-file", "-t", reference) != "tag":
            raise RuntimeError("expected source tag is not annotated")
        revision = self._git("rev-parse", "--verify", f"{reference}^{{commit}}")
        actual = self.read()
        if actual.get("revision") != revision:
            raise RuntimeError("expected source tag does not peel to HEAD")
        if actual.get("dirty") is not False or actual.get("working_tree_sha256") != "":
            raise RuntimeError("source worktree is not clean including untracked files")
        self._git("ls-files", "--error-unmatch", "--", relative)
        if self._git_bytes("cat-file", "blob", f"{reference}:{relative}") != (
            manifest.read_bytes()
        ):
            raise RuntimeError("tagged manifest blob differs from launch file")
        return actual

    def _git_bytes(self, *args: str) -> bytes:
        process = subprocess.run(
            ["git", *args],
            cwd=self._project_dir,
            capture_output=True,
        )
        if process.returncode != 0:
            raise RuntimeError("cannot read benchmark Git blob")
        return process.stdout


class FileCampaignJournal(CampaignJournalPort):
    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir.resolve()

    def resolve(self, path: str | Path) -> Path:
        raw = Path(path)
        if ".." in raw.parts:
            raise ValueError("campaign state path cannot contain '..'")
        candidate = raw if raw.is_absolute() else self._project_dir / raw
        resolved = candidate.resolve(strict=False)
        if resolved == self._project_dir or not resolved.is_relative_to(
            self._project_dir
        ):
            raise ValueError("campaign state path escapes the project directory")
        return resolved

    def read(self, path: str | Path) -> dict[str, Any] | None:
        resolved = self.resolve(path)
        if not resolved.exists():
            return None
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("campaign state must contain a JSON object")
        return payload

    def write(self, path: str | Path, payload: dict[str, Any]) -> None:
        resolved = self.resolve(path)
        if resolved.with_suffix(resolved.suffix + ".tmp").is_symlink():
            raise ValueError("campaign temporary state path cannot be a symlink")
        _write_json_atomic(resolved, payload)
        _fsync_path(resolved)
        _fsync_path(resolved.parent)

    def write_once(self, path: str | Path, payload: dict[str, Any]) -> bool:
        """先持久化私有 inode，再以 hard-link 排他发布完整 JSON。"""

        resolved = self.resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved = self.resolve(resolved)
        encoded = _json_text(payload).encode("utf-8")
        temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        linked = False
        try:
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, resolved, follow_symlinks=False)
            except FileExistsError:
                return False
            linked = True
            _fsync_path(resolved.parent)
            return True
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            if linked:
                _fsync_path(resolved.parent)

    def create_once(self, path: str | Path) -> bool:
        resolved = self.resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved = self.resolve(resolved)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(resolved, flags, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as marker:
            marker.write("started\n")
            marker.flush()
            os.fsync(marker.fileno())
        _fsync_path(resolved.parent)
        return True


class AppendOnlyJsonlLedger:
    """为一次不可重跑阶段创建并顺序追加 fsync 事件。"""

    def __init__(self, project_dir: Path, path: str | Path) -> None:
        self._journal = FileCampaignJournal(project_dir)
        self._path = self._journal.resolve(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path = self._journal.resolve(self._path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError as exc:
            raise FileExistsError(
                "append-only ledger already exists; this phase cannot be rerun"
            ) from exc
        os.close(descriptor)
        _fsync_path(self._path)
        _fsync_path(self._path.parent)
        self._sequence = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def next_sequence(self) -> int:
        return self._sequence + 1

    def append(self, payload: dict[str, Any]) -> None:
        expected = self._sequence + 1
        if payload.get("sequence") != expected:
            raise ValueError("append-only ledger sequence drift")
        try:
            encoded = (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("append-only ledger event is not strict JSON") from exc
        flags = os.O_WRONLY | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self._path, flags)
        try:
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise OSError("append-only ledger write was incomplete")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _fsync_path(self._path.parent)
        self._sequence = expected


class FileCampaignArtifacts(CampaignArtifactPort):
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
            key: _json_sha256(scorecard) for key, scorecard in public_scorecards.items()
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
    # `total_tokens` / `max_prompt_tokens` 是公开评测指标，不是认证 token。
    if _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item, key=str(item_key))
            for item_key, item in value.items()
        }
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


def _fsync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

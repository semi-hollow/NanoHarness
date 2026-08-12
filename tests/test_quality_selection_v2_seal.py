from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent_forge.bench.adapters.campaign_files import GitSourceIdentity
from agent_forge.bench.application.quality_selection_v2_seal import (
    QualitySelectionV2SealRefused,
    read_quality_selection_v2_campaign_inputs,
    seal_quality_selection_v2_campaign_inputs,
    verify_quality_selection_v2_credentials,
    verify_quality_selection_v2_source,
)

BASE_URL = "https://opencode.ai/zen/go/v1"
TAG = "quality-selection-v2-test-tag"


@dataclass(frozen=True)
class SealFixture:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    readiness_path: Path
    image_seal_path: Path
    output_path: Path
    ledger_path: Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def _ensure_flag(argv: list[str], name: str, value: str) -> None:
    if name in argv:
        assert _flag(argv, name) == value
        return
    argv.extend([name, value])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _endpoint() -> tuple[str, str]:
    return BASE_URL, hashlib.sha256(BASE_URL.encode("utf-8")).hexdigest()


def _augment_capability(path: Path) -> None:
    evidence = _read(path)
    endpoint, digest = _endpoint()
    evidence.update(
        base_url_origin_path=endpoint,
        base_url_sha256=digest,
        max_attempts=1,
    )
    _write(path, evidence)


def _actual_ledger(manifest: dict[str, Any]) -> bytes:
    pacing = manifest["pacing"]
    assert isinstance(pacing, dict)
    entries = [
        *manifest["capability_probes"],
        *manifest["qualification_commands"],
    ]
    events: list[dict[str, object]] = []
    clock = 0.0

    def wait(phase: str, seconds: int) -> None:
        nonlocal clock
        started = clock
        clock += seconds
        events.append(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "event_type": "pacing_wait",
                "phase": phase,
                "required_seconds": seconds,
                "started_monotonic": started,
                "ended_monotonic": clock,
                "elapsed_seconds": seconds,
                "result": "passed",
            }
        )

    wait("initial_quiet", int(pacing["initial_quiet_seconds"]))
    for provider_sequence, raw_entry in enumerate(entries, start=1):
        assert isinstance(raw_entry, dict)
        argv = raw_entry["argv"]
        assert isinstance(argv, list)
        candidate = str(raw_entry["candidate_id"])
        argv_sha = _json_sha(argv)
        started = clock
        events.append(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "event_type": "provider_command_started",
                "candidate_id": candidate,
                "provider_sequence": provider_sequence,
                "argv_sha256": argv_sha,
                "started_monotonic": started,
            }
        )
        clock += 2
        events.append(
            {
                "schema_version": 1,
                "sequence": len(events) + 1,
                "event_type": "provider_command_completed",
                "candidate_id": candidate,
                "provider_sequence": provider_sequence,
                "argv_sha256": argv_sha,
                "started_monotonic": started,
                "ended_monotonic": clock,
                "elapsed_seconds": 2,
                "exit_code": 0,
                "result": "passed",
            }
        )
        wait(
            "between_provider_commands",
            int(pacing["minimum_seconds_between_provider_commands"]),
        )
    wait(
        "qualification_to_formal_cooldown",
        int(pacing["qualification_to_formal_cooldown_seconds"]),
    )
    assert len(events) == 20
    return b"".join(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for event in events
    )


def _prepare(tmp_path: Path) -> SealFixture:
    evidence_fixtures = importlib.import_module(
        "tests.test_quality_selection_v2_evidence"
    )
    base: Any = evidence_fixtures._fixture(tmp_path)
    manifest = base.manifest
    manifest["source_identity"] = {
        "binding": "external_annotated_git_tag",
        "expected_tag": TAG,
        "require_clean_worktree_including_untracked": True,
    }
    manifest["credential_preflight"] = {
        "launcher_shell": "zsh -lic",
        "required_present_nonempty": "OPENCODE_GO_API_KEY",
        "required_absent": "AGENT_FORGE_API_KEY",
        "forbidden_fallback_sources": ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"],
        "resolver_required_credential_source": "OPENCODE_GO_API_KEY",
        "record_key_value": False,
    }
    fixed = manifest["fixed_argv"]
    assert isinstance(fixed, list)
    _ensure_flag(fixed, "--provider", "opencode-go")
    _ensure_flag(fixed, "--base-url", BASE_URL)
    _ensure_flag(fixed, "--model-request-max-attempts", "1")

    capabilities = manifest["capability_probes"]
    qualifications = manifest["qualification_commands"]
    assert isinstance(capabilities, list) and isinstance(qualifications, list)
    capability_paths: dict[str, Path] = {}
    for raw_entry in capabilities:
        assert isinstance(raw_entry, dict)
        argv = raw_entry["argv"]
        assert isinstance(argv, list)
        _ensure_flag(argv, "--base-url", BASE_URL)
        _ensure_flag(argv, "--thinking", "enabled")
        _ensure_flag(argv, "--reasoning-effort", "high")
        _ensure_flag(argv, "--timeout", "600")
        _ensure_flag(argv, "--max-attempts", "1")
        output = tmp_path / _flag(argv, "--output")
        _augment_capability(output)
        capability_paths[str(raw_entry["candidate_id"])] = output

    for raw_entry in qualifications:
        assert isinstance(raw_entry, dict)
        candidate = str(raw_entry["candidate_id"])
        argv = raw_entry["argv"]
        assert isinstance(argv, list)
        _ensure_flag(argv, "--base-url", BASE_URL)
        _ensure_flag(argv, "--thinking", "enabled")
        _ensure_flag(argv, "--reasoning-effort", "high")
        _ensure_flag(argv, "--timeout", "600")
        _ensure_flag(argv, "--max-attempts", "1")
        _ensure_flag(
            argv,
            "--capability-preflight",
            str(capability_paths[candidate].relative_to(tmp_path)),
        )
        output = tmp_path / _flag(argv, "--output")
        qualification = _read(output)
        records = qualification["rounds"]
        assert isinstance(records, list)
        for record in records:
            assert isinstance(record, dict)
            child = tmp_path / str(record["artifact"])
            _augment_capability(child)
            record["artifact_sha256"] = _sha(child)
        qualification.update(
            max_attempts=1,
            capability_preflight_sha256=_sha(capability_paths[candidate]),
        )
        _write(output, qualification)

    commands = manifest["commands"]
    assert isinstance(commands, list)
    composed = []
    for raw_command in commands:
        assert isinstance(raw_command, dict)
        suffix = raw_command["argv_suffix"]
        assert isinstance(suffix, list)
        composed.append([*fixed, *suffix])
    manifest["composed_commands_sha256"] = _json_sha(composed)

    ledger_path = tmp_path / str(manifest["ledger_path"])
    ledger_path.write_bytes(_actual_ledger(manifest))
    _write(tmp_path / base.manifest_path, manifest)
    (tmp_path / base.inputs_path).unlink()
    (tmp_path / ".gitignore").write_text(
        ".agent_forge/\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "sealed source")
    _git(tmp_path, "tag", "-a", TAG, "-m", "quality selection v2 source")

    return SealFixture(
        root=tmp_path,
        manifest_path=base.manifest_path,
        manifest=manifest,
        readiness_path=Path(str(base.inputs["readiness"]["path"])),
        image_seal_path=Path(str(base.inputs["image_seal"]["path"])),
        output_path=base.inputs_path,
        ledger_path=Path(str(manifest["ledger_path"])),
    )


def _seal(fixture: SealFixture):  # type: ignore[no-untyped-def]
    with patch.dict(
        os.environ,
        {"OPENCODE_GO_API_KEY": "subscription-secret"},
        clear=True,
    ):
        return seal_quality_selection_v2_campaign_inputs(
            project_root=fixture.root,
            manifest_path=fixture.manifest_path,
            manifest=fixture.manifest,
            readiness_path=fixture.readiness_path,
            image_seal_path=fixture.image_seal_path,
            output_path=fixture.output_path,
            source_reader=GitSourceIdentity(fixture.root),
        )


def test_source_gate_requires_annotated_head_and_clean_untracked(
    tmp_path: Path,
) -> None:
    fixture = _prepare(tmp_path)
    source = verify_quality_selection_v2_source(
        fixture.root,
        fixture.manifest_path,
        fixture.manifest,
        source_reader=GitSourceIdentity(fixture.root),
    )

    assert source == {
        "revision": _git(tmp_path, "rev-parse", "HEAD"),
        "branch": _git(tmp_path, "branch", "--show-current"),
        "dirty": False,
        "working_tree_sha256": "",
    }
    dirty = tmp_path / "untracked.txt"
    dirty.write_text("untracked\n", encoding="utf-8")
    with pytest.raises(
        QualitySelectionV2SealRefused, match="clean including untracked"
    ):
        verify_quality_selection_v2_source(
            fixture.root,
            fixture.manifest_path,
            fixture.manifest,
            source_reader=GitSourceIdentity(fixture.root),
        )
    dirty.unlink()
    fixture.manifest["source_identity"]["expected_tag"] = "lightweight"
    _write(tmp_path / fixture.manifest_path, fixture.manifest)
    _git(tmp_path, "add", str(fixture.manifest_path))
    _git(tmp_path, "commit", "-qm", "point at lightweight tag")
    _git(tmp_path, "tag", "lightweight")
    with pytest.raises(QualitySelectionV2SealRefused, match="not annotated"):
        verify_quality_selection_v2_source(
            fixture.root,
            fixture.manifest_path,
            fixture.manifest,
            source_reader=GitSourceIdentity(fixture.root),
        )


def test_credential_gate_returns_only_safe_exact_identities(tmp_path: Path) -> None:
    fixture = _prepare(tmp_path)
    with patch.dict(
        os.environ,
        {"OPENCODE_GO_API_KEY": "subscription-secret"},
        clear=True,
    ):
        identities = verify_quality_selection_v2_credentials(fixture.manifest)

    assert [item.candidate_id for item in identities] == ["v4-pro", "glm"]
    assert [item.model for item in identities] == ["deepseek-v4-pro", "glm-5.2"]
    assert all(
        item.provider == "opencode-go"
        and item.base_url == BASE_URL
        and item.credential_source == "OPENCODE_GO_API_KEY"
        for item in identities
    )
    assert "subscription-secret" not in repr(identities)
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(QualitySelectionV2SealRefused, match="absent"):
            verify_quality_selection_v2_credentials(fixture.manifest)
    with patch.dict(
        os.environ,
        {
            "OPENCODE_GO_API_KEY": "subscription-secret",
            "AGENT_FORGE_API_KEY": "forbidden",
        },
        clear=True,
    ):
        with pytest.raises(QualitySelectionV2SealRefused, match="forbidden"):
            verify_quality_selection_v2_credentials(fixture.manifest)
    with patch.dict(
        os.environ,
        {
            "OPENCODE_GO_API_KEY": "subscription-secret",
            "DEEPSEEK_API_KEY": "present-but-unused",
            "OPENAI_API_KEY": "present-but-unused",
        },
        clear=True,
    ):
        identities_with_unrelated_keys = verify_quality_selection_v2_credentials(
            fixture.manifest
        )
    assert identities_with_unrelated_keys == identities
    drifted = json.loads(json.dumps(fixture.manifest))
    qualification_argv = drifted["qualification_commands"][0]["argv"]
    qualification_argv[qualification_argv.index("--base-url") + 1] = (
        "https://different.invalid/v1"
    )
    with patch.dict(
        os.environ,
        {"OPENCODE_GO_API_KEY": "subscription-secret"},
        clear=True,
    ):
        with pytest.raises(QualitySelectionV2SealRefused, match="connection drift"):
            verify_quality_selection_v2_credentials(drifted)


def test_create_once_seal_and_read_only_revalidation_allow_ledger_growth(
    tmp_path: Path,
) -> None:
    fixture = _prepare(tmp_path)
    sealed = _seal(fixture)

    assert sealed.campaign_inputs_path == tmp_path / fixture.output_path
    assert len(sealed.campaign_inputs_sha256) == 64
    assert tuple(sealed.candidate_observed_models) == ("v4-pro", "glm")
    assert len(sealed.image_identities) == 10
    assert sealed.pacing_last_sequence == 20
    assert len(sealed.formal_command_argv_sha256) == 20
    payload_text = sealed.campaign_inputs_path.read_text(encoding="utf-8")
    assert "subscription-secret" not in payload_text
    assert sealed.payload["transport_policy"] == {
        "max_attempts": 1,
        "allowed_error_codes": [],
    }

    with (tmp_path / fixture.ledger_path).open("ab") as stream:
        stream.write(b'{"sequence":21,"event_type":"future_formal_event"}\n')
    reread = read_quality_selection_v2_campaign_inputs(
        project_root=tmp_path,
        manifest_path=fixture.manifest_path,
        manifest=fixture.manifest,
        campaign_inputs_path=fixture.output_path,
        source_reader=GitSourceIdentity(tmp_path),
    )
    assert reread.campaign_inputs_sha256 == sealed.campaign_inputs_sha256
    with pytest.raises(QualitySelectionV2SealRefused, match="already exists"):
        _seal(fixture)


@pytest.mark.parametrize("mutation", ["ledger", "child", "observed", "image"])
def test_semantic_drift_refuses_without_publishing(
    tmp_path: Path, mutation: str
) -> None:
    fixture = _prepare(tmp_path)
    if mutation == "ledger":
        ledger = tmp_path / fixture.ledger_path
        events = [json.loads(line) for line in ledger.read_text().splitlines()]
        events[0]["elapsed_seconds"] -= 1
        ledger.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
    elif mutation == "child":
        qualification = fixture.manifest["qualification_commands"][0]
        output = tmp_path / _flag(qualification["argv"], "--output")
        child = tmp_path / str(_read(output)["rounds"][0]["artifact"])
        child.write_text("{}\n", encoding="utf-8")
    elif mutation == "observed":
        capability = fixture.manifest["capability_probes"][0]
        output = tmp_path / _flag(capability["argv"], "--output")
        evidence = _read(output)
        evidence["observed_response_model"] = "different-model"
        _write(output, evidence)
    else:
        image = _read(tmp_path / fixture.image_seal_path)
        image["entries"][0]["identity"]["image_id"] = "sha256:short"
        _write(tmp_path / fixture.image_seal_path, image)

    with pytest.raises(QualitySelectionV2SealRefused):
        _seal(fixture)
    assert not (tmp_path / fixture.output_path).exists()


def test_existing_or_external_seal_target_is_never_overwritten(
    tmp_path: Path,
) -> None:
    fixture = _prepare(tmp_path)
    output = tmp_path / fixture.output_path
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("sentinel\n", encoding="utf-8")

    with pytest.raises(QualitySelectionV2SealRefused, match="already exists"):
        _seal(fixture)
    assert output.read_text(encoding="utf-8") == "sentinel\n"
    with patch.dict(
        os.environ,
        {"OPENCODE_GO_API_KEY": "subscription-secret"},
        clear=True,
    ):
        with pytest.raises(QualitySelectionV2SealRefused, match="escapes"):
            seal_quality_selection_v2_campaign_inputs(
                project_root=fixture.root,
                manifest_path=fixture.manifest_path,
                manifest=fixture.manifest,
                readiness_path=fixture.readiness_path,
                image_seal_path=fixture.image_seal_path,
                output_path=tmp_path.parent / "outside-seal.json",
                source_reader=GitSourceIdentity(fixture.root),
            )

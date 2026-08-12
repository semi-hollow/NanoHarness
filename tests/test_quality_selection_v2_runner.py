from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from _pytest.monkeypatch import MonkeyPatch

import pytest

from agent_forge.bench.application.quality_selection_v2 import (
    QualitySelectionV2Refused,
)
from scripts import run_quality_selection_v2 as runner


def _manifest(root: Path) -> dict[str, Any]:
    artifact = Path(".agent_forge/v2")
    commands = []
    for ordinal in range(1, 21):
        case = (ordinal + 1) // 2
        candidate = "v4-pro" if ordinal % 2 else "glm"
        commands.append(
            {
                "ordinal": ordinal,
                "candidate_id": candidate,
                "instance_ids": [f"repo__case-{case}"],
                "image": {"tag": f"registry/case-{case}:latest"},
                "output_root": str(artifact / "formal" / f"slot-{ordinal:03d}"),
                "argv_suffix": [
                    "--model",
                    candidate,
                    "--limit",
                    "1",
                    "--instance-id",
                    f"repo__case-{case}",
                    "--output-root",
                    str(artifact / "formal" / f"slot-{ordinal:03d}"),
                ],
            }
        )
    return {
        "fixed_argv": ["forge", "bench", "swebench"],
        "commands": commands,
        "capability_probes": [
            {
                "candidate_id": candidate,
                "argv": ["probe", "--output", str(artifact / f"{candidate}.json")],
            }
            for candidate in ("v4-pro", "glm")
        ],
        "qualification_commands": [
            {
                "candidate_id": candidate,
                "argv": [
                    "probe",
                    "--output",
                    str(artifact / f"qualification-{index}.json"),
                ],
            }
            for index, candidate in enumerate(
                ("v4-pro", "glm", "glm", "v4-pro"), start=1
            )
        ],
        "ledger_path": str(artifact / "ledger.jsonl"),
        "image_seal_state_path": str(artifact / "images.json"),
        "campaign_inputs_path": str(artifact / "inputs.json"),
        "campaign_state_root": str(artifact / "state"),
        "summary_output_path": str(artifact / "summary.json"),
        "readiness_path": str(artifact / "readiness.json"),
        "campaign_id": "showcase-quality-selection-v2",
        "preflight_ledger_last_sequence": 20,
        "completed_ledger_last_sequence": 80,
        "pacing": {"minimum_seconds_between_provider_commands": 300},
        "prelaunch": {
            "docker_data_free_space": {"minimum_free_bytes": 1},
        },
    }


class _Ledger:
    def __init__(self, *_args: object) -> None:
        self.sequence = 0

    @property
    def next_sequence(self) -> int:
        return self.sequence + 1

    def append(self, event: Mapping[str, Any]) -> None:
        assert event["sequence"] == self.sequence + 1
        self.sequence += 1


class _Preflight:
    def __init__(self, **kwargs: Any) -> None:
        self.append = kwargs["append_event"]

    def qualify(self) -> tuple[object, ...]:
        for sequence in range(1, 21):
            self.append({"sequence": sequence})
        return ()


class _FormalLauncher:
    def __init__(self, **kwargs: Any) -> None:
        self.append = kwargs["append_event"]
        self.sequence = kwargs["initial_sequence"]

    def __call__(self, _argv: Sequence[str]) -> int:
        for _ in range(3):
            self.sequence += 1
            self.append({"sequence": self.sequence})
        return 0


class _FormalRunner:
    groups = 0

    def __init__(self, **kwargs: Any) -> None:
        self.launch = kwargs["launch_command"]

    def run_group(self, slots: Sequence[Any]) -> tuple[Any, ...]:
        type(self).groups += 1
        records = []
        for slot in slots:
            assert self.launch(slot.expectation.command_argv) == 0
            records.append(SimpleNamespace(status="validated"))
        return tuple(records)


def test_execute_campaign_wires_twenty_no_rerun_slots(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    manifest = _manifest(tmp_path)
    manifest_path = Path("manifest.json")
    (tmp_path / manifest_path).write_text("{}\n", encoding="utf-8")
    readiness = tmp_path / manifest["readiness_path"]
    readiness.parent.mkdir(parents=True)
    readiness.write_text("{}\n", encoding="utf-8")
    quality_slots = runner.slots_from_manifest(manifest, tmp_path)
    formal_slots = tuple(
        SimpleNamespace(
            slot_id=slot.slot_id,
            expectation=SimpleNamespace(command_argv=slot.argv),
        )
        for slot in quality_slots
    )
    plan = SimpleNamespace(
        campaign_id="showcase-quality-selection-v2",
        identity_sha256="a" * 64,
        expected_launch_source={"revision": "b" * 40},
        slots=formal_slots,
    )
    _FormalRunner.groups = 0
    monkeypatch.setattr(runner, "validate_preregistration", lambda *_: manifest)
    monkeypatch.setattr(
        runner, "verify_quality_selection_v2_source", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        runner, "verify_quality_selection_v2_credentials", lambda *_: ()
    )
    monkeypatch.setattr(runner, "verify_quality_selection_v2_readiness", lambda *_: {})
    monkeypatch.setattr(runner, "FileCampaignJournal", lambda *_: object())
    monkeypatch.setattr(runner, "AppendOnlyJsonlLedger", _Ledger)
    monkeypatch.setattr(runner, "DockerExactImageRuntime", lambda: object())
    monkeypatch.setattr(runner, "ColimaDockerDataFreeSpaceProbe", lambda: object())
    monkeypatch.setattr(runner, "SequentialImageSealer", lambda *_a, **_k: object())
    monkeypatch.setattr(runner, "QualitySelectionV2Preflight", _Preflight)
    monkeypatch.setattr(runner, "QualitySelectionV2FormalLauncher", _FormalLauncher)
    monkeypatch.setattr(
        runner, "FreeSpaceGuardedExactImageRuntime", lambda *_a, **_k: object()
    )
    monkeypatch.setattr(runner, "FormalCampaignRunner", _FormalRunner)
    monkeypatch.setattr(
        runner,
        "seal_quality_selection_v2_campaign_inputs",
        lambda **_kwargs: SimpleNamespace(
            campaign_inputs_path=tmp_path / "inputs.json"
        ),
    )
    monkeypatch.setattr(runner, "build_v2_evidence_plan", lambda *_: plan)
    monkeypatch.setattr(runner, "GitSourceIdentity", lambda *_: object())
    monkeypatch.setattr(
        runner,
        "audit_completed_formal_campaign",
        lambda **_kwargs: tuple(range(20)),
    )

    result = runner.execute_campaign(tmp_path, manifest_path)

    assert result["status"] == "formal_campaign_complete_pending_independent_summary"
    assert result["validated_starts"] == 20
    assert result["pacing_events"] == 80
    assert _FormalRunner.groups == 10


def test_pristine_gate_rejects_qualification_round_directory(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    readiness = tmp_path / manifest["readiness_path"]
    readiness.parent.mkdir(parents=True)
    readiness.write_text("{}\n", encoding="utf-8")
    output = Path(manifest["qualification_commands"][0]["argv"][-1])
    (tmp_path / output.with_suffix("")).mkdir(parents=True)

    with pytest.raises(RuntimeError, match="dynamic output already exists"):
        runner._require_pristine_dynamic_outputs(tmp_path, manifest)


def test_no_go_readiness_does_not_claim_one_shot_ledger(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    manifest_path = Path("manifest.json")
    (tmp_path / manifest_path).write_text("{}\n", encoding="utf-8")
    readiness = tmp_path / manifest["readiness_path"]
    readiness.parent.mkdir(parents=True)
    readiness.write_text(
        '{"status":"no_go_current_subscription_window","gates":'
        '{"weekly_covers_complete_campaign":false,"use_balance_off":true}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "validate_preregistration", lambda *_: manifest)
    monkeypatch.setattr(
        runner, "verify_quality_selection_v2_source", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        runner, "verify_quality_selection_v2_credentials", lambda *_: ()
    )
    monkeypatch.setattr(
        runner,
        "AppendOnlyJsonlLedger",
        lambda *_: (_ for _ in ()).throw(AssertionError("ledger claimed")),
    )

    with pytest.raises(QualitySelectionV2Refused, match="readiness is not GO"):
        runner.execute_campaign(tmp_path, manifest_path)

    assert not (tmp_path / manifest["ledger_path"]).exists()

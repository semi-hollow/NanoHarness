from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_forge.bench.application.formal_campaign import FormalCampaignRecord
from agent_forge.bench.application.formal_selection import ExpectedFormalSlot
from agent_forge.bench.formal_artifacts import ValidatedFormalRun
from scripts import summarize_quality_selection_v2 as summary


def _validated_run(
    ordinal: int,
    *,
    resolved: int,
    tokens: int = 100,
    cost: str = "0.01",
) -> ValidatedFormalRun:
    digest = f"{ordinal:064x}"
    return ValidatedFormalRun(
        run_id=f"run-{ordinal:03d}",
        planned=1,
        finalized=1,
        resolved=resolved,
        unresolved=1 - resolved,
        decided=1,
        empty=0,
        infrastructure=0,
        failed_tools=ordinal % 3,
        tokens=tokens,
        cost=Decimal(cost),
        patch_binding_parts=(b"patch",),
        artifact_sha256={
            "results.json": digest,
            "scorecard.json": digest,
            "predictions.jsonl": digest,
            "official_aggregate.json": digest,
        },
        command_argv_sha256=digest,
        expected_source_identity_sha256=digest,
        frozen_inputs_sha256=digest,
        config_sha256=digest,
        transport_retries=0,
    )


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[dict[str, Any], Path]:
    manifest_path = tmp_path / "command-manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    output = tmp_path / ".agent_forge/v2/selection-summary.json"
    manifest = {
        "summary_output_path": str(output.relative_to(tmp_path)),
        "campaign_inputs_path": ".agent_forge/v2/campaign-inputs.json",
        "ledger_path": ".agent_forge/v2/pacing.jsonl",
        "campaign_state_root": ".agent_forge/v2/state",
        "preflight_ledger_last_sequence": 20,
        "completed_ledger_last_sequence": 80,
        "pacing": {"minimum_seconds_between_provider_commands": 300},
        "fixed_argv": [
            ".venv/bin/forge",
            "bench",
            "swebench",
            "--max-steps",
            "128",
            "--model-request-max-attempts",
            "1",
        ],
        "capability_probes": [
            {
                "candidate_id": "v4-pro",
                "argv": [
                    "probe",
                    "--provider",
                    "opencode-go",
                    "--model",
                    "deepseek-v4-pro",
                ],
            },
            {
                "candidate_id": "glm",
                "argv": [
                    "probe",
                    "--provider",
                    "opencode-go",
                    "--model",
                    "glm-5.2",
                ],
            },
        ],
    }
    expected_slots: list[ExpectedFormalSlot] = []
    records: list[FormalCampaignRecord] = []
    for ordinal in range(1, 21):
        candidate = "v4-pro" if ordinal % 2 else "glm"
        case_id = f"repo__case-{(ordinal + 1) // 2}"
        expected_slots.append(
            ExpectedFormalSlot(f"slot-{ordinal:03d}", candidate, case_id)
        )
        # V4-Pro 8/10，GLM 6/10，机械选择 V4-Pro。
        candidate_index = (ordinal + 1) // 2
        resolved = int(candidate_index <= (8 if candidate == "v4-pro" else 6))
        records.append(
            FormalCampaignRecord(
                slot_id=f"slot-{ordinal:03d}",
                status="validated",
                return_code=0,
                validated=_validated_run(ordinal, resolved=resolved),
            )
        )
    campaign_inputs_path = tmp_path / ".agent_forge/v2/campaign-inputs.json"
    campaign_inputs_path.parent.mkdir(parents=True)
    campaign_inputs_path.write_text("{}\n", encoding="utf-8")
    campaign_inputs = SimpleNamespace(
        campaign_inputs_path=campaign_inputs_path,
        campaign_inputs_sha256="a" * 64,
        pacing_ledger_prefix_bytes=123,
        candidate_observed_models={
            "v4-pro": "deepseek-v4-pro",
            "glm": "glm-5.2",
        },
    )
    plan = SimpleNamespace(
        campaign_id="showcase-quality-selection-v2",
        identity_sha256="b" * 64,
        expected_launch_source={"revision": "c" * 40},
        slots=tuple(SimpleNamespace(slot_id=item.slot_id) for item in expected_slots),
        expected_slots=tuple(expected_slots),
        candidate_order=("v4-pro", "glm"),
        pacing_ledger_prefix_sha256="d" * 64,
    )
    monkeypatch.setattr(summary, "validate_preregistration", lambda *_: manifest)
    monkeypatch.setattr(summary, "slots_from_manifest", lambda *_: plan.slots)
    monkeypatch.setattr(
        summary,
        "read_quality_selection_v2_campaign_inputs",
        lambda **_kwargs: campaign_inputs,
    )
    monkeypatch.setattr(summary, "build_v2_evidence_plan", lambda *_: plan)
    monkeypatch.setattr(
        summary,
        "audit_quality_selection_v2_completed_pacing",
        lambda **_kwargs: "e" * 64,
    )
    monkeypatch.setattr(
        summary,
        "audit_completed_formal_campaign",
        lambda **_kwargs: tuple(records),
    )
    return manifest, manifest_path


def test_summary_publishes_only_complete_protocol_valid_winner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, manifest_path = _fixture(monkeypatch, tmp_path)

    payload = summary.summarize(tmp_path, manifest_path)

    output = tmp_path / manifest["summary_output_path"]
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert payload["status"] == "winner_selected"
    assert payload["winner"] == "v4-pro"
    assert payload["planned_starts"] == payload["validated_starts"] == 20
    assert payload["selected_profile"] == {
        "provider": "opencode-go",
        "requested_model": "deepseek-v4-pro",
        "observed_model": "deepseek-v4-pro",
        "max_steps": 128,
        "model_request_max_attempts": 1,
        "cost_budget_usd": None,
    }
    assert payload["supporting_usage"]["v4-pro"] == {
        "tokens": 1000,
        "estimated_cost_usd": "0.10",
    }
    assert payload["supporting_usage"]["glm"] == {
        "tokens": 1000,
        "estimated_cost_usd": "0.10",
    }


def test_summary_copies_execution_limits_from_validated_frozen_formal_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, manifest_path = _fixture(monkeypatch, tmp_path)
    manifest["fixed_argv"][manifest["fixed_argv"].index("--max-steps") + 1] = "257"
    manifest["fixed_argv"][
        manifest["fixed_argv"].index("--model-request-max-attempts") + 1
    ] = "3"

    payload = summary.summarize(tmp_path, manifest_path)

    assert payload["selected_profile"]["max_steps"] == 257
    assert payload["selected_profile"]["model_request_max_attempts"] == 3


def test_summary_refuses_audit_failure_without_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, manifest_path = _fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(
        summary,
        "audit_completed_formal_campaign",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("last slot invalid")),
    )

    with pytest.raises(RuntimeError, match="last slot invalid"):
        summary.summarize(tmp_path, manifest_path)

    assert not (tmp_path / manifest["summary_output_path"]).exists()


def test_summary_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, manifest_path = _fixture(monkeypatch, tmp_path)
    output = tmp_path / manifest["summary_output_path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('{"status":"existing"}\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="already exists"):
        summary.summarize(tmp_path, manifest_path)

    assert json.loads(output.read_text(encoding="utf-8")) == {"status": "existing"}

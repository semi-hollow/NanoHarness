#!/usr/bin/env python3
"""执行冻结的 Golden-10 v2 双候选质量选择。

本入口只负责编排；正式产物、无重跑状态、镜像租约与动态证据均由共享
application 服务校验。任何槽位或基础设施异常都会停止剩余分母且不选胜者。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Mapping, Sequence

from agent_forge.bench.adapters.campaign_files import (
    AppendOnlyJsonlLedger,
    FileCampaignJournal,
    GitSourceIdentity,
)
from agent_forge.bench.adapters.docker_images import (
    ColimaDockerDataFreeSpaceProbe,
    DockerExactImageRuntime,
)
from agent_forge.bench.application.campaign_lifecycle import (
    FreeSpaceGuardedExactImageRuntime,
)
from agent_forge.bench.application.formal_campaign import (
    FormalCampaignRunner,
    audit_completed_formal_campaign,
)
from agent_forge.bench.application.image_sealer import SequentialImageSealer
from agent_forge.bench.application.quality_selection_v2 import (
    QualitySelectionV2FormalLauncher,
    QualitySelectionV2Preflight,
    slots_from_manifest,
    verify_quality_selection_v2_readiness,
)
from agent_forge.bench.application.quality_selection_v2_evidence import (
    build_v2_evidence_plan,
)
from agent_forge.bench.application.quality_selection_v2_seal import (
    seal_quality_selection_v2_campaign_inputs,
    verify_quality_selection_v2_credentials,
    verify_quality_selection_v2_source,
)

try:
    from scripts.summarize_quality_selection_v2 import validate_preregistration
except ModuleNotFoundError:  # 直接以脚本执行时项目根仍在 sys.path。
    from summarize_quality_selection_v2 import (  # type: ignore[no-redef]
        validate_preregistration,
    )


DEFAULT_MANIFEST = Path(
    "benchmarks/showcase/quality-selection-command-manifest-v2.json"
)


def execute_campaign(root: Path, manifest_path: Path) -> dict[str, object]:
    """在一个不可恢复重跑的进程中执行资格门与二十个正式槽位。"""

    project_root = root.resolve()
    manifest_file = _under(project_root, manifest_path)
    manifest = validate_preregistration(project_root, manifest_path)
    _require_pristine_dynamic_outputs(project_root, manifest)
    source_reader = GitSourceIdentity(project_root)

    # 失败关闭的静态门先于一次性 ledger；误触发 NO-GO 不应消耗正式身份。
    verify_quality_selection_v2_source(
        project_root, manifest_file, manifest, source_reader=source_reader
    )
    verify_quality_selection_v2_credentials(manifest)
    verify_quality_selection_v2_readiness(project_root, manifest["readiness_path"])

    journal = FileCampaignJournal(project_root)
    ledger = AppendOnlyJsonlLedger(project_root, manifest["ledger_path"])
    docker_runtime = DockerExactImageRuntime()
    free_space = ColimaDockerDataFreeSpaceProbe()
    minimum_free_bytes = int(
        manifest["prelaunch"]["docker_data_free_space"]["minimum_free_bytes"]
    )
    image_sealer = SequentialImageSealer(
        journal,
        manifest["image_seal_state_path"],
        docker_runtime,
        minimum_free_bytes=minimum_free_bytes,
        free_space_probe=free_space,
    )

    def source_gate(_policy: Mapping[str, Any]) -> None:
        verify_quality_selection_v2_source(
            project_root, manifest_file, manifest, source_reader=source_reader
        )

    def credential_gate(_policy: Mapping[str, Any]) -> None:
        verify_quality_selection_v2_credentials(manifest)

    preflight = QualitySelectionV2Preflight(
        project_root=project_root,
        manifest=manifest,
        readiness_path=Path(manifest["readiness_path"]),
        source_gate=source_gate,
        credential_gate=credential_gate,
        image_sealer=image_sealer,
        run_command=lambda argv: _run(argv, project_root),
        clock=monotonic,
        append_event=lambda event: ledger.append(dict(event)),
        wait=sleep,
    )
    preflight.qualify()
    if ledger.next_sequence != manifest["preflight_ledger_last_sequence"] + 1:
        raise RuntimeError("quality-selection v2 preflight ledger is incomplete")

    campaign_inputs = seal_quality_selection_v2_campaign_inputs(
        project_root=project_root,
        manifest_path=manifest_file,
        manifest=manifest,
        readiness_path=Path(manifest["readiness_path"]),
        image_seal_path=Path(manifest["image_seal_state_path"]),
        output_path=Path(manifest["campaign_inputs_path"]),
        source_reader=source_reader,
    )
    plan = build_v2_evidence_plan(
        project_root,
        manifest_file,
        manifest,
        campaign_inputs.campaign_inputs_path,
    )
    quality_slots = slots_from_manifest(manifest, project_root)
    formal_launcher = QualitySelectionV2FormalLauncher(
        slots=quality_slots,
        minimum_seconds=int(
            manifest["pacing"]["minimum_seconds_between_provider_commands"]
        ),
        run_command=lambda argv: _run(argv, project_root),
        clock=monotonic,
        append_event=lambda event: ledger.append(dict(event)),
        initial_sequence=int(manifest["preflight_ledger_last_sequence"]),
        wait=sleep,
    )
    guarded_runtime = FreeSpaceGuardedExactImageRuntime(
        docker_runtime,
        minimum_free_bytes=minimum_free_bytes,
        free_space_probe=free_space,
    )
    runner = FormalCampaignRunner(
        journal=journal,
        state_root=manifest["campaign_state_root"],
        campaign_id=plan.campaign_id,
        identity_sha256=plan.identity_sha256,
        slot_ids=tuple(slot.slot_id for slot in plan.slots),
        image_runtime=guarded_runtime,
        source_reader=source_reader,
        expected_launch_source=plan.expected_launch_source,
        launch_command=formal_launcher,
    )
    for index in range(0, len(plan.slots), 2):
        records = runner.run_group(plan.slots[index : index + 2])
        if len(records) != 2 or any(record.status != "validated" for record in records):
            raise RuntimeError("quality-selection v2 stopped at an invalid formal pair")

    audited = audit_completed_formal_campaign(
        journal=journal,
        state_root=manifest["campaign_state_root"],
        campaign_id=plan.campaign_id,
        identity_sha256=plan.identity_sha256,
        slots=plan.slots,
        expected_launch_source=plan.expected_launch_source,
    )
    if (
        len(audited) != 20
        or ledger.next_sequence != manifest["completed_ledger_last_sequence"] + 1
    ):
        raise RuntimeError("quality-selection v2 terminal denominator is incomplete")
    return {
        "schema_version": 1,
        "status": "formal_campaign_complete_pending_independent_summary",
        "campaign_id": plan.campaign_id,
        "campaign_identity_sha256": plan.identity_sha256,
        "planned_starts": 20,
        "validated_starts": len(audited),
        "pacing_events": ledger.next_sequence - 1,
    }


def _require_pristine_dynamic_outputs(root: Path, manifest: Mapping[str, Any]) -> None:
    paths = [
        manifest["ledger_path"],
        manifest["image_seal_state_path"],
        manifest["campaign_inputs_path"],
        manifest["campaign_state_root"],
        manifest["summary_output_path"],
        *(
            item["output_root"]
            for item in manifest["commands"]
            if isinstance(item, dict)
        ),
        *(
            _flag(item["argv"], "--output")
            for item in [
                *manifest["capability_probes"],
                *manifest["qualification_commands"],
            ]
        ),
    ]
    paths.extend(
        Path(_flag(item["argv"], "--output")).with_suffix("")
        for item in manifest["qualification_commands"]
    )
    collisions = [str(path) for path in paths if _under(root, path).exists()]
    if collisions:
        raise RuntimeError("quality-selection v2 dynamic output already exists")
    readiness = _under(root, manifest["readiness_path"])
    if not readiness.is_file():
        raise RuntimeError("quality-selection v2 launch readiness is missing")


def _run(argv: Sequence[str], root: Path) -> int:
    return subprocess.run(tuple(argv), cwd=root, check=False).returncode


def _flag(argv: Sequence[str], name: str) -> str:
    positions = [index for index, value in enumerate(argv) if value == name]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise RuntimeError(f"quality-selection v2 command flag drift: {name}")
    return str(argv[positions[0] + 1])


def _under(root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise RuntimeError(f"quality-selection v2 path escapes project root: {raw}")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--exclusive-subscription-window", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    manifest = validate_preregistration(root, args.manifest)
    if args.validate_only:
        print(
            json.dumps(
                {
                    "manifest_id": manifest["manifest_id"],
                    "planned_starts": manifest["planned_starts"],
                    "runner_dependency_ready": True,
                    "valid": True,
                },
                sort_keys=True,
            )
        )
        return
    if not args.execute or not args.exclusive_subscription_window:
        raise SystemExit(
            "--execute and --exclusive-subscription-window are both required"
        )
    print(json.dumps(execute_campaign(root, args.manifest), sort_keys=True))


if __name__ == "__main__":
    main()

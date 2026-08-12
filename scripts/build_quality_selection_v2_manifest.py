#!/usr/bin/env python3
"""Freeze the outcome-blind quality-selection-v2 command manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_DEFAULT = Path(__file__).parents[1]
PROTOCOL = Path("benchmarks/showcase/quality-selection-protocol-v2.json")
GOLDEN = Path("benchmarks/regression/golden-10-v2.json")
IMAGE_PLAN = Path("benchmarks/showcase/quality-selection-image-plan-v2.json")
CAPABILITY_PROBE = Path("scripts/probe_model_tool_contract.py")
CAPACITY_PROBE = Path("scripts/probe_model_rate_limit_contract.py")
BUILDER = Path("scripts/build_quality_selection_v2_manifest.py")
EXPORTER = Path("scripts/export_showcase_datasets.py")
SUMMARIZER = Path("scripts/summarize_quality_selection_v2.py")
RUNNER = Path("scripts/run_quality_selection_v2.py")
PROVENANCE_VERIFIER = Path("scripts/verify_golden_10_v2_provenance.py")
SKILL = Path("agent_forge/skills/packages/swebench_repair/SKILL.md")
LAUNCHER = Path(".venv/bin/forge")
SHARED_IMPLEMENTATION = (
    Path("agent_forge/bench/formal_artifacts.py"),
    Path("agent_forge/bench/application/campaign_lifecycle.py"),
    Path("agent_forge/bench/application/formal_campaign.py"),
    Path("agent_forge/bench/application/formal_selection.py"),
    Path("agent_forge/bench/application/image_sealer.py"),
    Path("agent_forge/bench/application/quality_selection_v2.py"),
    Path("agent_forge/bench/application/quality_selection_v2_evidence.py"),
    Path("agent_forge/bench/application/quality_selection_v2_seal.py"),
    Path("agent_forge/bench/adapters/campaign_files.py"),
    Path("agent_forge/bench/adapters/docker_images.py"),
    Path("agent_forge/bench/adapters/case_runtime.py"),
    Path("agent_forge/bench/application/swebench.py"),
    Path("agent_forge/bench/domain/config.py"),
    Path("agent_forge/bench/domain/models.py"),
    Path("agent_forge/bench/presentation/cli.py"),
    Path("agent_forge/bench/ports/campaign.py"),
    Path("agent_forge/evaluation/domain/scorecard.py"),
    Path("agent_forge/runtime/wiring.py"),
)
DEFAULT_ARTIFACT_ROOT = Path(".agent_forge/canonical-showcase/quality-selection-v2")
DEFAULT_OUTPUT = Path("benchmarks/showcase/quality-selection-command-manifest-v2.json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def _resolve_under(root: Path, value: Path) -> Path:
    resolved = (value if value.is_absolute() else root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {value}") from exc
    return resolved


def _argv_hash(commands: list[list[str]]) -> str:
    payload = json.dumps(
        commands,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candidate_pair(case_ordinal: int) -> tuple[str, str]:
    # Repeating ABBA blocks balance which candidate sees the earlier slot while
    # keeping the two starts for one Case adjacent on one evaluator-image window.
    return ("v4-pro", "glm") if (case_ordinal - 1) % 4 in {0, 3} else ("glm", "v4-pro")


def _fixed_argv(artifact_root: Path) -> list[str]:
    return [
        ".venv/bin/forge",
        "bench",
        "swebench",
        "--dataset",
        str(artifact_root / "dataset" / "official-cases.json"),
        "--cases-file",
        str(artifact_root / "dataset" / "agent-cases.json"),
        "--provider",
        "opencode-go",
        "--base-url",
        "https://opencode.ai/zen/go/v1",
        "--temperature",
        "0",
        "--thinking",
        "enabled",
        "--reasoning-effort",
        "high",
        "--max-steps",
        "128",
        "--max-context-chars",
        "64000",
        "--max-prompt-tokens",
        "131072",
        "--reserved-output-tokens",
        "16384",
        "--max-tool-calls-per-turn",
        "4",
        "--timeout-seconds",
        "3600",
        "--model-request-timeout-seconds",
        "600",
        "--model-request-max-attempts",
        "1",
        "--tool-execution-timeout-seconds",
        "600",
        "--repo-cache",
        ".agent_forge/bench/repos",
        "--evaluate",
        "--max-workers",
        "1",
        "--official-namespace",
        "swebench",
        "--official-cache-level",
        "instance",
        "--agent-mode",
        "single",
        "--max-revision-rounds",
        "0",
        "--tool-routing",
        "task-aware",
        "--skills",
        "swebench_repair",
        "--memory-recall-limit",
        "0",
        "--execution-mode",
        "worktree",
        "--network-policy",
        "deny",
        "--no-keep-worktree",
    ]


def compose_manifest(
    project_root: Path,
    artifact_root: Path,
    source_tag: str,
) -> dict[str, Any]:
    root = project_root.resolve()
    artifact_path = _resolve_under(root, artifact_root)
    artifact = artifact_path.relative_to(root)
    if artifact.parts[:1] != (".agent_forge",):
        raise ValueError("artifact root must be relative and under .agent_forge")
    if not source_tag or any(character.isspace() for character in source_tag):
        raise ValueError("source tag must be non-empty and contain no whitespace")
    paths = {
        "protocol": root / PROTOCOL,
        "golden": root / GOLDEN,
        "image_plan": root / IMAGE_PLAN,
        "capability_probe": root / CAPABILITY_PROBE,
        "capacity_probe": root / CAPACITY_PROBE,
        "builder": root / BUILDER,
        "exporter": root / EXPORTER,
        "summarizer": root / SUMMARIZER,
        "runner": root / RUNNER,
        "provenance_verifier": root / PROVENANCE_VERIFIER,
        "skill": root / SKILL,
        "launcher": root / LAUNCHER,
        "binding": root / artifact / "dataset-binding.json",
        "agent_dataset": root / artifact / "dataset" / "agent-cases.json",
        "official_dataset": root / artifact / "dataset" / "official-cases.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    missing.extend(
        str(path) for path in SHARED_IMPLEMENTATION if not (root / path).is_file()
    )
    if missing:
        raise ValueError("missing frozen input(s): " + ", ".join(missing))
    protocol = json.loads(paths["protocol"].read_text(encoding="utf-8"))
    golden = json.loads(paths["golden"].read_text(encoding="utf-8"))
    image_plan = json.loads(paths["image_plan"].read_text(encoding="utf-8"))
    case_ids = [str(item) for item in golden.get("case_ids") or []]
    if len(case_ids) != 10 or len(set(case_ids)) != 10:
        raise ValueError("Golden-10 v2 must contain exactly ten unique Case IDs")
    images = {
        str(item.get("instance_id") or ""): str(item.get("tag") or "")
        for item in image_plan.get("images") or []
        if isinstance(item, dict)
    }
    if set(images) != set(case_ids):
        raise ValueError("image plan must exactly cover Golden-10 v2")
    candidates = {
        str(item["candidate_id"]): {
            "provider": str(item["provider"]),
            "model": str(item["model"]),
        }
        for item in protocol["candidates"]
    }
    if list(candidates) != ["v4-pro", "glm"]:
        raise ValueError("v2 candidate order drift")

    fixed = _fixed_argv(artifact)
    commands: list[dict[str, Any]] = []
    composed_commands: list[list[str]] = []
    ordinal = 0
    for case_ordinal, instance_id in enumerate(case_ids, start=1):
        case_key = f"case-{case_ordinal:02d}"
        for pair_position, candidate_id in enumerate(
            _candidate_pair(case_ordinal), start=1
        ):
            ordinal += 1
            model = candidates[candidate_id]["model"]
            output_root = artifact / "formal" / candidate_id / case_key
            argv_suffix = [
                "--model",
                model,
                "--limit",
                "1",
                "--instance-id",
                instance_id,
                "--output-root",
                str(output_root),
            ]
            composed_commands.append([*fixed, *argv_suffix])
            commands.append(
                {
                    "ordinal": ordinal,
                    "case_ordinal": case_ordinal,
                    "pair_position": pair_position,
                    "candidate_id": candidate_id,
                    "shard": case_key,
                    "instance_ids": [instance_id],
                    "image": {
                        "instance_id": instance_id,
                        "tag": images[instance_id],
                    },
                    "output_root": str(output_root),
                    "argv_suffix": argv_suffix,
                }
            )

    base_url = "https://opencode.ai/zen/go/v1"
    runtime = protocol["fixed_runtime"]
    capability_probes = []
    for candidate_id, identity in candidates.items():
        capability_probes.append(
            {
                "candidate_id": candidate_id,
                "output_must_be_absent": True,
                "argv": [
                    ".venv/bin/python",
                    str(CAPABILITY_PROBE),
                    "--provider",
                    identity["provider"],
                    "--model",
                    identity["model"],
                    "--base-url",
                    base_url,
                    "--thinking",
                    runtime["thinking_mode"],
                    "--reasoning-effort",
                    runtime["reasoning_effort"],
                    "--timeout",
                    str(runtime["model_request_timeout_seconds"]),
                    "--max-attempts",
                    "1",
                    "--output",
                    str(artifact / "preflight" / f"{candidate_id}.json"),
                ],
            }
        )
    qualification_commands = []
    for item in protocol["provider_capacity_qualification"]["capacity_schedule"]:
        candidate_id, burst = item.split("/", 1)
        identity = candidates[candidate_id]
        qualification_commands.append(
            {
                "qualification_id": item,
                "candidate_id": candidate_id,
                "burst": burst,
                "output_must_be_absent": True,
                "argv": [
                    ".venv/bin/python",
                    str(CAPACITY_PROBE),
                    "--provider",
                    identity["provider"],
                    "--model",
                    identity["model"],
                    "--base-url",
                    base_url,
                    "--thinking",
                    runtime["thinking_mode"],
                    "--reasoning-effort",
                    runtime["reasoning_effort"],
                    "--timeout",
                    str(runtime["model_request_timeout_seconds"]),
                    "--max-attempts",
                    "1",
                    "--round-trips",
                    str(
                        protocol["provider_capacity_qualification"][
                            "native_tool_round_trips_per_burst"
                        ]
                    ),
                    "--capability-preflight",
                    str(artifact / "preflight" / f"{candidate_id}.json"),
                    "--output",
                    str(artifact / "qualification" / burst / f"{candidate_id}.json"),
                ],
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_id": "showcase-quality-v2-selection-commands",
        "status": "content_preregistered_source_tag_pending_launch_verification",
        "source_identity": {
            "binding": "external_annotated_git_tag",
            "expected_tag": source_tag,
            "target_rule": "the expected annotated tag must peel to launch HEAD containing this exact manifest",
            "require_clean_worktree_including_untracked": True,
        },
        "protocol_sha256": _sha256(paths["protocol"]),
        "capability_probe_script_sha256": _sha256(paths["capability_probe"]),
        "capacity_probe_script_sha256": _sha256(paths["capacity_probe"]),
        "manifest_builder_script_sha256": _sha256(paths["builder"]),
        "dataset_exporter_script_sha256": _sha256(paths["exporter"]),
        "selection_summarizer_script_sha256": _sha256(paths["summarizer"]),
        "campaign_runner_script_sha256": _sha256(paths["runner"]),
        "development_set_provenance_verifier_sha256": _sha256(
            paths["provenance_verifier"]
        ),
        "development_set_manifest_sha256": _sha256(paths["golden"]),
        "dataset_binding_sha256": _sha256(paths["binding"]),
        "agent_dataset_sha256": _sha256(paths["agent_dataset"]),
        "official_dataset_sha256": _sha256(paths["official_dataset"]),
        "image_manifest_sha256": _sha256(paths["image_plan"]),
        "skill_file_sha256": _sha256(paths["skill"]),
        "launcher_wrapper_sha256": _sha256(paths["launcher"]),
        "shared_implementation_sha256": {
            str(path): _sha256(root / path) for path in SHARED_IMPLEMENTATION
        },
        "artifact_root": str(artifact),
        "output_roots_must_be_absent": True,
        "planned_starts": 20,
        "credential_preflight": {
            "launcher_shell": "zsh -lic",
            "required_present_nonempty": "OPENCODE_GO_API_KEY",
            "required_absent": "AGENT_FORGE_API_KEY",
            "forbidden_fallback_sources": [
                "DEEPSEEK_API_KEY",
                "OPENAI_API_KEY",
            ],
            "resolver_required_credential_source": "OPENCODE_GO_API_KEY",
            "record_key_value": False,
        },
        "prelaunch": {
            "exclusive_subscription_window_operator_assertion_required": True,
            "docker_data_free_space": {
                "minimum_free_bytes": 26843545600,
                "probe_argv": [
                    "colima",
                    "ssh",
                    "--",
                    "df",
                    "-Pk",
                    "/var/lib/docker",
                ],
                "parser": "POSIX df -Pk final data row available 1K blocks multiplied by 1024",
            },
            "registry_probe": "an outcome-blind sequential pull/inspect pass must seal each exact tag's RepoDigest, local image ID, and linux/amd64 platform, then remove only a newly pulled campaign-owned exact tag",
            "all_exact_tags_must_be_pullable_before_provider_calls": True,
        },
        "pacing": {
            "initial_quiet_seconds": protocol["provider_capacity_qualification"][
                "initial_quiet_seconds"
            ],
            "minimum_seconds_between_provider_commands": protocol[
                "provider_capacity_qualification"
            ]["minimum_seconds_between_provider_commands"],
            "qualification_to_formal_cooldown_seconds": protocol[
                "provider_capacity_qualification"
            ]["qualification_to_formal_cooldown_seconds"],
        },
        "capability_probes": capability_probes,
        "qualification_commands": qualification_commands,
        "execution_schedule": {
            "mode": "serial_manifest_order",
            "max_concurrent_commands": 1,
            "unit": "one Case candidate slot",
            "candidate_pair_pattern": "ABBA repeating by Case",
            "image_window": "one exact Case tag shared by its two adjacent candidate starts",
            "command_order": [
                f"{item['candidate_id']}/{item['shard']}" for item in commands
            ],
        },
        "fixed_argv": fixed,
        "normalization": {
            "remove_flag_value_pairs": [
                "--instance-id",
                "--limit",
                "--model",
                "--output-root",
            ],
            "serialization": "compact UTF-8 JSON array containing the frozen fixed argv repeated twenty times",
        },
        "normalized_fixed_argv_sha256": _argv_hash([fixed] * len(commands)),
        "composed_commands_sha256": _argv_hash(composed_commands),
        "campaign_id": "showcase-quality-selection-v2",
        "readiness_path": str(artifact / "launch-readiness.json"),
        "image_seal_state_path": str(artifact / "prelaunch-image-seal.json"),
        "campaign_inputs_path": str(artifact / "campaign-inputs.json"),
        "campaign_state_root": str(artifact / "campaign-state"),
        "summary_output_path": str(artifact / "selection-summary.json"),
        "ledger_path": str(artifact / "pacing-ledger.jsonl"),
        "preflight_ledger_last_sequence": 20,
        "completed_ledger_last_sequence": 80,
        "commands": commands,
    }
    return manifest


def build_manifest(
    project_root: Path,
    artifact_root: Path,
    output: Path,
    source_tag: str,
) -> dict[str, Any]:
    root = project_root.resolve()
    destination = _resolve_under(root, output)
    manifest = compose_manifest(root, artifact_root, source_tag)
    _write_json(destination, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=PROJECT_DEFAULT)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source-tag",
        default="canonical-showcase-quality-selection-v2-preflight-20260812",
    )
    args = parser.parse_args()
    manifest = build_manifest(
        args.project_root,
        args.artifact_root,
        args.output,
        args.source_tag,
    )
    print(
        json.dumps(
            {
                "manifest_id": manifest["manifest_id"],
                "planned_starts": manifest["planned_starts"],
                "normalized_fixed_argv_sha256": manifest[
                    "normalized_fixed_argv_sha256"
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

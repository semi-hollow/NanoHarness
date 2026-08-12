from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).parents[1]


def _load(name: str, relative: str) -> Any:
    path = PROJECT_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILDER = _load(
    "quality_selection_v2_builder",
    "scripts/build_quality_selection_v2_manifest.py",
)
PROBE = _load(
    "quality_selection_v2_rate_probe",
    "scripts/probe_model_rate_limit_contract.py",
)
SUMMARIZER = _load(
    "quality_selection_v2_summarizer",
    "scripts/summarize_quality_selection_v2.py",
)
PROVENANCE = _load(
    "golden_10_v2_provenance",
    "scripts/verify_golden_10_v2_provenance.py",
)


def _json(relative: str) -> dict[str, Any]:
    value = json.loads((PROJECT_ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


class QualitySelectionV2AssetTest(unittest.TestCase):
    def test_fresh_cohort_is_unique_outcome_blind_and_disjoint(self) -> None:
        fresh = _json("benchmarks/regression/golden-10-v2.json")
        old = _json("benchmarks/regression/golden-10-v1.json")
        canonical = _json("benchmarks/showcase/canonical-50-v1.json")
        ids = fresh["case_ids"]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 10)
        self.assertEqual(len({item.split("__", 1)[0] for item in ids}), 10)
        self.assertFalse(set(ids) & set(old["case_ids"]))
        self.assertFalse(set(ids) & set(canonical["case_ids"]))
        self.assertTrue(fresh["selection_provenance"]["outcome_blind"])
        self.assertFalse(
            fresh["selection_provenance"]["old_quality_selection_artifacts_used"]
        )
        provenance = fresh["selection_provenance"]
        self.assertEqual(provenance["allowed_fields"], ["instance_id", "repo"])
        self.assertEqual(provenance["seed"], "nanoharness-golden-dev-10-v2")
        source_paths = [
            PROJECT_ROOT / item["path"]
            for item in provenance["remaining_pool"]["exclusion_sources"]
        ]
        for claim, path in zip(
            provenance["remaining_pool"]["exclusion_sources"],
            source_paths,
            strict=True,
        ):
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), claim["file_sha256"]
            )
        self.assertEqual(
            hashlib.sha256("\n".join(ids).encode()).hexdigest(),
            fresh["ordered_case_ids_sha256"],
        )
        self.assertEqual([item["instance_id"] for item in fresh["selected_cases"]], ids)
        for item in fresh["selected_cases"]:
            self.assertEqual(
                PROVENANCE._rank(item["instance_id"])[0], item["rank_sha256"]
            )

    def test_protocol_separates_capacity_and_forbids_partial_comparison(self) -> None:
        protocol = _json("benchmarks/showcase/quality-selection-protocol-v2.json")
        capacity = protocol["provider_capacity_qualification"]
        infrastructure = protocol["provider_infrastructure_policy"]

        self.assertFalse(capacity["enters_formal_denominator"])
        self.assertEqual(capacity["capacity_bursts_per_candidate"], 2)
        self.assertEqual(capacity["native_tool_round_trips_per_burst"], 4)
        self.assertEqual(
            capacity["capacity_schedule"],
            [
                "v4-pro/burst-01",
                "glm/burst-01",
                "glm/burst-02",
                "v4-pro/burst-02",
            ],
        )
        self.assertEqual(protocol["execution"]["correctness_rerun"], 0)
        self.assertEqual(
            protocol["execution"]["model_request_max_attempts_per_llm_call"], 1
        )
        self.assertFalse(infrastructure["replacement_case_allowed"])
        self.assertFalse(infrastructure["correctness_rerun_allowed"])
        self.assertFalse(infrastructure["partial_candidate_comparison_allowed"])
        self.assertEqual(
            protocol["runner_dependency"]["status"],
            "shared_primitives_and_thin_v2_composition_ready_pending_launch_seal",
        )
        required = " ".join(protocol["runner_dependency"]["required_shared_primitives"])
        self.assertIn("FormalRunExpectation", required)
        self.assertIn("FormalCampaignRunner", required)
        self.assertIn("campaign-input seal", required)
        self.assertIn("FreeSpaceGuardedExactImageRuntime", required)
        available = " ".join(protocol["runner_dependency"]["available_now"])
        self.assertIn("validate_formal_run", available)
        self.assertIn("NoRerunSlotLifecycle", available)
        self.assertIn("ExactImageLease", available)
        blockers = " ".join(protocol["runner_dependency"]["prelaunch_blockers"])
        self.assertIn("weekly subscription readiness", blockers)
        self.assertIn("annotated prelaunch tag", blockers)
        self.assertIn("dynamic campaign-input seal", blockers)

    def test_image_plan_is_exact_and_has_no_ownership_guess(self) -> None:
        fresh = _json("benchmarks/regression/golden-10-v2.json")
        plan = _json("benchmarks/showcase/quality-selection-image-plan-v2.json")
        images = plan["images"]

        self.assertEqual([item["instance_id"] for item in images], fresh["case_ids"])
        self.assertEqual(len({item["tag"] for item in images}), 10)
        self.assertTrue(
            all(
                item["tag"].startswith("swebench/sweb.eval.x86_64.")
                and item["tag"].endswith(":latest")
                for item in images
            )
        )
        self.assertTrue(all("repo_digest" not in item for item in images))
        self.assertIn("RepoDigest", plan["prelaunch_seal_requirement"])
        self.assertIn("ExactImageLease", plan["case_window_requirement"])

    def test_manifest_builder_emits_case_paired_abba_single_case_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            artifact = Path(".agent_forge/canonical-showcase/quality-selection-v2")
            self._copy_builder_inputs(tmp, artifact)
            output = Path(
                "benchmarks/showcase/quality-selection-command-manifest-v2.json"
            )

            manifest = BUILDER.build_manifest(tmp, artifact, output, "annotated-v2")
            SUMMARIZER.validate_preregistration(tmp, output)

            commands = manifest["commands"]
            fixed_argv = manifest["fixed_argv"]
            self.assertEqual(len(commands), 20)
            self.assertEqual(
                [item["candidate_id"] for item in commands],
                [
                    "v4-pro",
                    "glm",
                    "glm",
                    "v4-pro",
                    "glm",
                    "v4-pro",
                    "v4-pro",
                    "glm",
                    "v4-pro",
                    "glm",
                    "glm",
                    "v4-pro",
                    "glm",
                    "v4-pro",
                    "v4-pro",
                    "glm",
                    "v4-pro",
                    "glm",
                    "glm",
                    "v4-pro",
                ],
            )
            for index in range(0, 20, 2):
                pair = commands[index : index + 2]
                self.assertEqual(pair[0]["instance_ids"], pair[1]["instance_ids"])
                self.assertEqual(pair[0]["image"], pair[1]["image"])
                self.assertNotEqual(pair[0]["candidate_id"], pair[1]["candidate_id"])
                for command in pair:
                    argv = [*fixed_argv, *command["argv_suffix"]]
                    self.assertEqual(command["instance_ids"].__len__(), 1)
                    self.assertEqual(self._flag_values(argv, "--limit"), ["1"])
                    self.assertEqual(
                        self._flag_values(argv, "--instance-id"),
                        command["instance_ids"],
                    )

    def test_launch_capacity_is_dynamic_and_never_a_selection_metric(self) -> None:
        protocol = _json("benchmarks/showcase/quality-selection-protocol-v2.json")
        capacity = protocol["provider_capacity_qualification"]

        self.assertIn("weekly", capacity["launch_capacity_gate"])
        self.assertIn("20 formal starts", capacity["launch_capacity_gate"])
        self.assertEqual(
            capacity["use_balance_policy"].split(";")[0], "must remain off"
        )
        self.assertNotIn("subscription_snapshot", protocol)
        self.assertNotIn(
            "quota", " ".join(protocol["selection_rule"]["tie_break_order"])
        )

    def test_preregistration_rejects_path_escape(self) -> None:
        with self.assertRaisesRegex(ValueError, "escapes project root"):
            SUMMARIZER._resolve_under(PROJECT_ROOT, "../outside.json")

    def test_strict_command_parser_rejects_duplicate_and_equals_flags(self) -> None:
        prefix = ["forge", "bench"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            SUMMARIZER._parse_flags(
                [*prefix, "--model", "a", "--model", "b"],
                prefix=prefix,
                value_flags={"--model"},
            )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            SUMMARIZER._parse_flags(
                [*prefix, "--model=a"],
                prefix=prefix,
                value_flags={"--model"},
            )

    def test_builder_rejects_output_and_artifact_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp = Path(temporary)
            artifact = Path(".agent_forge/canonical-showcase/quality-selection-v2")
            self._copy_builder_inputs(tmp, artifact)
            with self.assertRaisesRegex(ValueError, "escapes project root"):
                BUILDER.build_manifest(tmp, artifact, Path("../outside.json"), "tag")
            with self.assertRaisesRegex(ValueError, "escapes project root"):
                BUILDER.build_manifest(
                    tmp,
                    Path(".agent_forge/../../outside"),
                    Path("inside.json"),
                    "tag",
                )

    def test_rate_probe_contract_requires_first_attempt_consistent_identity(
        self,
    ) -> None:
        evidence = {
            "status": "passed",
            "provider": "provider-x",
            "requested_model": "model-a",
            "credential_source": "OPENCODE_GO_API_KEY",
            "observed_response_model": "observed-model",
            "round_trip_observed_response_model": "observed-model",
            "tool_call_source": "native",
            "tool_call_count": 1,
            "tool_arguments_match": True,
            "round_trip_completed": True,
            "fallback_used": False,
            "attempts_per_call": [2, 1],
            "error_codes": ["rate_limited"],
            "error_code": "",
        }
        failure = PROBE._round_failure(
            evidence,
            expected_observed_model="observed-model",
            provider="provider-x",
            model="model-a",
        )
        self.assertIn("attempts_per_call", failure)
        self.assertIn("error_codes", failure)
        evidence["attempts_per_call"] = [1, 1]
        evidence["error_codes"] = []
        self.assertEqual(
            PROBE._round_failure(
                evidence,
                expected_observed_model="observed-model",
                provider="provider-x",
                model="model-a",
            ),
            "",
        )

    def test_rate_probe_binds_clean_capability_observed_identity(self) -> None:
        base_url = "https://provider.invalid/v1"
        endpoint, digest = PROBE._safe_endpoint_identity(base_url)
        payload = {
            "schema_version": 1,
            "status": "passed",
            "provider": "provider-x",
            "requested_model": "model-a",
            "credential_source": "OPENCODE_GO_API_KEY",
            "base_url_origin_path": endpoint,
            "base_url_sha256": digest,
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            "tool_call_source": "native",
            "tool_call_count": 1,
            "tool_arguments_match": True,
            "round_trip_completed": True,
            "fallback_used": False,
            "attempts_per_call": [1, 1],
            "error_codes": [],
            "error_code": "",
            "observed_response_model": "provider-model-a",
            "round_trip_observed_response_model": "provider-model-a",
        }
        observed = PROBE._expected_identity(
            payload,
            provider="provider-x",
            model="model-a",
            base_url=base_url,
            thinking="enabled",
            reasoning_effort="high",
        )
        self.assertEqual(observed, "provider-model-a")
        payload["attempts_per_call"] = [2, 1]
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            PROBE._expected_identity(
                payload,
                provider="provider-x",
                model="model-a",
                base_url=base_url,
                thinking="enabled",
                reasoning_effort="high",
            )

    def test_rate_probe_missing_child_artifact_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight = root / "preflight.json"
            output = root / "qualification.json"
            preflight.write_text(
                json.dumps(self._clean_preflight("https://provider.invalid/v1")),
                encoding="utf-8",
            )
            argv = self._probe_argv(preflight, output)
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    PROBE.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=1),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "capacity round 1"):
                    PROBE.main()
            self.assertFalse(output.exists())

    def test_rate_probe_never_overwrites_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preflight = root / "preflight.json"
            output = root / "qualification.json"
            preflight.write_text(
                json.dumps(self._clean_preflight("https://provider.invalid/v1")),
                encoding="utf-8",
            )
            output.write_text("sealed", encoding="utf-8")
            with patch.object(sys, "argv", self._probe_argv(preflight, output)):
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    PROBE.main()
            self.assertEqual(output.read_text(encoding="utf-8"), "sealed")

    @staticmethod
    def _clean_preflight(base_url: str) -> dict[str, Any]:
        endpoint, digest = PROBE._safe_endpoint_identity(base_url)
        return {
            "schema_version": 1,
            "status": "passed",
            "provider": "provider-x",
            "requested_model": "model-a",
            "credential_source": "OPENCODE_GO_API_KEY",
            "base_url_origin_path": endpoint,
            "base_url_sha256": digest,
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            "tool_call_source": "native",
            "tool_call_count": 1,
            "tool_arguments_match": True,
            "round_trip_completed": True,
            "fallback_used": False,
            "attempts_per_call": [1, 1],
            "error_codes": [],
            "error_code": "",
            "observed_response_model": "provider-model-a",
            "round_trip_observed_response_model": "provider-model-a",
        }

    @staticmethod
    def _probe_argv(preflight: Path, output: Path) -> list[str]:
        return [
            "probe_model_rate_limit_contract.py",
            "--provider",
            "provider-x",
            "--model",
            "model-a",
            "--base-url",
            "https://provider.invalid/v1",
            "--capability-preflight",
            str(preflight),
            "--output",
            str(output),
        ]

    @staticmethod
    def _flag_values(argv: list[str], flag: str) -> list[str]:
        return [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == flag]

    @staticmethod
    def _copy_builder_inputs(tmp: Path, artifact: Path) -> None:
        paths = [
            "benchmarks/regression/golden-10-v2.json",
            "benchmarks/showcase/canonical-50-exclusions-v1.json",
            "benchmarks/showcase/canonical-50-v1.json",
            "benchmarks/showcase/quality-selection-protocol-v2.json",
            "benchmarks/showcase/quality-selection-image-plan-v2.json",
            "scripts/probe_model_tool_contract.py",
            "scripts/probe_model_rate_limit_contract.py",
            "scripts/build_quality_selection_v2_manifest.py",
            "scripts/export_showcase_datasets.py",
            "scripts/summarize_quality_selection_v2.py",
            "scripts/run_quality_selection_v2.py",
            "scripts/verify_golden_10_v2_provenance.py",
            "agent_forge/skills/packages/swebench_repair/SKILL.md",
            "agent_forge/bench/formal_artifacts.py",
            "agent_forge/bench/application/campaign_lifecycle.py",
            "agent_forge/bench/application/formal_campaign.py",
            "agent_forge/bench/application/formal_selection.py",
            "agent_forge/bench/application/image_sealer.py",
            "agent_forge/bench/application/quality_selection_v2.py",
            "agent_forge/bench/application/quality_selection_v2_evidence.py",
            "agent_forge/bench/application/quality_selection_v2_seal.py",
            "agent_forge/bench/adapters/campaign_files.py",
            "agent_forge/bench/adapters/docker_images.py",
            "agent_forge/bench/adapters/case_runtime.py",
            "agent_forge/bench/application/swebench.py",
            "agent_forge/bench/domain/config.py",
            "agent_forge/bench/domain/models.py",
            "agent_forge/bench/presentation/cli.py",
            "agent_forge/bench/ports/campaign.py",
            "agent_forge/evaluation/domain/scorecard.py",
            "agent_forge/runtime/wiring.py",
            ".venv/bin/forge",
        ]
        for relative in paths:
            source = PROJECT_ROOT / relative
            destination = tmp / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        payloads = {
            artifact / "dataset" / "agent-cases.json": b"AGENT",
            artifact / "dataset" / "official-cases.json": b"SEALED",
        }
        for relative, value in payloads.items():
            destination = tmp / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        golden_path = tmp / "benchmarks/regression/golden-10-v2.json"
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        agent_path = (tmp / artifact / "dataset" / "agent-cases.json").resolve()
        official_path = (tmp / artifact / "dataset" / "official-cases.json").resolve()
        binding_path = tmp / artifact / "dataset-binding.json"
        binding_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "mechanically_exported_no_hidden_values_printed",
                    "row_count": 10,
                    "ordered_case_ids": golden["case_ids"],
                    "manifest_path": str(golden_path.resolve()),
                    "manifest_sha256": hashlib.sha256(
                        golden_path.read_bytes()
                    ).hexdigest(),
                    "exporter_sha256": hashlib.sha256(
                        (tmp / "scripts/export_showcase_datasets.py").read_bytes()
                    ).hexdigest(),
                    "arrow_sha256": golden["selection_provenance"]["dataset"][
                        "arrow_sha256"
                    ],
                    "agent_output": str(agent_path),
                    "official_output": str(official_path),
                    "agent_sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
                    "official_sha256": hashlib.sha256(
                        official_path.read_bytes()
                    ).hexdigest(),
                    "agent_fields": [
                        "instance_id",
                        "repo",
                        "problem_statement",
                        "base_commit",
                        "version",
                        "environment_setup_commit",
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()

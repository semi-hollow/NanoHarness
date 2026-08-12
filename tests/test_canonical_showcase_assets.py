import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.domain.cohort import load_benchmark_cohort
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.presentation.cli import _resolve_local_input
from agent_forge.cli.parser import build_parser
from agent_forge.runtime.domain.conversation import AgentResponse, ToolCall
from scripts.build_canonical_showcase_cohort import _load_exclusions
from scripts.probe_model_tool_contract import (
    _probe_passed,
    _round_trip_passed,
    _safe_endpoint_identity,
)


PROJECT_ROOT = Path(__file__).parents[1]
SHOWCASE_ROOT = PROJECT_ROOT / "benchmarks" / "showcase"


def _ids_sha256(case_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(case_ids).encode("utf-8")).hexdigest()


def _normalized_command_argv(argv: list[str], flags: set[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] in flags:
            index += 2
            continue
        normalized.append(argv[index])
        index += 1
    return normalized


class CanonicalShowcaseAssetsTest(unittest.TestCase):
    def test_capability_probe_redacts_endpoint_credentials_and_query(self):
        endpoint, digest = _safe_endpoint_identity(
            "https://user:secret@example.test:8443/v1/?token=hidden#fragment"
        )

        self.assertEqual(endpoint, "https://example.test:8443/v1")
        self.assertEqual(len(digest), 64)
        self.assertNotIn("secret", endpoint)
        self.assertNotIn("hidden", endpoint)

    def test_capability_probe_requires_native_call_and_response_model_identity(self):
        tool_call = ToolCall(
            id="probe-1",
            name="probe_read_file",
            arguments={"path": "README.md"},
        )
        response = AgentResponse(
            content=None,
            tool_calls=[tool_call],
            normalization={"tool_call_source": "native"},
            observed_model="provider/model-build-1",
        )

        self.assertTrue(
            _probe_passed(
                response=response,
                tool_calls=response.tool_calls,
                fallback_used=False,
            )
        )
        response.observed_model = None
        self.assertFalse(
            _probe_passed(
                response=response,
                tool_calls=response.tool_calls,
                fallback_used=False,
            )
        )

    def test_capability_probe_requires_same_model_across_tool_round_trip(self):
        first = AgentResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="probe-1",
                    name="probe_read_file",
                    arguments={"path": "README.md"},
                )
            ],
            normalization={"tool_call_source": "native"},
            observed_model="provider/model-build-1",
        )
        final = AgentResponse(
            content="probe-complete",
            tool_calls=[],
            observed_model="provider/model-build-1",
        )

        self.assertTrue(
            _round_trip_passed(
                first_response=first,
                final_response=final,
                first_fallback_used=False,
                final_fallback_used=False,
            )
        )
        final.observed_model = "provider/model-build-2"
        self.assertFalse(
            _round_trip_passed(
                first_response=first,
                final_response=final,
                first_fallback_used=False,
                final_fallback_used=False,
            )
        )

    def test_canonical_50_is_presealed_unique_and_excludes_historical_sets(self):
        cohort_path = SHOWCASE_ROOT / "canonical-50-v1.json"
        cohort_payload = json.loads(cohort_path.read_text(encoding="utf-8"))
        exclusions_path = SHOWCASE_ROOT / "canonical-50-exclusions-v1.json"
        exclusions = json.loads(exclusions_path.read_text(encoding="utf-8"))

        cohort = load_benchmark_cohort(cohort_path)
        case_ids = list(cohort.case_ids)

        self.assertEqual(len(case_ids), 50)
        self.assertEqual(len(set(case_ids)), 50)
        self.assertEqual(
            _ids_sha256(case_ids),
            "d6c90605f9dfe5a78d7a19c285b2cf3c19645b0c5b1c4ebd4e3f31e979d53c4b",
        )
        self.assertTrue(set(case_ids).isdisjoint(exclusions["case_ids"]))
        self.assertEqual(len(exclusions["case_ids"]), 117)
        self.assertEqual(
            _ids_sha256(exclusions["case_ids"]),
            exclusions["ordered_case_ids_sha256"],
        )
        self.assertEqual(len(exclusions["source_provenance"]), 6)
        source_union: set[str] = set()
        for source in exclusions["source_provenance"]:
            source_case_ids = source["case_ids"]
            self.assertEqual(len(source_case_ids), source["case_count"])
            self.assertEqual(len(set(source_case_ids)), source["case_count"])
            self.assertEqual(
                _ids_sha256(source_case_ids),
                source["ordered_case_ids_sha256"],
            )
            source_union.update(source_case_ids)
        self.assertEqual(sorted(source_union), exclusions["case_ids"])
        self.assertEqual(
            hashlib.sha256(exclusions_path.read_bytes()).hexdigest(),
            cohort_payload["selection"]["exclusions_sha256"],
        )
        self.assertEqual(sum(cohort_payload["selection"]["repo_quotas"].values()), 50)
        self.assertEqual(
            cohort_payload["selection"]["allowed_fields"], ["instance_id", "repo"]
        )
        self.assertTrue(
            all(
                set(item) == {"instance_id", "repo"}
                for item in cohort_payload["selected_cases"]
            )
        )

    def test_exclusion_loader_rejects_source_tampering_before_sampling(self):
        exclusions_path = SHOWCASE_ROOT / "canonical-50-exclusions-v1.json"
        payload = json.loads(exclusions_path.read_text(encoding="utf-8"))
        loaded, _ = _load_exclusions(exclusions_path)
        self.assertEqual(loaded, set(payload["case_ids"]))

        with tempfile.TemporaryDirectory() as tmp:
            tampered_path = Path(tmp) / "exclusions.json"

            wrong_count = json.loads(json.dumps(payload))
            wrong_count["source_provenance"][0]["case_count"] -= 1
            tampered_path.write_text(json.dumps(wrong_count), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source count"):
                _load_exclusions(tampered_path)

            wrong_hash = json.loads(json.dumps(payload))
            wrong_hash["source_provenance"][0]["ordered_case_ids_sha256"] = "0" * 64
            tampered_path.write_text(json.dumps(wrong_hash), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source ID SHA-256"):
                _load_exclusions(tampered_path)

            wrong_union = json.loads(json.dumps(payload))
            source = wrong_union["source_provenance"][0]
            source["case_ids"][0] = "synthetic__not-in-sealed-union"
            source["ordered_case_ids_sha256"] = _ids_sha256(source["case_ids"])
            tampered_path.write_text(json.dumps(wrong_union), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source union"):
                _load_exclusions(tampered_path)

    def test_five_ordered_waves_exactly_cover_canonical_50(self):
        cohort = load_benchmark_cohort(SHOWCASE_ROOT / "canonical-50-v1.json")

        flattened: list[str] = []
        for shard in cohort.shard_order:
            selected = cohort.select_shard(shard)
            self.assertEqual(len(selected.case_ids), 10)
            flattened.extend(selected.case_ids)

        self.assertEqual(flattened, list(cohort.case_ids))

    def test_golden_is_seen_development_only_and_not_canonical(self):
        golden = json.loads(
            (
                PROJECT_ROOT / "benchmarks" / "regression" / "golden-10-v1.json"
            ).read_text(encoding="utf-8")
        )
        canonical = json.loads(
            (SHOWCASE_ROOT / "canonical-50-v1.json").read_text(encoding="utf-8")
        )

        self.assertEqual(len(golden["case_ids"]), 10)
        self.assertEqual(
            _ids_sha256(golden["case_ids"]), golden["ordered_case_ids_sha256"]
        )
        self.assertTrue(set(golden["case_ids"]).isdisjoint(canonical["case_ids"]))
        self.assertIn("not a holdout", " ".join(golden["claim_limits"]))

    def test_quality_protocol_uses_non_restrictive_separate_timeouts(self):
        protocol = json.loads(
            (SHOWCASE_ROOT / "quality-selection-protocol-v1.json").read_text(
                encoding="utf-8"
            )
        )
        runtime = protocol["fixed_runtime"]

        self.assertEqual(protocol["development_set"]["planned_total_starts"], 20)
        self.assertEqual(
            [item["model"] for item in protocol["candidates"]],
            [
                "deepseek-v4-pro",
                "glm-5.2",
            ],
        )
        self.assertEqual(runtime["max_steps"], 128)
        self.assertEqual(runtime["max_context_chars"], 64_000)
        self.assertEqual(runtime["max_prompt_tokens"], 131_072)
        self.assertEqual(runtime["reserved_output_tokens"], 16_384)
        self.assertEqual(runtime["run_timeout_seconds_per_case"], 3_600)
        self.assertEqual(runtime["model_request_timeout_seconds"], 600)
        self.assertEqual(runtime["tool_execution_timeout_seconds"], 600)
        self.assertIsNone(runtime["cost_budget_usd_per_case"])
        self.assertFalse(runtime["fallback_allowed"])

    def test_archived_quality_selection_commands_share_one_fixed_runtime(self):
        manifest = json.loads(
            (SHOWCASE_ROOT / "quality-selection-command-manifest-v1.json").read_text(
                encoding="utf-8"
            )
        )
        protocol_path = SHOWCASE_ROOT / "quality-selection-protocol-v1.json"
        self.assertEqual(
            hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
            manifest["protocol_sha256"],
        )
        # v1 已是 fail-closed 的历史实验，应认证当时 source tag 中的
        # probe/summarizer blob，而不是冻结今天仍在维护的脚本。
        source_tag = manifest["source_identity"]["expected_tag"]
        tagged_probe = subprocess.run(
            [
                "git",
                "show",
                f"{source_tag}:scripts/probe_model_tool_contract.py",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        ).stdout
        tagged_summarizer = subprocess.run(
            [
                "git",
                "show",
                f"{source_tag}:scripts/summarize_quality_selection.py",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(
            hashlib.sha256(tagged_probe).hexdigest(),
            manifest["capability_probe_script_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(tagged_summarizer).hexdigest(),
            manifest["selection_summarizer_script_sha256"],
        )
        self.assertEqual(
            [probe["candidate_id"] for probe in manifest["capability_probes"]],
            ["v4-pro", "glm"],
        )
        self.assertTrue(
            all(
                probe["output_must_be_absent"]
                for probe in manifest["capability_probes"]
            )
        )
        flags = set(manifest["normalization"]["remove_flag_value_pairs"])
        normalized = [
            _normalized_command_argv(command["argv"], flags)
            for command in manifest["commands"]
        ]
        serialized = json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertEqual(len(normalized), 6)
        self.assertTrue(all(argv == normalized[0] for argv in normalized))
        self.assertEqual(
            hashlib.sha256(serialized).hexdigest(),
            manifest["normalized_fixed_argv_sha256"],
        )
        self.assertEqual(
            sum(len(command["instance_ids"]) for command in manifest["commands"]),
            20,
        )

    def test_swebench_cli_records_provider_request_timeout(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "bench",
                "swebench",
                "--model-request-timeout-seconds",
                "300",
            ]
        )

        self.assertEqual(args.model_request_timeout_seconds, 300)
        self.assertEqual(
            SwebenchRunRequest(
                model_request_timeout_seconds=300
            ).model_request_timeout_seconds,
            300,
        )
        with self.assertRaisesRegex(ValueError, "must be positive"):
            SwebenchRunRequest(model_request_timeout_seconds=0)

    def test_local_dataset_path_is_frozen_before_official_evaluator_changes_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset = Path(tmp) / "official.json"
            dataset.write_text("[]\n", encoding="utf-8")

            self.assertEqual(_resolve_local_input(str(dataset)), str(dataset.resolve()))
            self.assertEqual(
                _resolve_local_input("princeton-nlp/SWE-bench_Verified"),
                "princeton-nlp/SWE-bench_Verified",
            )


if __name__ == "__main__":
    unittest.main()

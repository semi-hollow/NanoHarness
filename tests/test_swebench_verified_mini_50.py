import hashlib
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from agent_forge.bench.domain.cohort import load_benchmark_cohort
from agent_forge.bench.domain.campaign import (
    CampaignState,
    build_campaign_records,
    campaign_config_digest,
)
from scripts.run_swebench_verified_mini_50 import (
    COHORT_PATH,
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_MAX_PROMPT_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_RESERVED_OUTPUT_TOKENS,
    OPEN_CODE_GO_BASE_URL,
    _configure_swebench_harness,
    _freeze_or_validate_plan,
    build_campaign_request,
    build_final_publish_gate,
    build_frozen_plan,
    build_parser,
    main,
    render_plan,
)


PROJECT_ROOT = Path(__file__).parents[1]


class SwebenchVerifiedMini50Test(unittest.TestCase):
    def _args(self, *extra: str):
        return build_parser().parse_args(["--campaign-id", "mini50-test", *extra])

    @staticmethod
    def _create_harness_fixture(root: Path) -> Path:
        harness_root = root / "swebench-harness"
        entrypoint = harness_root / "swebench/harness/run_evaluation.py"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("# hermetic test fixture\n", encoding="utf-8")
        return harness_root

    def test_manifest_matches_published_fifty_case_contract(self):
        cohort = load_benchmark_cohort(COHORT_PATH)
        selected = cohort.select_shard("all")
        payload = json.loads(COHORT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(len(selected.case_ids), 50)
        self.assertEqual(len(set(selected.case_ids)), 50)
        self.assertEqual(selected.case_ids, cohort.case_ids)
        self.assertEqual(
            hashlib.sha256("\n".join(selected.case_ids).encode()).hexdigest(),
            "7874edd7eab06ed1be2e5033c1a0b5dc951272864d7dfa789d9cff39675386fc",
        )
        self.assertEqual(
            payload["source"]["revision"],
            "7b231a952828022a43977f21acfd452adda5088c",
        )

    def test_default_plan_is_quality_first_single_runtime_pass_at_one(self):
        request = build_campaign_request(self._args(), project_root=PROJECT_ROOT)
        benchmark = request.benchmark

        self.assertEqual(len(request.case_ids), 50)
        self.assertEqual(request.repetitions, 1)
        self.assertEqual(len(request.variants), 1)
        self.assertEqual(request.max_infrastructure_attempts, 1)
        self.assertTrue(request.resume)
        self.assertFalse(request.rerun_incomplete_slots)
        self.assertEqual(request.max_parallel_slots, 2)
        self.assertEqual(benchmark.provider, "opencode-go")
        self.assertEqual(benchmark.model, DEFAULT_MODEL)
        self.assertEqual(benchmark.base_url, OPEN_CODE_GO_BASE_URL)
        self.assertEqual(benchmark.reasoning_effort, "max")
        self.assertEqual(benchmark.max_steps, 128)
        self.assertEqual(benchmark.max_context_chars, DEFAULT_MAX_CONTEXT_CHARS)
        self.assertEqual(benchmark.max_prompt_tokens, DEFAULT_MAX_PROMPT_TOKENS)
        self.assertEqual(
            benchmark.reserved_output_tokens,
            DEFAULT_RESERVED_OUTPUT_TOKENS,
        )
        self.assertIsNone(benchmark.cost_budget_usd)
        self.assertTrue(benchmark.evaluate)
        self.assertEqual(benchmark.agent_mode, "single")
        self.assertEqual(benchmark.skill_names, ("swebench_repair",))
        self.assertEqual(benchmark.official_cache_level, "env")
        self.assertEqual(benchmark.official_platform, "linux/amd64")
        self.assertFalse(benchmark.keep_worktree)
        self.assertEqual(benchmark.network_policy, "deny")

    def test_validate_only_never_enters_benchmark_runner(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness_root = self._create_harness_fixture(Path(temporary))
            with mock.patch(
                "scripts.run_swebench_verified_mini_50.run_benchmark_campaign"
            ) as runner:
                exit_code = main(
                    [
                        "--campaign-id",
                        "mini50-validate",
                        "--output-root",
                        temporary,
                        "--swebench-harness-root",
                        str(harness_root),
                    ]
                )
            plan_path = Path(temporary) / "mini50-validate/frozen_plan.json"
            persisted = json.loads(plan_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        runner.assert_not_called()
        self.assertEqual(
            persisted["artifact_type"],
            "swebench_verified_mini_50_frozen_plan",
        )

    def test_execute_refuses_missing_opencode_go_key_without_fallback(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch(
                "scripts.run_swebench_verified_mini_50.run_benchmark_campaign"
            ) as runner,
            self.assertRaisesRegex(RuntimeError, "OPENCODE_GO_API_KEY is missing"),
        ):
            main(["--campaign-id", "mini50-execute", "--execute"])

        runner.assert_not_called()

    def test_execute_refuses_missing_official_harness_before_model_calls(self):
        with (
            mock.patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "present"}, clear=True),
            mock.patch(
                "scripts.run_swebench_verified_mini_50.run_benchmark_campaign"
            ) as runner,
            self.assertRaisesRegex(RuntimeError, "official harness is missing"),
        ):
            main(
                [
                    "--campaign-id",
                    "mini50-missing-harness",
                    "--swebench-harness-root",
                    ".agent_forge/does-not-exist",
                    "--execute",
                ]
            )

        runner.assert_not_called()

    def test_official_harness_root_is_propagated_to_child_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entrypoint = root / "swebench/harness/run_evaluation.py"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PYTHONPATH": "existing"}, clear=True):
                _configure_swebench_harness(root)
                self.assertEqual(
                    os.environ["PYTHONPATH"].split(os.pathsep),
                    [str(root.resolve()), "existing"],
                )

    def test_rendered_plan_contains_no_credential_or_full_benchmark_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = build_campaign_request(self._args(), project_root=PROJECT_ROOT)
            frozen_plan = build_frozen_plan(
                request,
                swebench_harness_root=self._create_harness_fixture(Path(temporary)),
            )
            rendered = render_plan(request, execute=False, frozen_plan=frozen_plan)

        self.assertIn('"case_count": 50', rendered)
        self.assertIn('"paid_model_calls_started": false', rendered)
        self.assertIn('"max_context_chars": 64000', rendered)
        self.assertIn('"max_prompt_tokens": 131072', rendered)
        self.assertIn('"reserved_output_tokens": 16384', rendered)
        self.assertIn("full 500-case SWE-bench Verified leaderboard score", rendered)
        self.assertNotIn("api_key", rendered.lower())

    def test_frozen_plan_is_create_once_and_rejects_identity_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            harness_root = self._create_harness_fixture(Path(temporary))
            request = build_campaign_request(
                self._args("--output-root", temporary),
                project_root=PROJECT_ROOT,
            )
            plan = build_frozen_plan(
                request,
                swebench_harness_root=harness_root,
            )
            first = _freeze_or_validate_plan(request, plan)
            second = _freeze_or_validate_plan(request, plan)
            drifted = json.loads(json.dumps(plan))
            drifted["model_identity"]["max_steps"] = 129

            self.assertEqual(first, second)
            with self.assertRaisesRegex(RuntimeError, "frozen Mini-50 plan drift"):
                _freeze_or_validate_plan(request, drifted)

    def test_final_publish_gate_accepts_empty_as_terminal_unresolved(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = build_campaign_request(self._args(), project_root=PROJECT_ROOT)
            plan = build_frozen_plan(
                request,
                swebench_harness_root=self._create_harness_fixture(Path(temporary)),
            )
            plan["source_identity"] = {
                **plan["source_identity"],
                "dirty": False,
                "working_tree_sha256": "",
            }
            records = build_campaign_records(request)
            for record in records:
                record.status = "completed"
                record.attempts = 1
                record.evidence = {
                    "patch_generated": False,
                    "official_evaluation_status": "official_eval_skipped_empty_patch",
                    "failure_class": "no_candidate_patch",
                }
            source = dict(plan["source_identity"])
            state = CampaignState(
                campaign_id=request.campaign_id,
                config_digest=campaign_config_digest(request.identity(), source),
                config=request.identity(),
                source=source,
                created_at="2026-08-15T00:00:00+00:00",
                updated_at="2026-08-15T00:00:00+00:00",
                records=records,
                status="completed",
            )

            gate = build_final_publish_gate(
                state,
                request=request,
                frozen_plan=plan,
                final_plan=plan,
            )
            records[0].evidence["failure_class"] = "provider_transport_error"
            refused = build_final_publish_gate(
                state,
                request=request,
                frozen_plan=plan,
                final_plan=plan,
            )

        self.assertTrue(gate["publishable"])
        self.assertEqual(gate["headline"], "0/50")
        self.assertEqual(gate["empty_patch"], 50)
        self.assertFalse(refused["publishable"])
        self.assertEqual(refused["provider_infra"], 1)

    def test_shared_run_configuration_executes_the_guarded_entrypoint(self):
        configuration_path = (
            PROJECT_ROOT
            / ".run/NanoHarness Benchmark - SWE-bench Verified Mini 50.run.xml"
        )
        configuration = ET.parse(configuration_path).getroot().find("configuration")
        assert configuration is not None
        options = {
            item.attrib["name"]: item.attrib.get("value", "")
            for item in configuration.findall("option")
        }

        self.assertEqual(
            options["SCRIPT_NAME"],
            "$PROJECT_DIR$/scripts/run_swebench_verified_mini_50.py",
        )
        self.assertEqual(options["PARAMETERS"], "--execute")
        self.assertEqual(options["WORKING_DIRECTORY"], "$PROJECT_DIR$")


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from agent_forge.bench.domain.cohort import load_benchmark_cohort
from scripts.run_swebench_verified_mini_50 import (
    COHORT_PATH,
    OPEN_CODE_GO_BASE_URL,
    build_campaign_request,
    build_parser,
    main,
    render_plan,
)


PROJECT_ROOT = Path(__file__).parents[1]


class SwebenchVerifiedMini50Test(unittest.TestCase):
    def _args(self, *extra: str):
        return build_parser().parse_args(["--campaign-id", "mini50-test", *extra])

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
        self.assertEqual(benchmark.provider, "opencode-go")
        self.assertEqual(benchmark.model, "deepseek-v4-pro")
        self.assertEqual(benchmark.base_url, OPEN_CODE_GO_BASE_URL)
        self.assertEqual(benchmark.reasoning_effort, "max")
        self.assertEqual(benchmark.max_steps, 128)
        self.assertIsNone(benchmark.cost_budget_usd)
        self.assertTrue(benchmark.evaluate)
        self.assertEqual(benchmark.agent_mode, "single")
        self.assertEqual(benchmark.skill_names, ("swebench_repair",))
        self.assertEqual(benchmark.official_cache_level, "env")
        self.assertFalse(benchmark.keep_worktree)
        self.assertEqual(benchmark.network_policy, "deny")

    def test_validate_only_never_enters_benchmark_runner(self):
        with mock.patch(
            "scripts.run_swebench_verified_mini_50.run_benchmark_campaign"
        ) as runner:
            exit_code = main(["--campaign-id", "mini50-validate"])

        self.assertEqual(exit_code, 0)
        runner.assert_not_called()

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

    def test_rendered_plan_contains_no_credential_or_full_benchmark_claim(self):
        request = build_campaign_request(self._args(), project_root=PROJECT_ROOT)
        rendered = render_plan(request, execute=False)

        self.assertIn('"case_count": 50', rendered)
        self.assertIn('"paid_model_calls_started": false', rendered)
        self.assertIn("full 500-case SWE-bench Verified leaderboard score", rendered)
        self.assertNotIn("api_key", rendered.lower())

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

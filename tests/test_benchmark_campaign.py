import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agent_forge.bench.adapters.campaign_files import FileCampaignArtifacts
from agent_forge.bench.application.campaign import RunBenchmarkCampaign
from agent_forge.bench.domain.campaign import (
    BenchmarkCampaignRequest,
    CampaignVariant,
    build_campaign_records,
)
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCaseResult, BenchRunSummary


PROJECT_ROOT = Path(__file__).parents[1]


class _SourceIdentity:
    def __init__(self, *, dirty: bool = False) -> None:
        self.dirty = dirty

    def read(self):
        return {
            "revision": "abc123",
            "branch": "test",
            "dirty": self.dirty,
            "working_tree_sha256": "dirty-digest" if self.dirty else "",
        }


class _FakeBenchmarkRunner:
    def __init__(self) -> None:
        self.requests = []
        self.fail_once_for = ""
        self.failed = False
        self.structured_failures_remaining: dict[str, int] = {}

    def __call__(self, request):
        self.requests.append(request)
        if request.tool_routing_mode == self.fail_once_for and not self.failed:
            self.failed = True
            raise RuntimeError("temporary provider failure /Users/private/key")
        index = len(self.requests)
        run_dir = Path(request.output_root) / f"swebench-fake-{index}"
        run_dir.mkdir(parents=True, exist_ok=True)
        case_id = request.instance_ids[0]
        remaining = self.structured_failures_remaining.get(
            request.tool_routing_mode,
            0,
        )
        structured_failure = remaining > 0
        if structured_failure:
            self.structured_failures_remaining[request.tool_routing_mode] = (
                remaining - 1
            )
        official = (
            "official_eval_skipped_empty_patch"
            if structured_failure
            else (
                "official_resolved"
                if request.tool_routing_mode == "task-aware"
                else "official_eval_failed"
            )
        )
        case_data = {
            "instance_id": case_id,
            "status": "blocked" if structured_failure else "patch_generated",
            "patch_generated": not structured_failure,
            "patch_chars": 0 if structured_failure else 12,
            "local_validation_status": "not_run" if structured_failure else "passed",
            "official_evaluation_status": official,
            "failure_class": (
                "provider_transport_error" if structured_failure else official
            ),
            "total_tokens": 100,
            "estimated_cost_usd": 0.01,
            "llm_latency_ms": 40,
            "tool_calls": 3,
            "failed_tool_calls": 0,
        }
        (run_dir / "scorecard.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "metadata": {
                        "provider": request.provider,
                        "requested_model": request.model,
                        "debug_note": (
                            "failed in /Users/private/repository "
                            "https://example.test/v1?token=private"
                        ),
                    },
                    "metrics": {},
                    "cases": [case_data],
                }
            ),
            encoding="utf-8",
        )
        trace = run_dir / "trace.json"
        patch = run_dir / "candidate_changes.diff"
        trace.write_text("{}", encoding="utf-8")
        patch.write_text(
            "" if structured_failure else "diff --git a/a.py b/a.py\n",
            encoding="utf-8",
        )
        result = BenchCaseResult(
            instance_id=case_id,
            repo="owner/repo",
            workspace=run_dir,
            trace_path=trace,
            usage_report_path=None,
            candidate_diff_path=patch,
            status="blocked" if structured_failure else "patch_generated",
            final_answer="request_timeout" if structured_failure else "candidate",
            patch_chars=0 if structured_failure else 12,
            local_validation_status="not_run" if structured_failure else "passed",
            official_evaluation_status=official,
            failure_class=(
                "provider_transport_error" if structured_failure else official
            ),
        )
        return BenchRunSummary(
            run_id=f"swebench-fake-{index}",
            dataset_name=request.dataset_name,
            split=request.split,
            provider=request.provider,
            model=request.model or "",
            output_dir=run_dir,
            predictions_path=run_dir / "predictions.jsonl",
            case_results=[result],
        )


class BenchmarkCampaignTest(unittest.TestCase):
    def _request(self, root: Path, *, repetitions: int = 2):
        return BenchmarkCampaignRequest(
            benchmark=SwebenchRunRequest(
                provider="deepseek",
                model="deepseek-test",
                base_url="https://api.example.test/v1?token=private",
                api_key="super-secret",
                evaluate=True,
            ),
            case_ids=("case-1", "case-2"),
            campaign_id="campaign-test",
            repetitions=repetitions,
            output_root=str(root / ".agent_forge/campaigns"),
            publish_root=str(root / "benchmarks/campaigns"),
        )

    def test_schedule_interleaves_variant_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = build_campaign_records(self._request(Path(tmp), repetitions=1))

        self.assertEqual(len(records), 4)
        self.assertEqual(
            [(record.case_id, record.variant) for record in records],
            [
                ("case-1", "minimal-control"),
                ("case-1", "governed-runtime"),
                ("case-2", "governed-runtime"),
                ("case-2", "minimal-control"),
            ],
        )

    def test_campaign_resumes_failed_slot_and_publishes_sanitized_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeBenchmarkRunner()
            runner.fail_once_for = "all"
            use_case = RunBenchmarkCampaign(
                runner,
                FileCampaignArtifacts(root),
                _SourceIdentity(),
                now=lambda: "2026-07-19T00:00:00+00:00",
            )
            request = self._request(root)

            first = use_case.run_campaign(request)
            second = use_case.run_campaign(request)

            self.assertEqual(first.state.status, "completed_with_failures")
            self.assertEqual(second.state.status, "completed")
            self.assertEqual(len(runner.requests), 9)
            retried = [
                record for record in second.state.records if record.attempts == 2
            ]
            self.assertEqual(len(retried), 1)
            summary = json.loads(second.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["paired_official"]["evaluated_pairs"], 4)
            self.assertEqual(
                summary["paired_official"]["wins"]["governed-runtime"],
                4,
            )

            published_bundle_dir = second.published_bundle_dir
            self.assertIsNotNone(published_bundle_dir)
            assert published_bundle_dir is not None
            manifest = json.loads(
                (published_bundle_dir / "manifest.json").read_text(encoding="utf-8")
            )
            first_record = manifest["records"][0]
            public_scorecard = (
                published_bundle_dir / "runs" / first_record["key"] / "scorecard.json"
            )
            self.assertEqual(
                first_record["scorecard_sha256"],
                hashlib.sha256(public_scorecard.read_bytes()).hexdigest(),
            )
            public_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in published_bundle_dir.rglob("*")
                if path.is_file()
            )
            public_summary = json.loads(
                (published_bundle_dir / "summary.json").read_text(encoding="utf-8")
            )

        self.assertNotIn("super-secret", public_text)
        self.assertNotIn("token=private", public_text)
        self.assertNotIn("/Users/private", public_text)
        self.assertNotIn(str(root), public_text)
        self.assertIn("runtime-preset", public_text)
        self.assertIn("Official resolved", public_text)
        self.assertIn("Repetition count is `2`", public_text)
        self.assertNotIn("Three repetitions", public_text)
        self.assertEqual(
            public_summary["variants"]["minimal-control"]["total_tokens"],
            400,
        )
        self.assertNotIn("api_key", public_text.lower())

    def test_structured_infrastructure_failure_is_retried_once_in_same_campaign(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeBenchmarkRunner()
            runner.structured_failures_remaining["all"] = 1
            result = RunBenchmarkCampaign(
                runner,
                FileCampaignArtifacts(root),
                _SourceIdentity(),
            ).run_campaign(self._request(root, repetitions=1))

        retried = [record for record in result.state.records if record.attempts == 2]
        self.assertEqual(result.state.status, "completed")
        self.assertEqual(len(runner.requests), 5)
        self.assertEqual(len(retried), 1)
        self.assertEqual(len(retried[0].attempt_history), 1)
        self.assertEqual(
            retried[0].attempt_history[0]["evidence"]["failure_class"],
            "provider_transport_error",
        )
        self.assertNotIn("infrastructure_retry_exhausted", retried[0].evidence)

    def test_persistent_infrastructure_failure_stops_after_two_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeBenchmarkRunner()
            runner.structured_failures_remaining["all"] = 10
            result = RunBenchmarkCampaign(
                runner,
                FileCampaignArtifacts(root),
                _SourceIdentity(),
            ).run_campaign(self._request(root, repetitions=1))
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))

        persistent = [
            record
            for record in result.state.records
            if record.evidence.get("infrastructure_retry_exhausted")
        ]
        self.assertEqual(result.state.status, "completed")
        self.assertEqual(len(runner.requests), 6)
        self.assertEqual(len(persistent), 2)
        self.assertTrue(all(record.attempts == 2 for record in persistent))
        self.assertEqual(
            summary["variants"]["minimal-control"]["infrastructure_failures"],
            2,
        )
        self.assertEqual(
            summary["paired_sample"]["excluded_infrastructure_pairs"],
            2,
        )

    def test_dirty_source_is_rejected_before_any_paid_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeBenchmarkRunner()
            use_case = RunBenchmarkCampaign(
                runner,
                FileCampaignArtifacts(root),
                _SourceIdentity(dirty=True),
            )
            with self.assertRaisesRegex(ValueError, "clean git source"):
                use_case.run_campaign(self._request(root, repetitions=1))

        self.assertEqual(runner.requests, [])

    def test_single_runtime_snapshot_disables_whole_case_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _FakeBenchmarkRunner()
            runner.structured_failures_remaining["task-aware"] = 10
            single_runtime = CampaignVariant(
                name="canonical-runtime",
                label="Canonical Runtime",
                description="Fixed Pass@1 snapshot.",
                tool_routing_mode="task-aware",
                skill_mode="auto",
                skill_names=("swebench_repair",),
            )
            request = replace(
                self._request(root, repetitions=1),
                variants=(single_runtime,),
                max_infrastructure_attempts=1,
            )
            result = RunBenchmarkCampaign(
                runner,
                FileCampaignArtifacts(root),
                _SourceIdentity(),
            ).run_campaign(request)
            summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
            report = result.report_path.read_text(encoding="utf-8")

        self.assertEqual(len(result.state.records), 2)
        self.assertEqual(len(runner.requests), 2)
        self.assertTrue(all(record.attempts == 1 for record in result.state.records))
        self.assertEqual(
            summary["variants"]["canonical-runtime"]["infrastructure_failures"],
            2,
        )
        self.assertEqual(
            summary["claim_boundary"]["comparison_factor"],
            "none; single pre-registered runtime snapshot",
        )
        self.assertIn("fixed-sample Pass@1 capability snapshot", report)
        self.assertNotIn("## Paired Official Outcomes", report)


if __name__ == "__main__":
    unittest.main()

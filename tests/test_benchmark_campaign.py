import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.bench.adapters.campaign_files import FileCampaignArtifacts
from agent_forge.bench.application.campaign import RunBenchmarkCampaign
from agent_forge.bench.domain.campaign import (
    BenchmarkCampaignRequest,
    build_campaign_records,
)
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCaseResult, BenchRunSummary


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

    def __call__(self, request):
        self.requests.append(request)
        if request.tool_routing_mode == self.fail_once_for and not self.failed:
            self.failed = True
            raise RuntimeError("temporary provider failure /Users/private/key")
        index = len(self.requests)
        run_dir = Path(request.output_root) / f"swebench-fake-{index}"
        run_dir.mkdir(parents=True, exist_ok=True)
        case_id = request.instance_ids[0]
        official = (
            "official_resolved"
            if request.tool_routing_mode == "task-aware"
            else "official_eval_failed"
        )
        case_data = {
            "instance_id": case_id,
            "status": "patch_generated",
            "patch_generated": True,
            "patch_chars": 12,
            "local_validation_status": "passed",
            "official_evaluation_status": official,
            "failure_class": official,
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
        patch = run_dir / "patch.diff"
        trace.write_text("{}", encoding="utf-8")
        patch.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
        result = BenchCaseResult(
            instance_id=case_id,
            repo="owner/repo",
            workspace=run_dir,
            trace_path=trace,
            usage_report_path=None,
            patch_path=patch,
            status="patch_generated",
            final_answer="candidate",
            patch_chars=12,
            local_validation_status="passed",
            official_evaluation_status=official,
            failure_class=official,
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

            first = use_case.execute(request)
            second = use_case.execute(request)

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

            public_dir = second.public_dir
            self.assertIsNotNone(public_dir)
            assert public_dir is not None
            manifest = json.loads(
                (public_dir / "manifest.json").read_text(encoding="utf-8")
            )
            first_record = manifest["records"][0]
            public_scorecard = (
                public_dir / "runs" / first_record["key"] / "scorecard.json"
            )
            self.assertEqual(
                first_record["scorecard_sha256"],
                hashlib.sha256(public_scorecard.read_bytes()).hexdigest(),
            )
            public_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in public_dir.rglob("*")
                if path.is_file()
            )

        self.assertNotIn("super-secret", public_text)
        self.assertNotIn("token=private", public_text)
        self.assertNotIn("/Users/private", public_text)
        self.assertNotIn(str(root), public_text)
        self.assertIn("runtime-preset", public_text)
        self.assertIn("Official resolved", public_text)

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
                use_case.execute(self._request(root, repetitions=1))

        self.assertEqual(runner.requests, [])


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_forge.workbench.adapters.evidence_files import FileEvidenceCatalog
from agent_forge.workbench.presentation.http import (
    INDEX_HTML,
    WORKBENCH_READ_ONLY_MESSAGE,
    _render_evidence_html,
)


class WorkbenchRunStoryTest(unittest.TestCase):
    def test_workbench_default_surface_is_read_only(self):
        self.assertIn('class="read-only status-collapsed', INDEX_HTML)
        self.assertIn("Three guided evidence scenes", INDEX_HTML)
        self.assertIn("1 Governed Run", INDEX_HTML)
        self.assertIn("2 Coordinated Agents", INDEX_HTML)
        self.assertIn("3 Evaluation Loop", INDEX_HTML)
        self.assertIn("loadEvidence('controls')", INDEX_HTML)
        self.assertIn("Workbench is read-only", WORKBENCH_READ_ONLY_MESSAGE)

    def test_run_evidence_prefers_canonical_run_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge" / "runs" / "run-canonical"
            run_dir.mkdir(parents=True)
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run-canonical",
                        "task": "canonical task",
                        "status": "completed",
                        "stop_reason": "final_answer",
                        "artifacts": [
                            {
                                "artifact_id": "patch",
                                "kind": "candidate_diff",
                                "relative_path": "candidate_changes.diff",
                                "producer_symbol": "ExecutionEnvironment.diff",
                                "flow_stage": "artifacts",
                                "semantic_consumers": ["local evaluator"],
                                "evidence_level": "candidate",
                                "proves": ["a candidate patch was produced"],
                                "does_not_prove": ["official benchmark resolution"],
                                "byte_size": 18,
                            },
                            {
                                "artifact_id": "local-report",
                                "kind": "local_report",
                                "relative_path": "local_report.json",
                                "producer_symbol": "LocalEvaluator.evaluate",
                                "flow_stage": "evidence",
                                "evidence_level": "local",
                                "proves": ["local checks were recorded"],
                                "does_not_prove": ["official benchmark resolution"],
                                "byte_size": 42,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "trace.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {"event_type": "turn_started"},
                            {"event_type": "tool_call"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "comparison.json").write_text(
                json.dumps({"task_id": "stale legacy task", "single_status": "failed"}),
                encoding="utf-8",
            )

            story = FileEvidenceCatalog(project_dir).latest_run_story()
            rendered = _render_evidence_html(project_dir, "evidence")

        self.assertIsNotNone(story)
        self.assertEqual(story.run_id, "run-canonical")
        self.assertLess(rendered.index("Run Story"), rendered.index("Runtime Pipeline"))
        self.assertIn("read-only evidence view", rendered)
        self.assertIn("canonical run_manifest.json", rendered)
        self.assertIn("canonical task", rendered)
        self.assertNotIn("stale legacy task", rendered)
        self.assertIn("ToolExecutionPipeline.execute_calls", rendered)
        self.assertIn("candidate_changes.diff", rendered)
        self.assertIn("Candidate evidence", rendered)
        self.assertIn("Local evidence", rendered)
        self.assertIn("Official evidence", rendered)
        self.assertIn("official benchmark resolution", rendered)

    def test_run_evidence_keeps_legacy_fallback_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            run_dir = project_dir / ".agent_forge" / "runs" / "run-legacy"
            run_dir.mkdir(parents=True)
            (run_dir / "comparison.json").write_text(
                json.dumps({"task_id": "legacy-only task", "single_status": "completed"}),
                encoding="utf-8",
            )

            catalog = FileEvidenceCatalog(project_dir)
            story = catalog.latest_run_story()
            rendered = _render_evidence_html(project_dir, "evidence")

        self.assertIsNone(story)
        self.assertIn("Canonical run_manifest.json is not available", rendered)
        self.assertIn("Legacy-compatible evidence follows", rendered)
        self.assertIn("legacy-only task", rendered)
        self.assertIn("Runtime Pipeline", rendered)
        self.assertIn("Claim Ladder", rendered)

    def test_explicit_latest_run_pointer_wins_over_stale_directory_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            runs = project_dir / ".agent_forge" / "runs"
            stale = runs / "stale"
            current = runs / "control" / "phases" / "run-current"
            stale.mkdir(parents=True)
            current.mkdir(parents=True)
            os.utime(stale, (4_000_000_000, 4_000_000_000))
            latest = project_dir / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "run.txt").write_text(str(current), encoding="utf-8")

            selected = FileEvidenceCatalog(project_dir).latest_run_dir()

        self.assertEqual(selected, current)

    def test_published_campaign_bundle_uses_manifest_and_summary_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            campaign = project_dir / "published-campaign"
            campaign.mkdir()
            (campaign / "manifest.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "summary.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "variants": {},
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "campaign.txt").write_text(str(campaign), encoding="utf-8")

            catalog = FileEvidenceCatalog(project_dir)

            self.assertEqual(
                catalog.latest_campaign_state()["campaign_id"],
                "campaign-1",
            )
            self.assertEqual(
                catalog.latest_campaign_summary()["status"],
                "completed",
            )

    def test_improvement_view_renders_reviewed_before_after_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            campaign = project_dir / "campaign"
            campaign.mkdir()
            (campaign / "manifest.json").write_text(
                json.dumps({"campaign_id": "campaign-1", "records": []}),
                encoding="utf-8",
            )
            (campaign / "summary.json").write_text(
                json.dumps(
                    {
                        "campaign_id": "campaign-1",
                        "status": "completed",
                        "variants": {},
                    }
                ),
                encoding="utf-8",
            )
            (campaign / "improvement_record.json").write_text(
                json.dumps(
                    {
                        "observed_problem": "failed tool calls were noisy",
                        "diagnosis": {
                            "source": "maintainer_review",
                            "review_status": "reviewed",
                        },
                        "hypothesis": "routing reduces failed calls",
                        "change": {"reference": "governed preset"},
                        "regression_cases": ["case-a", "case-b"],
                        "before_after": {
                            "control": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "failed_tool_calls": 8,
                                "total_tokens": 100,
                                "estimated_cost_usd": 0.1,
                            },
                            "treatment": {
                                "official_evaluated": 2,
                                "official_resolved": 2,
                                "failed_tool_calls": 5,
                                "total_tokens": 130,
                                "estimated_cost_usd": 0.13,
                            },
                            "delta": {
                                "official_resolved": 0,
                                "failed_tool_calls": -3,
                                "total_tokens": 30,
                                "estimated_cost_usd": 0.03,
                            },
                        },
                        "decision": {
                            "status": "iterate",
                            "rationale": "correctness tied and cost increased",
                        },
                        "claim_boundary": "commissioning evidence only",
                    }
                ),
                encoding="utf-8",
            )
            latest = project_dir / ".agent_forge" / "latest"
            latest.mkdir(parents=True)
            (latest / "campaign.txt").write_text(str(campaign), encoding="utf-8")

            rendered = _render_evidence_html(project_dir, "feedback")

        self.assertIn("Observed problem", rendered)
        self.assertIn("maintainer_review", rendered)
        self.assertIn("reviewed", rendered)
        self.assertIn("failed tool calls", rendered)
        self.assertIn("iterate", rendered)
        self.assertIn("commissioning evidence only", rendered)


if __name__ == "__main__":
    unittest.main()

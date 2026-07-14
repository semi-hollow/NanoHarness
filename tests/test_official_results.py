import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from agent_forge.bench.evidence import read_local_validation
from agent_forge.bench.official_results import apply_official_results, parse_official_results
from agent_forge.bench.swebench import _run_official_evaluation
from agent_forge.bench.types import BenchCaseResult, BenchRunSummary


class OfficialResultsTest(unittest.TestCase):
    def test_parse_run_report_maps_each_evidence_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = {
                "schema_version": 2,
                "resolved_ids": ["resolved"],
                "unresolved_ids": ["unresolved"],
                "error_ids": ["error"],
                "empty_patch_ids": ["empty"],
                "incomplete_ids": ["incomplete"],
            }
            report_path = root / "agent-forge.run-1.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")

            parsed = parse_official_results(
                root,
                "run-1",
                ["resolved", "unresolved", "error", "empty", "incomplete", "missing"],
            )

        self.assertEqual(parsed.report_path, report_path)
        self.assertEqual(parsed.outcomes["resolved"].status, "official_resolved")
        self.assertEqual(parsed.outcomes["unresolved"].status, "official_eval_failed")
        self.assertEqual(parsed.outcomes["error"].status, "official_eval_error")
        self.assertEqual(parsed.outcomes["empty"].status, "official_eval_skipped_empty_patch")
        self.assertEqual(parsed.outcomes["incomplete"].status, "official_eval_incomplete")
        self.assertEqual(parsed.outcomes["missing"].status, "official_eval_incomplete")

    def test_per_case_report_is_authoritative_when_run_report_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = (
                root
                / "logs"
                / "run_evaluation"
                / "run-2"
                / "agent-forge"
                / "case-1"
                / "report.json"
            )
            report_path.parent.mkdir(parents=True)
            report_path.write_text(json.dumps({"case-1": {"resolved": True}}), encoding="utf-8")

            parsed = parse_official_results(root, "run-2", ["case-1"])

        outcome = parsed.outcomes["case-1"]
        self.assertEqual(outcome.status, "official_resolved")
        self.assertTrue(outcome.resolved)
        self.assertEqual(outcome.report_path, report_path)

    def test_conflicting_run_report_ids_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model.run-3.json").write_text(
                json.dumps({"resolved_ids": ["case-1"], "error_ids": ["case-1"]}),
                encoding="utf-8",
            )

            parsed = parse_official_results(root, "run-3", ["case-1"])

        self.assertEqual(parsed.outcomes["case-1"].status, "official_eval_error")
        self.assertIn("conflicting", parsed.outcomes["case-1"].detail)

    def test_apply_results_keeps_official_and_local_evidence_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "model.run-4.json"
            report_path.write_text(json.dumps({"resolved_ids": ["case-1"]}), encoding="utf-8")
            trace_path = root / "trace.json"
            trace_path.write_text("{}", encoding="utf-8")
            patch_path = root / "patch.diff"
            patch_path.write_text("diff --git a/a b/a", encoding="utf-8")
            result = BenchCaseResult(
                instance_id="case-1",
                repo="owner/repo",
                workspace=root,
                trace_path=trace_path,
                usage_report_path=None,
                patch_path=patch_path,
                status="patch_generated",
                final_answer="done",
                patch_chars=20,
                local_validation_status="passed",
            )
            parsed = parse_official_results(root, "run-4", ["case-1"])

            apply_official_results([result], parsed, process_exit_code=0)

        self.assertEqual(result.local_validation_status, "passed")
        self.assertEqual(result.official_evaluation_status, "official_resolved")
        self.assertEqual(result.evaluation_status, "official_resolved")
        self.assertEqual(result.official_evaluation_report_path, str(report_path))

    def test_runner_parses_report_from_isolated_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace_path = root / "trace.json"
            trace_path.write_text("{}", encoding="utf-8")
            patch_path = root / "patch.diff"
            patch_path.write_text("diff --git a/a b/a", encoding="utf-8")
            case = BenchCaseResult(
                instance_id="case-1",
                repo="owner/repo",
                workspace=root,
                trace_path=trace_path,
                usage_report_path=None,
                patch_path=patch_path,
                status="patch_generated",
                final_answer="done",
                patch_chars=20,
            )
            predictions = root / "predictions.jsonl"
            predictions.write_text("{}\n", encoding="utf-8")
            summary = BenchRunSummary(
                run_id="run-5",
                dataset_name="dataset",
                split="test",
                provider="provider",
                model="model",
                output_dir=root,
                predictions_path=predictions,
                case_results=[case],
            )
            observed_cwd = []
            observed_commands = []

            def fake_run(command, **kwargs):
                observed_cwd.append(Path(kwargs["cwd"]))
                observed_commands.append(command)
                (Path(kwargs["cwd"]) / "agent-forge.run-5.json").write_text(
                    json.dumps({"resolved_ids": ["case-1"]}),
                    encoding="utf-8",
                )
                return CompletedProcess(command, 0, stdout="complete", stderr="")

            with patch("agent_forge.bench.adapters.official_evaluator.importlib.util.find_spec", return_value=object()):
                with patch("agent_forge.bench.adapters.official_evaluator.subprocess.run", side_effect=fake_run):
                    _run_official_evaluation(summary, max_workers=1, namespace_empty=False)

        self.assertEqual(observed_cwd, [root])
        self.assertIn("--split", observed_commands[0])
        self.assertEqual(observed_commands[0][observed_commands[0].index("--split") + 1], "test")
        self.assertEqual(case.official_evaluation_status, "official_resolved")
        self.assertTrue(summary.official_eval_report_path.endswith("agent-forge.run-5.json"))

    def test_local_validation_requires_all_recorded_test_evidence_to_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "events": [
                            {"event_type": "validation_evidence", "validation": {"kind": "unittest", "status": "passed", "evidence": "suite A"}},
                            {"event_type": "validation_evidence", "validation": {"kind": "pytest", "status": "failed", "evidence": "suite B"}},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            validation = read_local_validation(trace_path)

        self.assertEqual(validation.status, "failed")
        self.assertEqual(len(validation.evidence), 2)


if __name__ == "__main__":
    unittest.main()

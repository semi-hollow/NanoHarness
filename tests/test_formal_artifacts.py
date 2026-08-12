from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_forge.bench.formal_artifacts import (
    FormalArtifactRefused,
    FormalRunExpectation,
    parse_formal_cli,
    validate_formal_run,
)
from tests.test_quality_selection_summarizer import (
    QualitySelectionFixture,
    _sha256,
    _write_json,
)


def _expectation(fixture: QualitySelectionFixture) -> FormalRunExpectation:
    manifest = json.loads(fixture.command_path.read_text(encoding="utf-8"))
    command = manifest["commands"][0]
    bindings = (
        (
            str(fixture.command_path.relative_to(fixture.root)),
            _sha256(fixture.command_path),
        ),
        (
            str(fixture.protocol_path.relative_to(fixture.root)),
            _sha256(fixture.protocol_path),
        ),
        (
            str(
                (fixture.artifact_root / "dataset" / "agent-cases.json").relative_to(
                    fixture.root
                )
            ),
            manifest["agent_dataset_sha256"],
        ),
        (
            str(
                (fixture.artifact_root / "dataset" / "official-cases.json").relative_to(
                    fixture.root
                )
            ),
            manifest["official_dataset_sha256"],
        ),
        (
            str(fixture.skill_path.relative_to(fixture.root)),
            _sha256(fixture.skill_path),
        ),
    )
    return FormalRunExpectation(
        label="candidate-a/shard-a",
        project_root=fixture.root,
        artifact_root=fixture.artifact_root,
        output_root=fixture.artifact_root / "candidate-a" / "shard-a",
        instance_ids=tuple(fixture.case_ids),
        command_argv=tuple(command["argv"]),
        expected_source_identity=manifest["source_identity"],
        expected_source_manifest_path=fixture.command_path,
        frozen_inputs=bindings,
        observed_model="observed-model-a",
        skill_name="swebench_repair",
        skill_version="3.0.0",
        skill_content_sha256=_sha256(fixture.skill_path),
    )


def _rewrite(path: Path, change: Any) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    change(payload)
    _write_json(path, payload)


class FormalArtifactsTest(unittest.TestCase):
    def test_direct_validation_binds_complete_bundle_and_empty_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QualitySelectionFixture(Path(temporary))

            validated = validate_formal_run(_expectation(fixture))

            self.assertEqual((validated.planned, validated.finalized), (2, 2))
            self.assertEqual((validated.resolved, validated.decided), (1, 1))
            self.assertEqual((validated.empty, validated.infrastructure), (1, 0))
            self.assertEqual(validated.transport_retries, 0)
            self.assertIn(
                "cases/repo__project-1/official-patch.diff", validated.artifact_sha256
            )
            self.assertNotIn(
                "cases/repo__project-2/official-patch.diff", validated.artifact_sha256
            )
            evidence = validated.evidence("shard-a")
            self.assertIn("artifact_bundle_sha256", evidence)
            self.assertIn("expected_source_identity_sha256", evidence)
            self.assertNotIn("run_source_identity_sha256", evidence)

    def test_public_cli_rejects_duplicate_and_equals_forms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QualitySelectionFixture(Path(temporary))
            argv = list(_expectation(fixture).command_argv)
            with self.assertRaisesRegex(FormalArtifactRefused, "repeats --max-steps"):
                parse_formal_cli([*argv, "--max-steps", "128"], "duplicate")
            with self.assertRaisesRegex(FormalArtifactRefused, "flag=value"):
                parse_formal_cli([*argv, "--model=model-a"], "equals")

    def test_output_must_remain_inside_declared_campaign_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QualitySelectionFixture(Path(temporary))
            sibling = fixture.root / ".agent_forge" / "another-campaign"
            sibling.mkdir(parents=True)

            with self.assertRaisesRegex(
                FormalArtifactRefused, "escapes its frozen root"
            ):
                validate_formal_run(replace(_expectation(fixture), output_root=sibling))

    def test_expected_source_manifest_must_equal_tagged_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QualitySelectionFixture(Path(temporary))
            expectation = _expectation(fixture)
            fixture.command_path.write_bytes(fixture.command_path.read_bytes() + b" \n")

            with self.assertRaisesRegex(
                FormalArtifactRefused, "differs from tagged blob"
            ):
                validate_formal_run(expectation)

    def test_direct_validator_rejects_each_artifact_layer_tamper(self) -> None:
        layers = {
            "results": ("results.json", "provider drift"),
            "scorecard": ("scorecard.json", "scorecard metric total_tokens drift"),
            "trace": ("cases/repo__project-1/trace.json", "fallback"),
            "usage": ("cases/repo__project-1/usage.json", "token total drift"),
            "prediction": ("predictions.jsonl", "candidate/prediction drift"),
            "candidate": (
                "cases/repo__project-1/candidate_changes.diff",
                "candidate/prediction drift",
            ),
            "aggregate": (
                "agent-forge-provider-x-model-a.run-candidate-a.json",
                "safe official run aggregate",
            ),
            "official_patch": (
                "logs/run_evaluation/run-candidate-a/agent-forge-provider-x-model-a/"
                "repo__project-1/patch.diff",
                "official patch-byte drift",
            ),
            "empty_official_patch": (
                "logs/run_evaluation/run-candidate-a/agent-forge-provider-x-model-a/"
                "repo__project-2/patch.diff",
                "empty patch has unexpected evaluator patch bytes",
            ),
        }
        for layer, (relative, message) in layers.items():
            with self.subTest(layer=layer), tempfile.TemporaryDirectory() as temporary:
                fixture = QualitySelectionFixture(Path(temporary))
                run_dir = (
                    fixture.artifact_root
                    / "candidate-a"
                    / "shard-a"
                    / "run-candidate-a"
                )
                path = run_dir / relative
                if layer == "results":
                    _rewrite(path, lambda value: value.__setitem__("provider", "drift"))
                elif layer == "scorecard":
                    _rewrite(
                        path,
                        lambda value: value["metrics"].__setitem__("total_tokens", 0),
                    )
                elif layer == "trace":
                    _rewrite(
                        path,
                        lambda value: value["events"][2]["model_usage"].__setitem__(
                            "fallback_used", True
                        ),
                    )
                elif layer == "usage":
                    _rewrite(
                        path,
                        lambda value: value["summary"].__setitem__("total_tokens", 0),
                    )
                elif layer == "prediction":
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    rows[0]["model_patch"] = "different\n"
                    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                elif layer == "aggregate":
                    _rewrite(path, lambda value: value.pop("schema_version"))
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("different bytes\n", encoding="utf-8")

                with self.assertRaisesRegex(FormalArtifactRefused, message):
                    validate_formal_run(_expectation(fixture))

    def test_observed_model_and_transport_boundaries_are_fail_closed(self) -> None:
        cases = (
            ("observed", ["other-model"], 1, [], "provider-reported model drift"),
            ("transport", ["observed-model-a"], 2, ["invalid"], "non-retryable"),
        )
        for name, models, attempts, errors, message in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                fixture = QualitySelectionFixture(Path(temporary))
                case_dir = (
                    fixture.artifact_root
                    / "candidate-a"
                    / "shard-a"
                    / "run-candidate-a"
                    / "cases"
                    / fixture.case_ids[0]
                )
                trace_path = case_dir / "trace.json"

                def change_trace(value: dict[str, Any]) -> None:
                    usage = value["events"][2]["model_usage"]
                    usage["observed_models"] = models
                    usage["attempts"] = attempts
                    usage["error_codes"] = errors

                _rewrite(trace_path, change_trace)
                if name == "transport":
                    usage_path = case_dir / "usage.json"

                    def change_usage(value: dict[str, Any]) -> None:
                        usage = value["steps"][0]["llm_calls"][0]
                        usage["attempts"] = attempts
                        usage["error_codes"] = errors

                    _rewrite(usage_path, change_usage)

                with self.assertRaisesRegex(FormalArtifactRefused, message):
                    validate_formal_run(_expectation(fixture))

    def test_first_attempt_only_profile_rejects_recovered_transport_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = QualitySelectionFixture(Path(temporary))
            case_dir = (
                fixture.artifact_root
                / "candidate-a"
                / "shard-a"
                / "run-candidate-a"
                / "cases"
                / fixture.case_ids[0]
            )

            def retry(value: dict[str, Any]) -> None:
                usage = value["events"][2]["model_usage"]
                usage["attempts"] = 2
                usage["error_codes"] = ["server_error"]

            _rewrite(case_dir / "trace.json", retry)

            def usage_retry(value: dict[str, Any]) -> None:
                usage = value["steps"][0]["llm_calls"][0]
                usage["attempts"] = 2
                usage["error_codes"] = ["server_error"]

            _rewrite(case_dir / "usage.json", usage_retry)
            default_validated = validate_formal_run(_expectation(fixture))
            self.assertEqual(default_validated.transport_retries, 1)

            strict = replace(
                _expectation(fixture),
                max_transport_attempts=1,
                allowed_transport_error_codes=frozenset(),
            )
            with self.assertRaisesRegex(FormalArtifactRefused, "attempts drift"):
                validate_formal_run(strict)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "summarize_quality_selection.py"
SPEC = importlib.util.spec_from_file_location(
    "quality_selection_summarizer", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
SUMMARIZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SUMMARIZER
SPEC.loader.exec_module(SUMMARIZER)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class QualitySelectionFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifact_root = (
            root / ".agent_forge" / "canonical-showcase" / "quality-selection"
        )
        self.case_ids = ["repo__project-1", "repo__project-2"]
        self.candidates = [
            {
                "candidate_id": "candidate-a",
                "provider": "provider-x",
                "model": "model-a",
            },
            {
                "candidate_id": "candidate-b",
                "provider": "provider-x",
                "model": "model-b",
            },
        ]
        self.runtime = {
            "agent_mode": "single",
            "temperature": 0.0,
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            "max_steps": 128,
            "max_context_chars": 64000,
            "max_prompt_tokens": 131072,
            "reserved_output_tokens": 16384,
            "max_tool_calls_per_turn": 4,
            "cost_budget_usd_per_case": None,
            "run_timeout_seconds_per_case": 3600,
            "model_request_timeout_seconds": 600,
            "tool_execution_timeout_seconds": 600,
            "tool_routing_mode": "task-aware",
            "skill": "swebench_repair@3.0.0",
            "memory_recall_limit": 0,
            "execution_mode": "worktree",
            "network_policy": "deny",
            "keep_worktree": False,
            "official_namespace": "swebench",
            "official_cache_level": "instance",
            "official_max_workers_per_shard": 1,
            "fallback_allowed": False,
        }
        self.skill_path = (
            root
            / "agent_forge"
            / "skills"
            / "packages"
            / "swebench_repair"
            / "SKILL.md"
        )
        self.protocol_path = (
            root / "benchmarks" / "showcase" / "quality-selection-protocol-v1.json"
        )
        self.command_path = (
            root
            / "benchmarks"
            / "showcase"
            / "quality-selection-command-manifest-v1.json"
        )
        self._build()

    def _build(self) -> None:
        golden_path = self.root / "benchmarks" / "regression" / "golden-2-v1.json"
        _write_json(
            golden_path,
            {
                "schema_version": 1,
                "split": "test",
                "case_ids": self.case_ids,
                "ordered_case_ids_sha256": hashlib.sha256(
                    "\n".join(self.case_ids).encode()
                ).hexdigest(),
            },
        )
        protocol = {
            "schema_version": 1,
            "protocol_id": "test-selection",
            "development_set": {
                "manifest": "benchmarks/regression/golden-2-v1.json",
                "role": "seen_development_and_regression_only",
                "planned_cases_per_candidate": 2,
                "planned_total_starts": 4,
            },
            "candidates": self.candidates,
            "fixed_runtime": self.runtime,
            "execution": {
                "candidate_order": ["candidate-a", "candidate-b"],
                "pass_at": 1,
                "cross_shard_concurrency": 1,
                "correctness_rerun": 0,
                "whole_case_provider_retry": 0,
                "built_in_identical_transport_attempts_per_llm_call": 2,
                "official_evaluator_rerun": 0,
                "result_driven_parameter_change": False,
            },
            "selection_rule": {
                "primary": (
                    "highest official_resolved over the fixed planned denominator of 2"
                ),
                "tie_break_order": [
                    "higher official_decided coverage",
                    "fewer empty patches",
                    "fewer provider or evaluator infrastructure failures",
                    "fewer failed tool calls",
                    "candidate_order",
                ],
                "validity_requirement": (
                    "both candidates must complete and remain protocol-valid"
                ),
            },
            "claim_limits": ["development only"],
        }
        _write_json(self.protocol_path, protocol)

        dataset_dir = self.artifact_root / "dataset"
        dataset_dir.mkdir(parents=True, exist_ok=True)
        # These deliberately are not JSON. The summarizer may only hash them as
        # opaque sealed bytes and must never parse their rows or hidden labels.
        (dataset_dir / "official-cases.json").write_bytes(
            b"SEALED GOLD ROWS MUST NOT BE READ"
        )
        (dataset_dir / "agent-cases.json").write_bytes(b"SAFE AGENT CASES")
        (self.artifact_root / "dataset-binding.json").write_bytes(b"SAFE BINDING")
        image_path = (
            self.root
            / "benchmarks"
            / "showcase"
            / "quality-selection-image-manifest-v1.json"
        )
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"IMAGE IDENTITIES")
        self.skill_path.parent.mkdir(parents=True, exist_ok=True)
        self.skill_path.write_text("# skill\n", encoding="utf-8")
        fixture_probe = self.root / "scripts" / "probe_model_tool_contract.py"
        fixture_summarizer = self.root / "scripts" / "summarize_quality_selection.py"
        fixture_probe.parent.mkdir(parents=True, exist_ok=True)
        fixture_probe.write_bytes(
            (PROJECT_ROOT / "scripts" / "probe_model_tool_contract.py").read_bytes()
        )
        fixture_summarizer.write_bytes(SCRIPT_PATH.read_bytes())

        commands = [
            self._command(candidate, "shard-a") for candidate in self.candidates
        ]
        remove_flags = {"--model", "--limit", "--instance-id", "--output-root"}
        command_manifest = {
            "schema_version": 1,
            "status": "frozen_before_any_quality_selection_model_call",
            "protocol_sha256": _sha256(self.protocol_path),
            "capability_probe_script_sha256": _sha256(fixture_probe),
            "selection_summarizer_script_sha256": _sha256(fixture_summarizer),
            "development_set_manifest_sha256": _sha256(golden_path),
            "dataset_binding_sha256": _sha256(
                self.artifact_root / "dataset-binding.json"
            ),
            "agent_dataset_sha256": _sha256(dataset_dir / "agent-cases.json"),
            "official_dataset_sha256": _sha256(dataset_dir / "official-cases.json"),
            "image_manifest_sha256": _sha256(image_path),
            "skill_file_sha256": _sha256(self.skill_path),
            "planned_starts": 4,
            "credential_preflight": {
                "launcher_shell": "zsh -lic",
                "required_present_nonempty": "OPENCODE_GO_API_KEY",
                "forbidden_fallback_sources": [
                    "DEEPSEEK_API_KEY",
                    "OPENAI_API_KEY",
                ],
                "resolver_required_credential_source": "OPENCODE_GO_API_KEY",
                "record_key_value": False,
            },
            "capability_probes": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "output_must_be_absent": True,
                    "argv": [
                        ".venv/bin/python",
                        "scripts/probe_model_tool_contract.py",
                        "--provider",
                        candidate["provider"],
                        "--model",
                        candidate["model"],
                        "--base-url",
                        "https://provider.invalid/v1",
                        "--thinking",
                        "enabled",
                        "--reasoning-effort",
                        "high",
                        "--timeout",
                        "600",
                        "--output",
                        (
                            ".agent_forge/canonical-showcase/quality-selection/"
                            f"preflight/{candidate['candidate_id']}.json"
                        ),
                    ],
                }
                for candidate in self.candidates
            ],
            "execution_schedule": {
                "mode": "serial_manifest_order",
                "max_concurrent_commands": 1,
                "command_order": [
                    f"{command['candidate_id']}/{command['shard']}"
                    for command in commands
                ],
            },
            "normalization": {
                "remove_flag_value_pairs": sorted(remove_flags),
            },
            "commands": commands,
        }
        command_manifest["normalized_fixed_argv_sha256"] = (
            SUMMARIZER._normalized_command_hash(commands, remove_flags)
        )
        _write_json(self.command_path, command_manifest)

        for candidate in self.candidates:
            self._preflight(candidate)
        self._run(
            self.candidates[0],
            outcomes={self.case_ids[0]: "resolved", self.case_ids[1]: "empty"},
        )
        self._run(
            self.candidates[1],
            outcomes={
                self.case_ids[0]: "unresolved",
                self.case_ids[1]: "unresolved",
            },
        )

    def _command(self, candidate: dict[str, str], shard: str) -> dict[str, Any]:
        output_root = (
            f".agent_forge/canonical-showcase/quality-selection/"
            f"{candidate['candidate_id']}/{shard}"
        )
        argv = [
            ".venv/bin/forge",
            "bench",
            "swebench",
            "--dataset",
            ".agent_forge/canonical-showcase/quality-selection/dataset/official-cases.json",
            "--cases-file",
            ".agent_forge/canonical-showcase/quality-selection/dataset/agent-cases.json",
            "--provider",
            candidate["provider"],
            "--base-url",
            "https://provider.invalid/v1",
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
            "--model",
            candidate["model"],
            "--limit",
            "2",
        ]
        for instance_id in self.case_ids:
            argv.extend(["--instance-id", instance_id])
        argv.extend(["--output-root", output_root])
        return {
            "candidate_id": candidate["candidate_id"],
            "shard": shard,
            "instance_ids": self.case_ids,
            "argv": argv,
        }

    def _preflight(self, candidate: dict[str, str]) -> None:
        _write_json(
            self.artifact_root / "preflight" / f"{candidate['candidate_id']}.json",
            {
                "schema_version": 1,
                "status": "passed",
                "provider": candidate["provider"],
                "requested_model": candidate["model"],
                "credential_source": "OPENCODE_GO_API_KEY",
                "base_url_origin_path": "https://provider.invalid/v1",
                "base_url_sha256": hashlib.sha256(
                    b"https://provider.invalid/v1"
                ).hexdigest(),
                "observed_response_model": f"observed-{candidate['model']}",
                "round_trip_observed_response_model": f"observed-{candidate['model']}",
                "capability_source": (
                    f"provider_default:{candidate['provider']}:{candidate['model']}"
                ),
                "thinking_mode": "enabled",
                "reasoning_effort": "high",
                "tool_call_source": "native",
                "tool_call_count": 1,
                "tool_name": "probe_read_file",
                "tool_arguments_match": True,
                "round_trip_completed": True,
                "fallback_used": False,
                "attempts_per_call": [1, 1],
                "error_codes": [],
                "provider_usage_present": True,
                "error_code": "",
            },
        )

    def _run(self, candidate: dict[str, str], outcomes: dict[str, str]) -> None:
        candidate_id = candidate["candidate_id"]
        run_id = f"run-{candidate_id}"
        run_dir = self.artifact_root / candidate_id / "shard-a" / run_id
        prediction_name = f"agent-forge-{candidate['provider']}-{candidate['model']}"
        prediction_rows = []
        case_results = []
        score_cases = []
        resolved_ids = []
        unresolved_ids = []
        empty_ids = []
        total_tokens = 0
        total_cost = 0.0
        total_failed_tools = 0
        for index, instance_id in enumerate(self.case_ids):
            outcome = outcomes[instance_id]
            patch = "" if outcome == "empty" else f"diff --git a/{index} b/{index}\n"
            status = {
                "resolved": "official_resolved",
                "unresolved": "official_eval_failed",
                "empty": "official_eval_skipped_empty_patch",
            }[outcome]
            if outcome == "resolved":
                resolved_ids.append(instance_id)
            elif outcome == "unresolved":
                unresolved_ids.append(instance_id)
            else:
                empty_ids.append(instance_id)
            case_dir = run_dir / "cases" / instance_id
            case_dir.mkdir(parents=True, exist_ok=True)
            candidate_path = case_dir / "candidate_changes.diff"
            candidate_path.write_text(patch, encoding="utf-8")
            (case_dir / "usage_report.md").write_text("safe usage\n", encoding="utf-8")
            tokens = 100 + index
            cost = 0.01 + index / 1000
            failed_tools = index
            total_tokens += tokens
            total_cost += cost
            total_failed_tools += failed_tools
            observed = f"observed-{candidate['model']}"
            model_usage = {
                "provider": candidate["provider"],
                "model": candidate["model"],
                "observed_models": [observed],
                "fallback_used": False,
                "fallback_provider": "",
                "fallback_model": "",
                "error_codes": [],
                "attempts": 1,
                "usage_source": "provider",
                "total_tokens": tokens,
                "estimated_cost_usd": cost,
            }
            trace = {
                "task": "ignored model-visible task text",
                "events": [
                    {
                        "event_type": "model_capabilities",
                        "model_capabilities": {
                            "native_tool_calling": True,
                            "source": (
                                f"provider_default:{candidate['provider']}:"
                                f"{candidate['model']}"
                            ),
                        },
                    },
                    {
                        "event_type": "skill_selection",
                        "skill_mode": "auto",
                        "skills": [
                            {
                                "name": "swebench_repair",
                                "version": "3.0.0",
                                "content_sha256": _sha256(self.skill_path),
                                "content": "ignored skill body",
                            }
                        ],
                    },
                    {
                        "event_type": "llm_call",
                        "model_usage": model_usage,
                        "tool_call_count": 1,
                        "response_normalization": {"tool_call_source": "native"},
                        "llm_response_summary": "ignored model text",
                    },
                ],
            }
            _write_json(case_dir / "trace.json", trace)
            usage_call = {
                "provider": candidate["provider"],
                "model": candidate["model"],
                "provider_reported_models": [observed],
                "fallback_used": False,
                "fallback_provider": "",
                "fallback_model": "",
                "error_codes": [],
                "attempts": 1,
                "usage_source": "provider",
                "total_tokens": tokens,
                "estimated_cost_usd": cost,
                "response_normalization": {"tool_call_source": "native"},
            }
            _write_json(
                case_dir / "usage.json",
                {
                    "task": "ignored task text",
                    "steps": [{"llm_calls": [usage_call]}],
                    "summary": {
                        "llm_calls": 1,
                        "total_tokens": tokens,
                        "estimated_cost_usd": cost,
                        "active_skills": ["swebench_repair"],
                        "failed_tool_calls": failed_tools,
                    },
                },
            )
            prediction_rows.append(
                {
                    "instance_id": instance_id,
                    "model_name_or_path": prediction_name,
                    "model_patch": patch,
                }
            )
            case_results.append(
                {
                    "instance_id": instance_id,
                    "candidate_diff_path": str(candidate_path.resolve()),
                    "trace_path": str((case_dir / "trace.json").resolve()),
                    "usage_report_path": str((case_dir / "usage_report.md").resolve()),
                    "patch_chars": len(patch),
                    "error": "",
                    "official_evaluation_status": status,
                }
            )
            score_cases.append(
                {
                    "instance_id": instance_id,
                    "patch_chars": len(patch),
                    "patch_generated": bool(patch),
                    "official_evaluation_status": status,
                    "official_evaluated": outcome in {"resolved", "unresolved"},
                    "official_resolved": outcome == "resolved",
                    "total_tokens": tokens,
                    "failed_tool_calls": failed_tools,
                    "estimated_cost_usd": cost,
                }
            )
            if patch:
                official_dir = (
                    run_dir
                    / "logs"
                    / "run_evaluation"
                    / run_id
                    / prediction_name
                    / instance_id
                )
                official_dir.mkdir(parents=True, exist_ok=True)
                (official_dir / "patch.diff").write_text(patch, encoding="utf-8")
                # A valid-looking forbidden report proves the summarizer does
                # not depend on tests_status or any per-test label.
                _write_json(
                    official_dir / "report.json",
                    {
                        instance_id: {
                            "resolved": outcome == "resolved",
                            "tests_status": {},
                        }
                    },
                )

        predictions_path = run_dir / "predictions.jsonl"
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        predictions_path.write_text(
            "".join(json.dumps(item) + "\n" for item in prediction_rows),
            encoding="utf-8",
        )
        official_path = run_dir / f"{prediction_name}.{run_id}.json"
        completed = resolved_ids + unresolved_ids
        _write_json(
            official_path,
            {
                "schema_version": 2,
                "submitted_ids": self.case_ids,
                "completed_ids": completed,
                "resolved_ids": resolved_ids,
                "unresolved_ids": unresolved_ids,
                "error_ids": [],
                "empty_patch_ids": empty_ids,
                "incomplete_ids": [],
                "total_instances": 2,
                "submitted_instances": 2,
                "completed_instances": len(completed),
                "resolved_instances": len(resolved_ids),
                "unresolved_instances": len(unresolved_ids),
                "error_instances": 0,
                "empty_patch_instances": len(empty_ids),
            },
        )
        official_command = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            str((self.artifact_root / "dataset" / "official-cases.json").resolve()),
            "--split",
            "test",
            "--predictions_path",
            str(predictions_path.resolve()),
            "--max_workers",
            "1",
            "--cache_level",
            "instance",
            "--run_id",
            run_id,
            "--instance_ids",
            *self.case_ids,
            "--namespace",
            "swebench",
        ]
        results = {
            "run_id": run_id,
            "dataset_name": str(
                (self.artifact_root / "dataset" / "official-cases.json").resolve()
            ),
            "split": "test",
            "provider": candidate["provider"],
            "model": candidate["model"],
            "temperature": 0.0,
            "thinking_mode": "enabled",
            "reasoning_effort": "high",
            "agent_mode": "single",
            "max_revision_rounds": 0,
            "tool_routing_mode": "task-aware",
            "skill_mode": "auto",
            "skill_names": ["swebench_repair"],
            "skill_manifest_sha256": "builtins_only",
            "execution_mode": "worktree",
            "network_policy": "deny",
            "keep_worktree": False,
            "max_steps": 128,
            "max_context_chars": 64000,
            "max_prompt_tokens": 131072,
            "reserved_output_tokens": 16384,
            "max_tool_calls_per_turn": 4,
            "cost_budget_usd": None,
            "timeout_seconds": 3600,
            "model_request_timeout_seconds": 600,
            "tool_execution_timeout_seconds": 600,
            "memory_namespace": "swebench:<instance_id>",
            "memory_recall_limit": 0,
            "official_namespace": "swebench",
            "output_dir": str(run_dir.resolve()),
            "predictions_path": str(predictions_path.resolve()),
            "official_eval_command": official_command,
            "official_eval_exit_code": 0,
            "official_eval_output": "ignored evaluator text",
            "official_eval_report_path": str(official_path.resolve()),
            "official_eval_warnings": [],
            "case_results": case_results,
        }
        _write_json(run_dir / "results.json", results)
        scorecard = {
            "schema_version": 1,
            "metadata": {
                "run_id": run_id,
                "dataset_name": results["dataset_name"],
                "split": "test",
                "provider": candidate["provider"],
                "requested_model": candidate["model"],
                "observed_models": [f"observed-{candidate['model']}"],
                "official_namespace": "swebench",
                "temperature": 0.0,
                "thinking_mode": "enabled",
                "reasoning_effort": "high",
                "agent_mode": "single",
                "max_steps": 128,
                "max_context_chars": 64000,
                "max_prompt_tokens": 131072,
                "reserved_output_tokens": 16384,
                "max_tool_calls_per_turn": 4,
                "cost_budget_usd": None,
                "timeout_seconds": 3600,
                "model_request_timeout_seconds": 600,
                "tool_execution_timeout_seconds": 600,
                "max_revision_rounds": 0,
                "tool_routing_mode": "task-aware",
                "skill_mode": "auto",
                "skill_names": ["swebench_repair"],
                "skill_manifest_sha256": "builtins_only",
                "memory_recall_limit": 0,
                "execution_mode": "worktree",
                "network_policy": "deny",
                "keep_worktree": False,
            },
            "metrics": {
                "case_count": 2,
                "patch_generated_count": 2 - len(empty_ids),
                "official_evaluated_count": len(completed),
                "official_resolved_count": len(resolved_ids),
                "total_tokens": total_tokens,
                "failed_tool_calls": total_failed_tools,
                "estimated_cost_usd": round(total_cost, 6),
            },
            "cases": score_cases,
        }
        _write_json(run_dir / "scorecard.json", scorecard)

    def aggregate_path(self, candidate_id: str) -> Path:
        candidate = next(
            item for item in self.candidates if item["candidate_id"] == candidate_id
        )
        run_id = f"run-{candidate_id}"
        prediction_name = f"agent-forge-{candidate['provider']}-{candidate['model']}"
        return (
            self.artifact_root
            / candidate_id
            / "shard-a"
            / run_id
            / f"{prediction_name}.{run_id}.json"
        )


class QualitySelectionSummarizerTest(unittest.TestCase):
    def test_complete_valid_pair_selects_mechanical_winner_without_gold_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))

            summary = SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

            self.assertEqual(
                summary["status"], "selected_after_both_candidates_validated"
            )
            self.assertEqual(summary["winner"]["candidate_id"], "candidate-a")
            self.assertEqual(summary["development_set"]["planned_total_starts"], 4)
            self.assertEqual(
                [item["finalized"] for item in summary["candidates"]], [2, 2]
            )
            self.assertIn("No sealed rows", summary["artifact_boundary"])

    def test_patch_byte_drift_refuses_whole_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            official_patch = (
                fixture.artifact_root
                / "candidate-a"
                / "shard-a"
                / "run-candidate-a"
                / "logs"
                / "run_evaluation"
                / "run-candidate-a"
                / "agent-forge-provider-x-model-a"
                / fixture.case_ids[0]
                / "patch.diff"
            )
            official_patch.write_text("different bytes\n", encoding="utf-8")

            with self.assertRaisesRegex(
                SUMMARIZER.SelectionRefused, "official patch-byte drift"
            ):
                SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

    def test_incomplete_or_extra_formal_run_refuses_whole_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            extra = fixture.artifact_root / "candidate-b" / "shard-a" / "partial-run"
            extra.mkdir(parents=True)

            with self.assertRaisesRegex(
                SUMMARIZER.SelectionRefused, "exactly one formal run"
            ):
                SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

    def test_official_infrastructure_bucket_refuses_whole_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            aggregate_path = fixture.aggregate_path("candidate-a")
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
            failed_id = fixture.case_ids[1]
            aggregate["empty_patch_ids"] = []
            aggregate["error_ids"] = [failed_id]
            aggregate["empty_patch_instances"] = 0
            aggregate["error_instances"] = 1
            _write_json(aggregate_path, aggregate)

            with self.assertRaisesRegex(
                SUMMARIZER.SelectionRefused, "evaluator infrastructure errors"
            ):
                SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

    def test_fallback_or_provider_identity_drift_refuses_whole_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            trace_path = (
                fixture.artifact_root
                / "candidate-b"
                / "shard-a"
                / "run-candidate-b"
                / "cases"
                / fixture.case_ids[0]
                / "trace.json"
            )
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            trace["events"][2]["model_usage"]["fallback_used"] = True
            _write_json(trace_path, trace)

            with self.assertRaisesRegex(SUMMARIZER.SelectionRefused, "fallback"):
                SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

    def test_single_retryable_transport_retry_remains_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            case_dir = (
                fixture.artifact_root
                / "candidate-a"
                / "shard-a"
                / "run-candidate-a"
                / "cases"
                / fixture.case_ids[0]
            )
            trace_path = case_dir / "trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            trace_usage = trace["events"][2]["model_usage"]
            trace_usage["attempts"] = 2
            trace_usage["error_codes"] = ["request_timeout"]
            _write_json(trace_path, trace)
            usage_path = case_dir / "usage.json"
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            usage_call = usage["steps"][0]["llm_calls"][0]
            usage_call["attempts"] = 2
            usage_call["error_codes"] = ["request_timeout"]
            _write_json(usage_path, usage)

            summary = SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

            self.assertEqual(summary["winner"]["candidate_id"], "candidate-a")

    def test_case_with_no_tool_call_stays_in_fixed_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            case_dir = (
                fixture.artifact_root
                / "candidate-a"
                / "shard-a"
                / "run-candidate-a"
                / "cases"
                / fixture.case_ids[1]
            )
            trace_path = case_dir / "trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            trace["events"][2]["tool_call_count"] = 0
            trace["events"][2]["response_normalization"]["tool_call_source"] = ""
            _write_json(trace_path, trace)
            usage_path = case_dir / "usage.json"
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            usage["steps"][0]["llm_calls"][0]["response_normalization"][
                "tool_call_source"
            ] = ""
            _write_json(usage_path, usage)

            summary = SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

            self.assertEqual(summary["candidates"][0]["finalized"], 2)

    def test_estimated_usage_is_evidence_only_not_a_selection_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            preflight_path = fixture.artifact_root / "preflight" / "candidate-a.json"
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            preflight["provider_usage_present"] = False
            _write_json(preflight_path, preflight)
            case_dir = (
                fixture.artifact_root
                / "candidate-a"
                / "shard-a"
                / "run-candidate-a"
                / "cases"
                / fixture.case_ids[0]
            )
            trace_path = case_dir / "trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            trace["events"][2]["model_usage"]["usage_source"] = "estimate"
            _write_json(trace_path, trace)
            usage_path = case_dir / "usage.json"
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            usage["steps"][0]["llm_calls"][0]["usage_source"] = "estimate"
            _write_json(usage_path, usage)

            summary = SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

            self.assertEqual(summary["winner"]["candidate_id"], "candidate-a")

    def test_protocol_command_binding_drift_refuses_before_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = QualitySelectionFixture(Path(tmp))
            protocol = json.loads(fixture.protocol_path.read_text(encoding="utf-8"))
            protocol["fixed_runtime"]["max_steps"] = 129
            _write_json(fixture.protocol_path, protocol)

            with self.assertRaisesRegex(
                SUMMARIZER.SelectionRefused, "not bound to the current protocol"
            ):
                SUMMARIZER.summarize(fixture.root, fixture.artifact_root)

    def test_safe_official_reader_rejects_per_test_report_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            _write_json(path, {"tests_status": {}, "resolved": True})

            with self.assertRaisesRegex(
                SUMMARIZER.SelectionRefused,
                "not the safe official run aggregate",
            ):
                SUMMARIZER._safe_official_aggregate(path, "unsafe report")

    def test_lexicographic_rule_uses_declared_candidate_order_last(self):
        base = {
            "official_resolved": 4,
            "official_decided": 8,
            "empty_patches": 2,
            "infrastructure_failures": 0,
            "failed_tool_calls": 3,
        }
        first = {**base, "candidate_id": "first"}
        second = {**base, "candidate_id": "second"}

        winner = SUMMARIZER.select_winner([second, first], ["first", "second"])

        self.assertEqual(winner["candidate_id"], "first")

    def test_lexicographic_rule_applies_every_frozen_metric_in_order(self):
        neutral = {
            "official_resolved": 4,
            "official_decided": 8,
            "empty_patches": 2,
            "infrastructure_failures": 0,
            "failed_tool_calls": 3,
        }
        cases = [
            ("official_resolved", 5, 4),
            ("official_decided", 9, 8),
            ("empty_patches", 1, 2),
            ("infrastructure_failures", 0, 1),
            ("failed_tool_calls", 2, 3),
        ]
        for field, winning_value, losing_value in cases:
            with self.subTest(field=field):
                first = {**neutral, "candidate_id": "first", field: winning_value}
                second = {**neutral, "candidate_id": "second", field: losing_value}

                winner = SUMMARIZER.select_winner([second, first], ["first", "second"])

                self.assertEqual(winner["candidate_id"], "first")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys

from agent_forge.bench.adapters.official_results import (
    apply_official_results,
    parse_official_results,
)
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary


class SwebenchOfficialEvaluator:

    def evaluate(
        self,
        summary: BenchRunSummary,
        request: SwebenchRunRequest,
    ) -> None:
        if importlib.util.find_spec("swebench") is None:
            self._mark_unavailable(summary)
            return

        command = self._command(summary, request)
        summary.official_eval_command = command
        process = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=str(summary.output_dir),
        )
        summary.official_eval_exit_code = process.returncode
        output = f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
        summary.official_eval_output = output[-20000:]
        instance_ids = [result.instance_id for result in summary.case_results]
        parsed = parse_official_results(
            summary.output_dir,
            summary.run_id,
            instance_ids,
        )
        summary.official_eval_report_path = str(parsed.report_path or "")
        summary.official_eval_warnings = parsed.warnings
        apply_official_results(
            summary.case_results,
            parsed,
            process_exit_code=process.returncode,
        )

    @staticmethod
    def _mark_unavailable(summary: BenchRunSummary) -> None:
        summary.official_eval_exit_code = 127
        summary.official_eval_output = (
            "swebench package is not installed. Install SWE-bench and rerun "
            "with --evaluate."
        )
        for result in summary.case_results:
            result.official_evaluation_status = "official_eval_unavailable"
            result.official_evaluation_detail = summary.official_eval_output
            result.evaluation_status = "official_eval_unavailable"

    @staticmethod
    def _command(
        summary: BenchRunSummary,
        request: SwebenchRunRequest,
    ) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            summary.dataset_name,
            "--split",
            summary.split,
            "--predictions_path",
            str(summary.predictions_path),
            "--max_workers",
            str(request.max_workers),
            "--run_id",
            summary.run_id,
        ]
        instance_ids = [result.instance_id for result in summary.case_results]
        if instance_ids:
            command.extend(["--instance_ids", *instance_ids])
        needs_empty_namespace = platform.system() == "Darwin" and platform.machine().lower() in {
            "arm64",
            "aarch64",
        }
        if request.namespace_empty or needs_empty_namespace:
            command.extend(["--namespace", ""])
        return command

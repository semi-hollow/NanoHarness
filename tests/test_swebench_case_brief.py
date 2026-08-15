from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from agent_forge.bench.api import inspect_swebench_case
from agent_forge.bench.domain.models import BenchCase
from scripts.inspect_swebench_case import (
    DATASET_NAME,
    DATASET_REVISION,
    local_post_run_cases_file,
    main,
    render_experiment_observations,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGRESSION_CASE = "astropy__astropy-14182"


def _local_case() -> dict[str, object]:
    return {
        "instance_id": REGRESSION_CASE,
        "repo": "astropy/astropy",
        "base_commit": "abc123",
        "version": "5.1",
        "environment_setup_commit": "setup123",
        "created_at": "2022-12-16T11:13:37Z",
        "difficulty": "15 min - 1 hour",
        "problem_statement": (
            "The RST writer fails when header rows are omitted.\r\n"
            "\r\nOriginal issue body."
        ),
        "hints_text": "Inspect the RST writer options.",
        "FAIL_TO_PASS": ["astropy/io/ascii/tests/test_rst.py::test_no_header"],
        "PASS_TO_PASS": ["astropy/io/ascii/tests/test_rst.py::test_default"],
        "test_patch": "SECRET TEST PATCH",
        "patch": "SECRET GOLD PATCH",
    }


def test_case_inspection_forwards_the_pinned_dataset_revision() -> None:
    case = BenchCase.from_mapping(_local_case())
    with patch(
        "agent_forge.bench.api.SwebenchCaseSource.load",
        return_value=[case],
    ) as loader:
        inspection = inspect_swebench_case(
            REGRESSION_CASE,
            dataset_name=DATASET_NAME,
            dataset_revision=DATASET_REVISION,
        )

    request = loader.call_args.args[0]
    assert request.dataset_name == DATASET_NAME
    assert request.dataset_revision == DATASET_REVISION
    assert inspection.instance_id == REGRESSION_CASE


def test_viewer_preserves_source_fields_and_can_append_post_run_transitions(
    tmp_path: Path,
    capsys,
) -> None:
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps([_local_case()]), encoding="utf-8")
    output = tmp_path / "case.md"

    assert (
        main(
            [
                REGRESSION_CASE,
                "--cases-file",
                str(cases_file),
                "--experiment",
                "both",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    document = output.read_text(encoding="utf-8")
    raw_document = output.read_bytes()
    assert "# SWE-bench source case" in document
    assert "## environment_setup_commit\n\nsetup123" in document
    assert "## created_at\n\n2022-12-16T11:13:37Z" in document
    assert "## difficulty\n\n15 min - 1 hour" in document
    assert "## problem_statement" in document
    assert "The RST writer fails" in document
    assert b"header rows are omitted.\r\n\r\nOriginal issue body." in raw_document
    assert "test_no_header" in document
    assert "Optional post-run Tool / ACI observations" in document
    assert "resolved_to_unresolved" in document
    assert "| R1 | resolved | unresolved |" in document
    assert "| R2 | resolved | unresolved |" in document
    assert "SECRET TEST PATCH" not in document
    assert "SECRET GOLD PATCH" not in document
    assert "这个 Case 在做什么" not in document
    assert "Harness 观察点" not in document
    assert f"SAVED: {output}" in capsys.readouterr().out


def test_transition_projection_returns_nothing_for_case_outside_experiment() -> None:
    assert render_experiment_observations("unknown__case-1", "both") == ""


def test_completed_r2_case_prefers_the_local_post_run_dataset() -> None:
    local = local_post_run_cases_file(REGRESSION_CASE)
    if local is not None:
        assert local.name == "official-cases.sealed.json"
        assert "tool-aci-golden-20-r2" in local.as_posix()


def test_run_configuration_uses_case_id_and_experiment_environment_variables() -> None:
    configuration_path = (
        PROJECT_ROOT / ".run" / "NanoHarness Benchmark - Inspect SWE-bench Case.run.xml"
    )
    configuration = ET.parse(configuration_path).getroot().find("configuration")
    assert configuration is not None
    options = {
        item.attrib["name"]: item.attrib.get("value", "")
        for item in configuration.findall("option")
    }
    environment = {
        item.attrib["name"]: item.attrib["value"]
        for item in configuration.findall("./envs/env")
    }

    assert options["SCRIPT_NAME"] == "$PROJECT_DIR$/scripts/inspect_swebench_case.py"
    assert environment["SWE_BENCH_CASE_ID"] == REGRESSION_CASE
    assert environment["SWE_BENCH_EXPERIMENT"] == "none"

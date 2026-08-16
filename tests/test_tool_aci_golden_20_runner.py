from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_tool_aci_golden_20 import (
    DEFAULT_CONFIG,
    PROJECT_ROOT,
    build_shard_command,
    import_history,
    load_plan,
    main,
    render_plan,
)


def test_config_drives_cases_variant_and_runtime_flags() -> None:
    plan = load_plan(DEFAULT_CONFIG, "r2")
    rendered = json.loads(
        render_plan(
            plan,
            source_root=PROJECT_ROOT,
            output_root=PROJECT_ROOT / ".agent_forge/runs/experiments/test",
        )
    )

    assert rendered["paid_model_calls_started"] is False
    assert rendered["experiment_id"] == "07-tool-aci-golden-20-r2"
    assert rendered["variant"] == "tool-r2"
    assert rendered["case_count"] == 20
    assert [len(shard) for shard in rendered["shards"]] == [5, 5, 5, 5]
    assert (
        rendered["benchmark_args"][rendered["benchmark_args"].index("--model") + 1]
        == "deepseek-v4-flash"
    )


def test_shard_command_adds_only_pipeline_owned_coordinates() -> None:
    plan = load_plan(DEFAULT_CONFIG, "r2")
    command = build_shard_command(
        plan,
        0,
        output_root=PROJECT_ROOT / ".agent_forge/runs/experiments/test",
        repo_cache=PROJECT_ROOT / ".agent_forge/internal/cache/bench/repos",
    )

    assert command[:5] == [
        Path(command[0]).as_posix(),
        "-m",
        "agent_forge.forge_cli",
        "bench",
        "swebench",
    ]
    assert command.count("--instance-id") == 5
    assert command[command.index("--max-steps") + 1] == "128"
    assert "--evaluate" in command
    assert "--api-key" not in command


def test_default_cli_is_a_write_free_dry_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--experiment", "r2"]) == 0
    output = capsys.readouterr().out
    assert '"paid_model_calls_started": false' in output
    assert "VALIDATED_ONLY: no provider request was sent" in output


def test_historical_runs_are_imported_without_claiming_pipeline_launch(
    tmp_path: Path,
) -> None:
    plan = load_plan(DEFAULT_CONFIG, "r2")
    if not plan.historical_runs_root.is_dir():
        pytest.skip("ignored local R2 evidence is not present")

    output = import_history(plan, tmp_path / "execution.json")
    manifest = json.loads(output.read_text(encoding="utf-8"))

    assert manifest["artifact_type"] == "nanoharness_evaluation_execution"
    assert manifest["provenance"]["analysis_included"] is False
    assert manifest["provenance"]["historical_run_predates_this_launcher"] is True
    assert manifest["provenance"]["launcher"] == ("scripts/run_tool_aci_golden_20.py")
    assert len(manifest["provenance"]["launcher_sha256"]) == 64
    assert manifest["provenance"]["origin"] == "imported_historical_run"
    assert manifest["provenance"]["run_artifacts_producer"] == ("forge bench swebench")
    assert len(manifest["shards"]) == 4
    assert {item["role"] for item in manifest["shards"][0]["artifacts"]} == {
        "results",
        "scorecard",
        "predictions",
        "official_aggregate",
    }


@pytest.mark.parametrize(
    ("experiment", "variant"),
    [("r1", "tool-r0"), ("r1", "tool-r1"), ("r2", "tool-r2")],
)
def test_checked_in_execution_indexes_are_reproducible(
    experiment: str,
    variant: str,
    tmp_path: Path,
) -> None:
    plan = load_plan(DEFAULT_CONFIG, experiment, variant)
    if not plan.historical_runs_root.is_dir():
        pytest.skip("ignored local Tool/ACI evidence is not present")

    generated = import_history(plan, tmp_path / f"{variant}.json")
    assert generated.read_bytes() == plan.manifest_output.read_bytes()


def test_provenance_catalog_covers_every_completed_experiment_folder() -> None:
    catalog = json.loads(
        (PROJECT_ROOT / "benchmarks/experiments/artifact-provenance.json").read_text(
            encoding="utf-8"
        )
    )
    directories = {item["directory"] for item in catalog["experiments"]}
    expected = {
        path.relative_to(PROJECT_ROOT).as_posix()
        for path in (PROJECT_ROOT / "benchmarks/experiments").iterdir()
        if path.is_dir()
    }

    assert directories == expected


def test_reserved_coordinates_cannot_be_hidden_in_config(tmp_path: Path) -> None:
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["experiments"]["r2"]["benchmark_args"].extend(["--api-key", "secret"])
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="secret flags"):
        load_plan(path, "r2")


def test_core_contract_rejects_analysis_or_correctness_reruns(tmp_path: Path) -> None:
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    config["evaluation_contract"]["analysis_in_pipeline"] = True
    config["evaluation_contract"]["correctness_reruns"] = 1
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValueError, match="evaluation contract invalid"):
        load_plan(path, "r2")

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from agent_forge.bench.swebench import build_swebench_parser, run_swebench_from_args
from agent_forge.evaluation.feedback_dataset import export_feedback_dataset, record_feedback
from agent_forge.evaluation.experiment import write_ablation_comparison
from agent_forge.evaluation.mini_cases import run_mini_cases
from agent_forge.multi_agent import (
    FanoutPlan,
    LiveFanoutCoordinator,
    MultiAgentCoordinator,
    get_profile,
    list_profiles,
)
from agent_forge.observability.trace import TraceRecorder
from agent_forge.runtime.approval import ApprovalStore
from agent_forge.observability.usage_report import write_usage_artifacts
from agent_forge.runtime.agent_loop import AgentLoop
from agent_forge.runtime.config import RuntimeConfig
from agent_forge.runtime.execution_environment import ExecutionEnvironment, ExecutionEnvironmentConfig, EnvironmentProbe
from agent_forge.runtime.human_input import HumanInputStore
from agent_forge.runtime.llm_config import resolve_llm_config
from agent_forge.runtime.task_state import TaskStateStore, replay_trace
from agent_forge.runtime.wiring import build_llm, build_registry
from agent_forge.skills import SkillRegistry, build_default_skill_registry
from agent_forge.ui import build_ui_parser, run_ui_from_args


def build_parser() -> argparse.ArgumentParser:
    """Build the product-facing command surface.

    The parser exposes user goals instead of internal modes: run a task, run a
    public benchmark, inspect a report, replay a trace, or check setup.
    """

    parser = argparse.ArgumentParser(
        prog="forge",
        description="Agent Forge: a SWE-bench-oriented CodingAgent harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run Agent Forge on a repository task.")
    run_parser.add_argument("task", help="Issue or coding task to solve.")
    run_parser.add_argument("--workspace", default=".")
    _add_execution_environment_args(run_parser)
    run_parser.add_argument("--provider", default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"))
    run_parser.add_argument("--model")
    run_parser.add_argument("--base-url")
    run_parser.add_argument("--api-key")
    run_parser.add_argument("--max-steps", type=int, default=16)
    run_parser.add_argument("--max-context-chars", type=int, default=12000)
    _add_tool_routing_arg(run_parser)
    run_parser.add_argument("--approval-mode", default="trusted", choices=["trusted", "on-write", "on-risk", "locked", "dry-run"])
    run_parser.add_argument(
        "--auto-approve-writes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-approve write-like tool actions. Use --no-auto-approve-writes to leave pending approval files.",
    )
    run_parser.add_argument("--approval-root", default=".agent_forge/approvals")
    run_parser.add_argument("--human-input-root", default=".agent_forge/human_input")
    run_parser.add_argument("--operation-ledger-root", default=".agent_forge/operation_ledger")
    run_parser.add_argument(
        "--resume-state",
        default="",
        help="Path to a task_state checkpoint JSON used to seed a safe continuation.",
    )
    run_parser.add_argument("--output-root", default=".agent_forge/runs")
    run_parser.add_argument("--agent-mode", default="single", choices=["single", "multi", "fanout"])
    run_parser.add_argument("--profile", default="coding_fix", choices=list_profiles())
    run_parser.add_argument("--max-revision-rounds", type=int, default=2)
    run_parser.add_argument(
        "--fanout-plan",
        default="",
        help="Validated JSON task DAG required by --agent-mode fanout.",
    )
    run_parser.add_argument(
        "--fanout-resume",
        default="",
        help="Prior fanout run, summary, or checkpoint used to restore merged tasks.",
    )
    run_parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Maximum concurrent live fanout workers (bounded to 1-8).",
    )
    run_parser.add_argument(
        "--skills",
        default="auto",
        help="auto, none, or comma-separated built-in/custom skill names. Default: auto.",
    )
    run_parser.add_argument(
        "--skill-manifest",
        action="append",
        default=[],
        help="Load additional Skill manifest JSON files for this run.",
    )
    run_parser.add_argument(
        "--mcp-config",
        help="Load MCP-style external tool config for this run.",
    )
    run_parser.add_argument(
        "--mcp-tool",
        action="append",
        default=[],
        help="Allow only this MCP tool name. Can be passed more than once.",
    )

    bench_parser = subparsers.add_parser("bench", help="Run benchmark loops.")
    bench_subparsers = bench_parser.add_subparsers(dest="bench_name", required=True)
    swebench_parser = bench_subparsers.add_parser("swebench", help="Generate SWE-bench predictions.")
    build_swebench_parser(swebench_parser)

    eval_parser = subparsers.add_parser("eval", help="Run lightweight evaluation utilities.")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_name", required=True)
    mini_cases_parser = eval_subparsers.add_parser(
        "mini-cases",
        help="Score small non-coding Agent application cases from explicit evidence.",
    )
    mini_cases_parser.add_argument("--case", default="all", help="Mini case id to run, or all.")
    mini_cases_parser.add_argument(
        "--evidence",
        help="JSON evidence file. Use either one evidence object or a dict keyed by case_id.",
    )
    mini_cases_parser.add_argument("--output-root", default=".agent_forge/mini_cases")
    feedback_parser = eval_subparsers.add_parser(
        "feedback",
        help="Attach a human outcome and labels to one run or benchmark case.",
    )
    feedback_parser.add_argument("target", help="Run directory, case directory, or trace.json path.")
    feedback_parser.add_argument(
        "--outcome",
        required=True,
        choices=["accepted", "needs_work", "rejected"],
    )
    feedback_parser.add_argument("--label", action="append", default=[])
    feedback_parser.add_argument("--note", default="")
    feedback_parser.add_argument("--reviewer", default="human")
    export_parser = eval_subparsers.add_parser(
        "export-dataset",
        help="Export trace, policy, evaluation, and human-feedback evidence as JSONL.",
    )
    export_parser.add_argument("target", nargs="+", help="One or more run or case directories.")
    export_parser.add_argument("--output", default=".agent_forge/evaluation/evidence_dataset.jsonl")
    export_parser.add_argument("--require-feedback", action="store_true")
    export_parser.add_argument(
        "--include-patch",
        action="store_true",
        help="Include candidate patch text. By default only size and SHA-256 are exported.",
    )
    ablation_parser = eval_subparsers.add_parser(
        "ablation",
        help="Compare two matched benchmark run scorecards as a paired ablation.",
    )
    ablation_parser.add_argument("control", help="Control benchmark run directory.")
    ablation_parser.add_argument("treatment", help="Treatment benchmark run directory.")
    ablation_parser.add_argument("--factor", required=True, help="Single runtime factor changed between runs.")
    ablation_parser.add_argument("--control-label", default="control")
    ablation_parser.add_argument("--treatment-label", default="treatment")
    ablation_parser.add_argument("--output", default=".agent_forge/evaluation/ablation")

    report_parser = subparsers.add_parser("report", help="Print a benchmark or run report.")
    report_parser.add_argument("target", nargs="?", default="latest")

    replay_parser = subparsers.add_parser("replay", help="Replay a trace timeline.")
    replay_parser.add_argument("target", nargs="?", default="latest")

    skills_parser = subparsers.add_parser("skills", help="Inspect versioned Skill manifests.")
    skills_subparsers = skills_parser.add_subparsers(dest="skills_command", required=True)
    skills_list_parser = skills_subparsers.add_parser("list", help="List registered Skill versions.")
    skills_list_parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="Path to an additional Skill manifest JSON file.",
    )
    skills_list_parser.add_argument("--name", help="Filter to one skill name.")
    skills_list_parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
    skills_list_parser.add_argument("--no-builtins", action="store_true", help="Show only manifests passed with --manifest.")

    subparsers.add_parser("doctor", help="Check local benchmark/runtime environment.")
    approve_parser = subparsers.add_parser("approve", help="Approve or reject a pending human-in-the-loop request.")
    approve_parser.add_argument("operation_key", help="Approval operation key printed by a waiting run.")
    approve_parser.add_argument("--approval-root", default=".agent_forge/approvals")
    approve_parser.add_argument("--decision", choices=["approved", "rejected"], default="approved")
    approve_parser.add_argument("--note", default="")
    respond_parser = subparsers.add_parser("respond", help="Respond to or cancel a pending human-input request.")
    respond_parser.add_argument("request_id")
    response_group = respond_parser.add_mutually_exclusive_group(required=True)
    response_group.add_argument("--answer")
    response_group.add_argument("--cancel", action="store_true")
    respond_parser.add_argument("--note", default="")
    respond_parser.add_argument("--human-input-root", default=".agent_forge/human_input")
    resume_parser = subparsers.add_parser("resume", help="Resume from the latest checkpoint under a run directory.")
    resume_parser.add_argument("run_dir", help="Previous Agent Forge run directory.")
    resume_parser.add_argument("--task", help="Override the continuation task.")
    resume_parser.add_argument("--workspace", help="Override the checkpoint workspace.")
    _add_execution_environment_args(resume_parser)
    resume_parser.add_argument("--provider", default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"))
    resume_parser.add_argument("--model")
    resume_parser.add_argument("--base-url")
    resume_parser.add_argument("--api-key")
    resume_parser.add_argument("--max-steps", type=int, default=16)
    resume_parser.add_argument("--max-context-chars", type=int, default=12000)
    _add_tool_routing_arg(resume_parser)
    resume_parser.add_argument("--approval-mode", default="trusted", choices=["trusted", "on-write", "on-risk", "locked", "dry-run"])
    resume_parser.add_argument(
        "--auto-approve-writes",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-approve write-like tool actions. Use --no-auto-approve-writes to leave pending approval files.",
    )
    resume_parser.add_argument("--approval-root", default=".agent_forge/approvals")
    resume_parser.add_argument("--human-input-root", default=".agent_forge/human_input")
    resume_parser.add_argument("--operation-ledger-root", default=".agent_forge/operation_ledger")
    resume_parser.add_argument("--output-root", default=".agent_forge/runs")
    resume_parser.add_argument("--agent-mode", default="single", choices=["single", "multi"])
    resume_parser.add_argument("--profile", default="coding_fix", choices=list_profiles())
    resume_parser.add_argument("--max-revision-rounds", type=int, default=2)
    resume_parser.add_argument("--skills", default="auto")
    resume_parser.add_argument("--skill-manifest", action="append", default=[])
    resume_parser.add_argument("--mcp-config")
    resume_parser.add_argument("--mcp-tool", action="append", default=[])
    subparsers.add_parser("tui", help="Open a lightweight terminal menu.")
    ui_parser = subparsers.add_parser("ui", help="Open the local browser workbench UI.")
    build_ui_parser(ui_parser)
    return parser


def _add_execution_environment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--execution-mode",
        choices=["local", "worktree", "container"],
        default="local",
        help="Run in the selected checkout, an isolated git worktree, or a constrained OCI container over a snapshot.",
    )
    parser.add_argument(
        "--network-policy",
        choices=["deny", "allow"],
        default="deny",
        help="Block or allow network-oriented shell commands at the environment boundary.",
    )
    parser.add_argument(
        "--keep-worktree",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep isolated worktree/snapshot files for inspection after a run; containers are always removed.",
    )
    parser.add_argument("--container-runtime", default="docker", help="Docker-compatible OCI CLI for container mode.")
    parser.add_argument("--container-image", default="python:3.11-slim", help="Pre-pulled OCI image for container mode.")
    parser.add_argument("--container-cpus", type=float, default=1.0)
    parser.add_argument("--container-memory", default="1g")
    parser.add_argument("--container-pids-limit", type=int, default=256)
    parser.add_argument(
        "--container-read-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use a read-only container root filesystem; the /workspace snapshot remains writable.",
    )


def _add_tool_routing_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tool-routing",
        choices=["task-aware", "all"],
        default="task-aware",
        help="Change model-visible tool schemas for ablation; runtime safety policies remain enabled.",
    )


def main(argv: list[str] | None = None) -> None:
    """Dispatch the public CLI."""

    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(render_doctor())
        return
    if args.command == "approve":
        print(approve_request(args))
        return
    if args.command == "respond":
        print(respond_to_human_input(args))
        return
    if args.command == "resume":
        run_dir = resume_repository_task(args)
        print(f"Run directory: {run_dir}")
        print(f"Report: {run_dir / 'usage_report.md'}")
        return
    if args.command == "run":
        run_dir = run_repository_task(args)
        print(f"Run directory: {run_dir}")
        print(f"Report: {run_dir / 'usage_report.md'}")
        return
    if args.command == "bench" and args.bench_name == "swebench":
        summary = run_swebench_from_args(args)
        print(f"Benchmark run: {summary.output_dir}")
        print(f"Result card: {summary.output_dir / 'report.md'}")
        print(f"Predictions: {summary.predictions_path}")
        return
    if args.command == "eval" and args.eval_name == "mini-cases":
        report_paths = run_mini_cases_from_args(args)
        for path in report_paths:
            print(f"Mini case report: {path}")
        return
    if args.command == "eval" and args.eval_name == "feedback":
        try:
            path = record_feedback(
                args.target,
                outcome=args.outcome,
                labels=args.label,
                note=args.note,
                reviewer=args.reviewer,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Feedback: {path}")
        return
    if args.command == "eval" and args.eval_name == "export-dataset":
        try:
            records = export_feedback_dataset(
                args.target,
                args.output,
                require_feedback=args.require_feedback,
                include_patch=args.include_patch,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Dataset: {args.output}")
        print(f"Records: {len(records)}")
        return
    if args.command == "eval" and args.eval_name == "ablation":
        try:
            json_path, report_path = write_ablation_comparison(
                args.control,
                args.treatment,
                factor=args.factor,
                output_dir=args.output,
                control_label=args.control_label,
                treatment_label=args.treatment_label,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Ablation JSON: {json_path}")
        print(f"Ablation report: {report_path}")
        return
    if args.command == "report":
        print_report(args.target)
        return
    if args.command == "replay":
        print(replay_trace(str(resolve_trace_target(args.target))))
        return
    if args.command == "skills":
        print_skills(args)
        return
    if args.command == "tui":
        run_tui()
        return
    if args.command == "ui":
        run_ui_from_args(args)
        return


def run_repository_task(args: argparse.Namespace) -> Path:
    """Run the canonical AgentLoop on the selected workspace."""

    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:7]}"
    run_dir = Path(args.output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "trace.json"
    trace = TraceRecorder(str(trace_path))
    environment, probe = prepare_execution_environment(args, run_id, run_dir)
    trace.add(
        0,
        "Runtime",
        "execution_environment",
        execution_environment=probe.to_dict(),
    )
    try:
        active_workspace = str(environment.active_workspace)
        llm_config = resolve_llm_config(
            provider=args.provider,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=60,
        )
        if not llm_config.is_configured():
            raise SystemExit(
                f"{args.provider} model config is incomplete. Set API env vars or pass --base-url/--api-key/--model."
            )
        config = RuntimeConfig(
            workspace=active_workspace,
            max_steps=args.max_steps,
            trace_file=str(trace_path),
            max_context_chars=args.max_context_chars,
            timeout_seconds=900,
            execution_environment=environment,
            task_state_root=str(run_dir / "task_state"),
            resume_state=getattr(args, "resume_state", ""),
            auto_approve_writes=getattr(args, "auto_approve_writes", True),
            approval_root=getattr(args, "approval_root", ".agent_forge/approvals"),
            human_input_root=getattr(args, "human_input_root", ".agent_forge/human_input"),
            human_thread_id=getattr(args, "human_thread_id", ""),
            operation_ledger_root=getattr(args, "operation_ledger_root", ".agent_forge/operation_ledger"),
            approval_mode=args.approval_mode,
            skill_mode=_parse_skill_mode(getattr(args, "skills", "auto")),
            skill_names=_parse_skill_names(getattr(args, "skills", "auto")),
            skill_manifest_files=getattr(args, "skill_manifest", []),
            tool_routing_mode=getattr(args, "tool_routing", "task-aware"),
        )

        def registry_factory(workspace, worker_environment):
            return build_registry(
                str(workspace),
                auto=True,
                mcp_config_file=getattr(args, "mcp_config", None),
                mcp_allowed_tools=getattr(args, "mcp_tool", []),
                execution_environment=worker_environment,
            )

        agent_mode = getattr(args, "agent_mode", "single")
        if agent_mode == "fanout":
            plan_path = getattr(args, "fanout_plan", "")
            if not plan_path:
                raise SystemExit("--fanout-plan is required when --agent-mode fanout is selected.")
            try:
                plan = FanoutPlan.load(plan_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise SystemExit(f"invalid fanout plan: {exc}") from exc
            trace.set_run_context(task=args.task)
            summary = LiveFanoutCoordinator(
                plan=plan,
                base_config=config,
                trace=trace,
                run_dir=run_dir,
                llm_factory=lambda: build_llm(llm_config),
                registry_factory=registry_factory,
                max_workers=getattr(args, "max_workers", 4),
                resume_from=getattr(args, "fanout_resume", "") or None,
            ).run()
            final_answer = "\n".join(
                part
                for part in [
                    summary.final_answer.strip(),
                    f"fanout status: {summary.status}",
                    f"report: {summary.report_path}",
                    "The integration patch is a candidate artifact; no official benchmark resolution is implied.",
                ]
                if part
            )
            trace.set_run_context(
                stop_reason=f"fanout_{summary.status}",
                final_answer=final_answer,
            )
        else:
            registry = registry_factory(active_workspace, environment)
            llm = build_llm(llm_config)
        if agent_mode == "multi":
            profile = get_profile(getattr(args, "profile", "coding_fix"))
            summary = MultiAgentCoordinator(
                args.task,
                profile,
                config,
                trace,
                registry,
                llm,
                run_dir=run_dir,
                max_revision_rounds=getattr(args, "max_revision_rounds", profile.default_max_revision_rounds),
            ).run()
            final_answer = summary.final_answer
        elif agent_mode == "single":
            final_answer = AgentLoop(config, trace, registry, llm).run(args.task)
        trace.write()
        write_usage_artifacts(trace_path)
        (run_dir / "final_answer.txt").write_text(final_answer, encoding="utf-8")
        (run_dir / "patch.diff").write_text(environment.diff(), encoding="utf-8")
        _write_latest_run_pointer(run_dir)
        return run_dir
    finally:
        try:
            environment.write_manifest(run_dir)
        finally:
            environment.cleanup()


def prepare_execution_environment(
    args: argparse.Namespace,
    run_id: str,
    run_dir: str | Path,
) -> tuple[ExecutionEnvironment, EnvironmentProbe]:
    """Prepare and persist the execution boundary used by a repository run."""

    environment = ExecutionEnvironment(
        ExecutionEnvironmentConfig(
            mode=getattr(args, "execution_mode", "local"),
            workspace=args.workspace,
            run_id=run_id,
            network_policy=getattr(args, "network_policy", "deny"),
            keep_worktree=getattr(args, "keep_worktree", True),
            container_runtime=getattr(args, "container_runtime", "docker"),
            container_image=getattr(args, "container_image", "python:3.11-slim"),
            container_cpus=getattr(args, "container_cpus", 1.0),
            container_memory=getattr(args, "container_memory", "1g"),
            container_pids_limit=getattr(args, "container_pids_limit", 256),
            container_read_only=getattr(args, "container_read_only", True),
        )
    )
    probe = environment.prepare()
    environment.write_manifest(run_dir)
    return environment, probe


def resume_repository_task(args: argparse.Namespace) -> Path:
    """Resume from the newest task-state checkpoint under a previous run."""

    checkpoint_path = latest_checkpoint_path(args.run_dir)
    checkpoint = TaskStateStore.load_path(checkpoint_path)
    human_store = HumanInputStore(args.human_input_root)
    continuation_task, human_thread_id = continuation_task_with_human_response(
        checkpoint,
        human_store,
        override_task=args.task or "",
    )
    run_args = argparse.Namespace(
        task=continuation_task,
        workspace=args.workspace or checkpoint_resume_workspace(checkpoint),
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        max_steps=args.max_steps,
        max_context_chars=args.max_context_chars,
        approval_mode=args.approval_mode,
        auto_approve_writes=args.auto_approve_writes,
        approval_root=args.approval_root,
        human_input_root=args.human_input_root,
        human_thread_id=human_thread_id,
        operation_ledger_root=args.operation_ledger_root,
        resume_state=str(checkpoint_path),
        output_root=args.output_root,
        agent_mode=args.agent_mode,
        profile=args.profile,
        max_revision_rounds=args.max_revision_rounds,
        skills=args.skills,
        skill_manifest=args.skill_manifest,
        mcp_config=args.mcp_config,
        mcp_tool=args.mcp_tool,
        execution_mode=args.execution_mode,
        network_policy=args.network_policy,
        keep_worktree=args.keep_worktree,
        tool_routing=args.tool_routing,
        container_runtime=args.container_runtime,
        container_image=args.container_image,
        container_cpus=args.container_cpus,
        container_memory=args.container_memory,
        container_pids_limit=args.container_pids_limit,
        container_read_only=args.container_read_only,
    )
    run_dir = run_repository_task(run_args)
    write_resume_link(
        run_dir,
        resumed_from_run_dir=Path(args.run_dir),
        resume_state=checkpoint_path,
        previous_run_id=checkpoint.run_id,
    )
    return run_dir


def checkpoint_resume_workspace(checkpoint) -> str:
    """Return the original checkout when a prior run used a temporary worktree."""

    metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
    environment = metadata.get("execution_environment")
    if isinstance(environment, dict) and environment.get("requested_workspace"):
        return str(environment["requested_workspace"])
    return checkpoint.workspace


def write_resume_link(
    run_dir: str | Path,
    *,
    resumed_from_run_dir: str | Path,
    resume_state: str | Path,
    previous_run_id: str,
) -> tuple[Path, Path]:
    """Write machine-readable and report-visible resume-chain artifacts."""

    run_path = Path(run_dir)
    payload = {
        "resumed_from_run_dir": str(Path(resumed_from_run_dir)),
        "resume_state": str(Path(resume_state)),
        "previous_run_id": previous_run_id,
    }
    link_path = run_path / "resume_link.json"
    link_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    chain_path = run_path / "resume_chain.md"
    chain_text = "\n".join(
        [
            "# Resume Chain",
            "",
            f"- resumed_from_run_dir: `{payload['resumed_from_run_dir']}`",
            f"- resume_state: `{payload['resume_state']}`",
            f"- previous_run_id: `{payload['previous_run_id']}`",
            "",
        ]
    )
    chain_path.write_text(chain_text, encoding="utf-8")

    report_path = run_path / "usage_report.md"
    if report_path.exists():
        report = report_path.read_text(encoding="utf-8").rstrip()
        chain_body = "\n".join(chain_text.splitlines()[2:]) + "\n"
        report_path.write_text(f"{report}\n\n## Resume Chain\n\n{chain_body}", encoding="utf-8")
    return link_path, chain_path


def run_mini_cases_from_args(args: argparse.Namespace) -> list[Path]:
    """Run the mini-case evaluator from CLI args."""

    evidence = {}
    if args.evidence:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    return run_mini_cases(case_id=args.case, evidence=evidence, output_dir=args.output_root)


def latest_checkpoint_path(run_dir: str | Path) -> Path:
    """Return the newest task-state checkpoint JSON under a run directory."""

    state_dir = Path(run_dir) / "task_state"
    candidates = sorted(state_dir.glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"no task_state checkpoints found under {run_dir}")

    def updated_at(path: Path) -> float:
        try:
            return float(json.loads(path.read_text(encoding="utf-8")).get("updated_at") or 0.0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return path.stat().st_mtime

    return max(candidates, key=updated_at)


def approve_request(args: argparse.Namespace) -> str:
    """Update one pending approval request from the CLI."""

    request = ApprovalStore(args.approval_root).decide(
        args.operation_key,
        args.decision,
        note=getattr(args, "note", ""),
    )
    return (
        f"approval {request.status}: operation_key={request.operation_key} "
        f"tool={request.tool_name} path={request.path}"
    )


def respond_to_human_input(args: argparse.Namespace) -> str:
    """Persist an operator response without resuming execution implicitly."""

    store = HumanInputStore(args.human_input_root)
    if getattr(args, "cancel", False):
        request = store.cancel(args.request_id, note=getattr(args, "note", ""))
    else:
        request = store.respond(args.request_id, args.answer, note=getattr(args, "note", ""))
    return (
        f"human input {request.status}: request_id={request.request_id} "
        f"path={request.path}"
    )


def continuation_task_with_human_response(checkpoint, store: HumanInputStore, override_task: str = "") -> tuple[str, str]:
    """Build a continuation task and require a terminal human-input decision."""

    metadata = checkpoint.metadata if isinstance(checkpoint.metadata, dict) else {}
    thread_id = str(metadata.get("human_thread_id") or checkpoint.run_id)
    task = override_task or f"continue previous task: {checkpoint.task}"
    request_id = str(metadata.get("human_input_request_id") or "")
    if not request_id:
        return task, thread_id
    request = store.get(request_id)
    if request is None:
        raise SystemExit(f"human input request not found: {request_id}")
    if request.status == "pending":
        raise SystemExit(f"human input is still pending: {request_id}")
    if request.status == "cancelled":
        raise SystemExit(f"human input request was cancelled: {request_id}")
    task = "\n".join(
        [
            task,
            "",
            "Human response from the previous run:",
            f"Question: {request.question}",
            f"Answer: {request.answer}",
            "Continue from this explicit operator input; do not ask the same question again.",
        ]
    )
    return task, thread_id


def render_doctor() -> str:
    """Return a concise environment report for benchmark runs."""

    rows = [
        ("python", sys.version.split()[0]),
        ("platform", f"{platform.system()} {platform.machine()}"),
        ("cwd", str(Path.cwd())),
        ("git", _command_version(["git", "--version"])),
        ("docker", _command_version(["docker", "--version"])),
        ("datasets", "installed" if importlib.util.find_spec("datasets") else "missing; install with python -m pip install -e '.[bench]'"),
        ("swebench", "installed" if importlib.util.find_spec("swebench") else "missing; needed only for official --evaluate"),
        ("DEEPSEEK_API_KEY", "set" if os.getenv("DEEPSEEK_API_KEY") else "missing"),
        ("AGENT_FORGE_BASE_URL", os.getenv("AGENT_FORGE_BASE_URL", "")),
        ("AGENT_FORGE_MODEL", os.getenv("AGENT_FORGE_MODEL", "")),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name:<{width}} : {value}" for name, value in rows)


def print_report(target: str) -> None:
    """Print report.md for a run directory or the latest benchmark."""

    report = resolve_report_target(target)
    print(report.read_text(encoding="utf-8"))


def resolve_report_target(target: str) -> Path:
    """Resolve ``latest`` or a path to a report file."""

    if target == "latest":
        pointer = Path(".agent_forge/latest/bench.txt")
        if not pointer.exists():
            pointer = Path(".agent_forge/latest/run.txt")
        if not pointer.exists():
            raise SystemExit("No latest run pointer found.")
        target = pointer.read_text(encoding="utf-8").strip()
    path = Path(target)
    if path.is_dir():
        for candidate in (
            path / "report.md",
            path / "fanout" / "fanout_report.md",
            path / "multi_agent" / "multi_agent_report.md",
            path / "usage_report.md",
        ):
            if candidate.exists():
                return candidate
    if path.exists():
        return path
    raise SystemExit(f"Report not found: {target}")


def resolve_trace_target(target: str) -> Path:
    """Resolve ``latest`` or a path to trace.json."""

    if target == "latest":
        pointer = Path(".agent_forge/latest/bench.txt")
        if not pointer.exists():
            pointer = Path(".agent_forge/latest/run.txt")
        if not pointer.exists():
            raise SystemExit("No latest run pointer found.")
        run_dir = Path(pointer.read_text(encoding="utf-8").strip())
        if (run_dir / "trace.json").exists():
            return run_dir / "trace.json"
        traces = sorted(run_dir.glob("cases/*/trace.json"))
        if traces:
            return traces[0]
        raise SystemExit(f"No trace found under {run_dir}")
    path = Path(target)
    if path.is_dir():
        return path / "trace.json"
    return path


def print_skills(args: argparse.Namespace) -> None:
    """Print Skill manifest registry contents.

    This command is intentionally read-only. It answers production questions
    like "which skill version is active?", "what permission scopes does it
    need?", and "where would rollback go?" without starting an agent run.
    """

    manifests = args.manifest or []
    registry = SkillRegistry() if args.no_builtins else build_default_skill_registry([])
    try:
        if manifests:
            registry.load_manifests(manifests)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    specs = registry.list_specs(name=args.name)
    if args.json:
        report = [spec.to_dict() for spec in specs]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if not specs:
        print("No skills found.")
        return

    print("Skill Registry")
    for spec in specs:
        rollback = registry.rollback_target(spec.name, spec.version)
        rollback_label = f"{rollback.name}@{rollback.version}" if rollback else "-"
        permissions = ", ".join(spec.permissions) or "-"
        dependencies = ", ".join(spec.dependencies) or "-"
        print(f"- {spec.name}@{spec.version}")
        print(f"  owner       : {spec.owner or '-'}")
        print(f"  entrypoint  : {spec.entrypoint}")
        print(f"  permissions : {permissions}")
        print(f"  dependencies: {dependencies}")
        print(f"  rollback_to : {rollback_label}")
        print(f"  tags        : {', '.join(spec.tags) or '-'}")
        print(f"  tools       : {', '.join(spec.tool_names) or '-'}")


def _parse_skill_mode(value: str) -> str:
    """Normalize CLI skill mode without exposing parser details elsewhere."""

    return "none" if (value or "").strip().lower() == "none" else "auto"


def _parse_skill_names(value: str) -> list[str]:
    """Return explicit skill names from --skills, or empty for auto/none."""

    normalized = (value or "").strip()
    if not normalized or normalized.lower() in {"auto", "none"}:
        return []
    return [item.strip() for item in normalized.split(",") if item.strip()]


def run_tui() -> None:
    """A tiny terminal menu for users who do not want to remember commands.

    This is deliberately lightweight. It is not a Claude Code-style product
    surface; it simply makes the main benchmark flow discoverable while keeping
    the repo focused on agent-runtime design.
    """

    print("Agent Forge")
    print("1. Doctor")
    print("2. Run SWE-bench Lite sample")
    print("3. Run a task in current repo")
    print("4. Show latest report")
    choice = input("Choose 1-4: ").strip()
    if choice == "1":
        print(render_doctor())
    elif choice == "2":
        limit = input("Limit [1]: ").strip() or "1"
        provider = input("Provider [deepseek]: ").strip() or "deepseek"
        args = argparse.Namespace(
            dataset="princeton-nlp/SWE-bench_Lite",
            split="test",
            limit=int(limit),
            instance_id=[],
            showcase=False,
            regression_set=None,
            cases_file=None,
            provider=provider,
            model=None,
            base_url=None,
            api_key=None,
            max_steps=16,
            max_context_chars=12000,
            repo_cache=".agent_forge/bench/repos",
            output_root=".agent_forge/runs",
            direct_baseline=True,
            evaluate=False,
            max_workers=1,
            namespace_empty=False,
        )
        summary = run_swebench_from_args(args)
        print(f"Result card: {summary.output_dir / 'report.md'}")
    elif choice == "3":
        task = input("Task: ").strip()
        if not task:
            print("No task provided.")
            return
        args = argparse.Namespace(
            task=task,
            workspace=".",
            provider=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek"),
            model=None,
            base_url=None,
            api_key=None,
            max_steps=16,
            max_context_chars=12000,
            approval_mode="trusted",
            output_root=".agent_forge/runs",
            agent_mode="single",
            profile="coding_fix",
            max_revision_rounds=2,
            skills="auto",
            skill_manifest=[],
            mcp_config=None,
            mcp_tool=[],
        )
        print(f"Run directory: {run_repository_task(args)}")
    elif choice == "4":
        print_report("latest")
    else:
        print("Canceled.")


def _command_version(command: list[str]) -> str:
    """Return a command version or a compact missing marker."""

    if shutil.which(command[0]) is None:
        return "missing"
    result = subprocess.run(command, text=True, capture_output=True)
    return (result.stdout or result.stderr).strip().splitlines()[0]


def _write_latest_run_pointer(run_dir: Path) -> None:
    """Update stable pointer for report/replay commands."""

    latest = Path(".agent_forge/latest")
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "run.txt").write_text(str(run_dir), encoding="utf-8")


if __name__ == "__main__":
    main()

"""解析后的 CLI 命令到 capability 公共 API 的单一分发表。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_forge.bench.presentation.cli import (
    publish_case_document,
    render_case_catalog_from_args,
    render_case_inspection_from_args,
    run_campaign_from_args,
    run_swebench_from_args,
)
from agent_forge.context.api import (
    forget_memory,
    list_memories,
    RememberMemoryRequest,
    remember_memory,
)
from agent_forge.cli.inspection import print_skills, render_inspection, render_doctor
from agent_forge.cli.parser import build_parser
from agent_forge.cli.repository import run_repository_task
from agent_forge.cli.resume import resume_repository_task
from agent_forge.evaluation.api import (
    FeedbackRequest,
    export_feedback_dataset,
    record_feedback,
)
from agent_forge.showcase import run_governed_demo
from agent_forge.workbench.api import run_ui_from_args


# 主要入口：公开 ``forge`` 命令总分发，只路由到各 capability API。
def main(argv: list[str] | None = None) -> None:
    """解析并分发公开 CLI；本函数不包含 Agent 业务逻辑。"""

    args = build_parser().parse_args(argv)
    if args.command == "console":
        from agent_forge.operator_console import run_console_from_args

        run_console_from_args(args)
    elif args.command == "doctor":
        print(render_doctor())
    elif args.command == "inspect":
        print(render_inspection(args.target), end="")
    elif args.command == "demo":
        result = run_governed_demo(
            args.scenario,
            output_root=args.output_root,
            answer=args.answer,
        )
        print(f"Demo: {result.report_path}")
        print(f"State: {result.waiting_status} -> {result.completed_status}")
        print(f"Inspect: forge inspect {result.inspect_target}")
    elif args.command == "resume":
        _print_run_location(resume_repository_task(args))
    elif args.command == "run":
        _print_run_location(run_repository_task(args))
    elif args.command == "bench" and args.bench_name == "swebench":
        summary = run_swebench_from_args(args)
        print(f"Benchmark run: {summary.output_dir}")
        print(f"Result card: {summary.output_dir / 'report.md'}")
        print(f"Predictions: {summary.predictions_path}")
    elif args.command == "bench" and args.bench_name == "cases":
        publish_case_document(render_case_catalog_from_args(args), args.output)
    elif args.command == "bench" and args.bench_name == "case":
        publish_case_document(render_case_inspection_from_args(args), args.output)
    elif args.command == "bench" and args.bench_name == "campaign":
        campaign = run_campaign_from_args(args)
        print(f"Campaign status: {campaign.state.status}")
        print(f"Campaign directory: {campaign.campaign_dir}")
        print(f"Campaign report: {campaign.report_path}")
        if campaign.published_bundle_dir:
            print(f"Public evidence: {campaign.published_bundle_dir}")
    elif args.command == "eval":
        _dispatch_evaluation(args)
    elif args.command == "skills":
        print_skills(args)
    elif args.command == "memory":
        _dispatch_memory(args)
    elif args.command == "ui":
        run_ui_from_args(args)


def _dispatch_evaluation(args: argparse.Namespace) -> None:
    if args.eval_name == "feedback":
        try:
            path = record_feedback(
                FeedbackRequest(
                    target=args.target,
                    outcome=args.outcome,
                    labels=tuple(args.label),
                    note=args.note,
                    reviewer=args.reviewer,
                )
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Feedback: {path}")
        return
    if args.eval_name == "export-dataset":
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
def _print_run_location(run_dir: Path) -> None:
    print(f"Run directory: {run_dir}")
    print(f"Report: {run_dir / 'usage_report.md'}")


def _dispatch_memory(args: argparse.Namespace) -> None:
    """把 CLI 参数转换成长期记忆公共 API 调用。"""

    try:
        if args.memory_command == "remember":
            record = remember_memory(
                RememberMemoryRequest(
                    memory_root=args.memory_root,
                    workspace=args.workspace,
                    key=args.key,
                    content=args.content,
                    scope=args.scope,
                )
            )
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
            return
        if args.memory_command == "forget":
            record = forget_memory(args.memory_root, args.memory_id)
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
            return
        records = list_memories(
            args.memory_root,
            args.workspace,
            scope=args.scope,
        )
        if args.json:
            print(
                json.dumps(
                    [record.to_dict() for record in records],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        for record in records:
            print(
                f"{record.memory_id}\t{record.scope}\tr{record.revision}\t{record.key}"
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

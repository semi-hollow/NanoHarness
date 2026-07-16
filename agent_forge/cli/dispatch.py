"""解析后的 CLI 命令到 capability 公共 API 的单一分发表。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_forge.bench.presentation.cli import run_swebench_from_args
from agent_forge.context.api import (
    build_evidence_reference,
    list_memories,
    promote_memory,
    propose_memory,
    reject_memory,
    retire_memory,
)
from agent_forge.cli.inspection import (
    print_report,
    print_skills,
    render_doctor,
    resolve_trace_target,
    run_tui,
)
from agent_forge.cli.operator import approve_request, respond_to_human_input
from agent_forge.cli.parser import build_parser
from agent_forge.cli.repository import run_repository_task
from agent_forge.cli.resume import resume_repository_task
from agent_forge.evaluation.api import (
    export_feedback_dataset,
    record_feedback,
    run_mini_cases,
    write_ablation_comparison,
)
from agent_forge.observability.api import replay_trace_file
from agent_forge.workbench.api import run_ui_from_args

# 主要入口：下方定义承接该模块的核心调用。
def main(argv: list[str] | None = None) -> None:
    """解析并分发公开 CLI；本函数不包含 Agent 业务逻辑。"""

    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        print(render_doctor())
    elif args.command == "approve":
        print(approve_request(args))
    elif args.command == "respond":
        print(respond_to_human_input(args))
    elif args.command == "resume":
        _print_run_location(resume_repository_task(args))
    elif args.command == "run":
        _print_run_location(run_repository_task(args))
    elif args.command == "bench" and args.bench_name == "swebench":
        summary = run_swebench_from_args(args)
        print(f"Benchmark run: {summary.output_dir}")
        print(f"Result card: {summary.output_dir / 'report.md'}")
        print(f"Predictions: {summary.predictions_path}")
    elif args.command == "eval":
        _dispatch_evaluation(args)
    elif args.command == "report":
        print_report(args.target)
    elif args.command == "replay":
        print(replay_trace_file(str(resolve_trace_target(args.target))))
    elif args.command == "skills":
        print_skills(args)
    elif args.command == "memory":
        _dispatch_memory(args)
    elif args.command == "tui":
        run_tui()
    elif args.command == "ui":
        run_ui_from_args(args)


def run_mini_cases_from_args(args: argparse.Namespace) -> list[Path]:
    """把 CLI evidence 文件适配为 mini-case 公共 API。"""

    evidence = {}
    if args.evidence:
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    return run_mini_cases(
        case_id=args.case,
        evidence=evidence,
        output_dir=args.output_root,
    )


def _dispatch_evaluation(args: argparse.Namespace) -> None:
    if args.eval_name == "mini-cases":
        for path in run_mini_cases_from_args(args):
            print(f"Mini case report: {path}")
        return
    if args.eval_name == "feedback":
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
    if args.eval_name == "ablation":
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


def _print_run_location(run_dir: Path) -> None:
    print(f"Run directory: {run_dir}")
    print(f"Report: {run_dir / 'usage_report.md'}")


def _dispatch_memory(args: argparse.Namespace) -> None:
    """把 CLI 参数转换成长期记忆公共 API 调用。"""

    try:
        if args.memory_command == "propose":
            record = propose_memory(
                memory_root=args.memory_root,
                workspace=args.workspace,
                namespace=args.namespace,
                key=args.key,
                kind=args.kind,
                content=args.content,
                scope=args.scope,
                agent_name=args.agent_name,
                confidence=args.confidence,
                importance=args.importance,
                tags=args.tag,
                ttl_seconds=args.ttl_seconds,
            )
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
            return
        if args.memory_command == "promote":
            evidence = [build_evidence_reference(item) for item in args.evidence]
            record = promote_memory(args.memory_root, args.memory_id, evidence)
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
            return
        if args.memory_command == "retire":
            record = retire_memory(args.memory_root, args.memory_id)
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
            return
        if args.memory_command == "reject":
            record = reject_memory(args.memory_root, args.memory_id)
            print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
            return
        records = list_memories(
            args.memory_root,
            args.workspace,
            namespace=args.namespace,
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
                f"{record.memory_id}\t{record.status}\t{record.kind}\t"
                f"{record.scope}\t{record.key}"
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

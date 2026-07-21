"""公开 ``forge`` 命令契约；只定义参数，不执行能力。"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator

from agent_forge.bench.presentation.cli import (
    build_campaign_parser,
    build_case_catalog_parser,
    build_case_inspection_parser,
    build_swebench_parser,
)
from agent_forge.multi_agent.profiles import list_profiles
from agent_forge.workbench.api import build_ui_parser


class _PublicHelpFormatter(argparse.HelpFormatter):
    """隐藏兼容命令的 help 行，同时保留其解析能力。"""

    def _iter_indented_subactions(
        self,
        action: argparse.Action,
    ) -> Iterator[argparse.Action]:
        for subaction in super()._iter_indented_subactions(action):
            if subaction.help != argparse.SUPPRESS:
                yield subaction


# 主要入口：构造完整 ``forge`` 命令树；所有 ``_add_*`` 都只是参数分组。
def build_parser() -> argparse.ArgumentParser:
    """构造按用户目标组织的命令面，不暴露内部 application 类。"""

    parser = argparse.ArgumentParser(
        prog="forge",
        description="NanoHarness: governed repository agent and benchmark workbench.",
        formatter_class=_PublicHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{run,inspect,demo,resume,bench,ui}",
    )
    _add_run_command(subparsers)
    _add_inspect_command(subparsers)
    _add_demo_command(subparsers)
    _add_resume_command(subparsers)
    _add_benchmark_command(subparsers)
    _add_evaluation_command(subparsers)
    _add_inspection_commands(subparsers)
    _add_memory_command(subparsers)
    ui_parser = subparsers.add_parser(
        "ui",
        help="Open the local browser workbench UI.",
    )
    build_ui_parser(ui_parser)
    return parser


def _add_inspect_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "inspect",
        help="Inspect a run, artifact, or source symbol through one read-only view.",
    )
    parser.add_argument("target", nargs="?", default="latest")


def _add_demo_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "demo",
        help="Run one deterministic governed-run story without an online model.",
    )
    parser.add_argument(
        "--scenario",
        choices=["approval", "hitl"],
        default="approval",
    )
    parser.add_argument("--answer", default="Python 3.11")
    parser.add_argument("--output-root", default=".agent_forge/showcases")


def _add_run_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "run",
        help="Run NanoHarness on a repository task.",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="Issue or coding task to solve; may also come from --config.",
    )
    parser.add_argument(
        "--config",
        help="Versioned YAML/JSON run configuration; explicit CLI options win.",
    )
    parser.add_argument("--workspace")
    _add_execution_environment_args(parser, defaults=False)
    _add_model_args(parser, defaults=False)
    _add_runtime_policy_args(parser, defaults=False)
    parser.add_argument(
        "--resume-state",
        default=None,
        help="Path to a task_state checkpoint JSON used to seed a safe continuation.",
    )
    parser.add_argument("--output-root")
    parser.add_argument(
        "--agent-mode",
        default=None,
        choices=["single", "multi", "fanout"],
    )
    _add_multi_agent_args(parser, defaults=False)
    parser.add_argument(
        "--fanout-plan",
        default=None,
        help="Validated JSON task DAG required by --agent-mode fanout.",
    )
    parser.add_argument(
        "--fanout-resume",
        default=None,
        help="Prior fanout run, summary, or checkpoint used to restore merged tasks.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum concurrent live fanout workers (bounded to 1-8).",
    )
    _add_extension_args(parser, defaults=False)


def _add_benchmark_command(subparsers: argparse._SubParsersAction) -> None:
    bench_parser = subparsers.add_parser("bench", help="Run benchmark loops.")
    bench_subparsers = bench_parser.add_subparsers(
        dest="bench_name",
        required=True,
    )
    swebench_parser = bench_subparsers.add_parser(
        "swebench",
        help="Generate SWE-bench predictions.",
    )
    build_swebench_parser(swebench_parser)
    cases_parser = bench_subparsers.add_parser(
        "cases",
        help="Explain a fixed benchmark set and why each case was selected.",
    )
    build_case_catalog_parser(cases_parser)
    case_parser = bench_subparsers.add_parser(
        "case",
        help="Inspect one case's task and test contract without running an agent.",
    )
    build_case_inspection_parser(case_parser)
    campaign_parser = bench_subparsers.add_parser(
        "campaign",
        help="Run or resume a repeated matched Smoke-5 runtime-preset campaign.",
    )
    build_campaign_parser(campaign_parser)


def _add_evaluation_command(subparsers: argparse._SubParsersAction) -> None:
    eval_parser = subparsers.add_parser(
        "eval",
        help=argparse.SUPPRESS,
    )
    eval_subparsers = eval_parser.add_subparsers(dest="eval_name", required=True)

    mini = eval_subparsers.add_parser(
        "mini-cases",
        help="Score small non-coding Agent application cases from explicit evidence.",
    )
    mini.add_argument("--case", default="all", help="Mini case id to run, or all.")
    mini.add_argument(
        "--evidence",
        help="JSON evidence file: one object or a dict keyed by case_id.",
    )
    mini.add_argument("--output-root", default=".agent_forge/mini_cases")

    feedback = eval_subparsers.add_parser(
        "feedback",
        help="Attach a human outcome and labels to one run or benchmark case.",
    )
    feedback.add_argument(
        "target",
        help="Run directory, case directory, or trace.json path.",
    )
    feedback.add_argument(
        "--outcome",
        required=True,
        choices=["accepted", "needs_work", "rejected"],
    )
    feedback.add_argument("--label", action="append", default=[])
    feedback.add_argument("--note", default="")
    feedback.add_argument("--reviewer", default="human")

    export = eval_subparsers.add_parser(
        "export-dataset",
        help="Export trace, policy, evaluation, and human-feedback evidence as JSONL.",
    )
    export.add_argument(
        "target",
        nargs="+",
        help="One or more run or case directories.",
    )
    export.add_argument(
        "--output",
        default=".agent_forge/evaluation/evidence_dataset.jsonl",
    )
    export.add_argument("--require-feedback", action="store_true")
    export.add_argument(
        "--include-patch",
        action="store_true",
        help="Include candidate patch text; default export keeps size and SHA-256 only.",
    )

    ablation = eval_subparsers.add_parser(
        "ablation",
        help="Compare two matched benchmark run scorecards as a paired ablation.",
    )
    ablation.add_argument("control", help="Control benchmark run directory.")
    ablation.add_argument("treatment", help="Treatment benchmark run directory.")
    ablation.add_argument(
        "--factor",
        required=True,
        help="Single runtime factor changed between runs.",
    )
    ablation.add_argument("--control-label", default="control")
    ablation.add_argument("--treatment-label", default="treatment")
    ablation.add_argument("--output", default=".agent_forge/evaluation/ablation")


def _add_inspection_commands(subparsers: argparse._SubParsersAction) -> None:
    skills = subparsers.add_parser(
        "skills",
        help=argparse.SUPPRESS,
    )
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)
    list_parser = skill_commands.add_parser(
        "list",
        help="List registered Skill versions.",
    )
    list_parser.add_argument("--manifest", action="append", default=[])
    list_parser.add_argument("--name", help="Filter to one skill name.")
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of a table.",
    )
    list_parser.add_argument(
        "--no-builtins",
        action="store_true",
        help="Show only manifests passed with --manifest.",
    )
    subparsers.add_parser(
        "doctor",
        help=argparse.SUPPRESS,
    )


def _add_memory_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "memory",
        help=argparse.SUPPRESS,
    )
    commands = parser.add_subparsers(dest="memory_command", required=True)

    propose = commands.add_parser(
        "propose",
        help="Create a candidate that is not recalled until promoted.",
    )
    propose.add_argument("--workspace", default=".")
    propose.add_argument(
        "--namespace",
        default="",
        help="Stable logical namespace; defaults to the resolved workspace path.",
    )
    propose.add_argument("--memory-root", default=".agent_forge/memory")
    propose.add_argument("--key", required=True)
    propose.add_argument(
        "--kind",
        required=True,
        choices=["fact", "decision", "constraint", "preference", "failure_pattern"],
    )
    propose.add_argument("--content", required=True)
    propose.add_argument(
        "--scope",
        default="workspace",
        choices=["workspace", "agent_private"],
    )
    propose.add_argument("--agent-name", default="")
    propose.add_argument("--confidence", type=float, default=0.5)
    propose.add_argument("--importance", type=float, default=0.5)
    propose.add_argument("--tag", action="append", default=[])
    propose.add_argument("--ttl-seconds", type=float)

    promote = commands.add_parser(
        "promote",
        help="Promote a candidate with one or more evidence references.",
    )
    promote.add_argument("memory_id")
    promote.add_argument("--memory-root", default=".agent_forge/memory")
    promote.add_argument("--evidence", action="append", required=True)

    for command_name in ["retire", "reject"]:
        command = commands.add_parser(command_name)
        command.add_argument("memory_id")
        command.add_argument("--memory-root", default=".agent_forge/memory")

    list_parser = commands.add_parser("list")
    list_parser.add_argument("--workspace")
    list_parser.add_argument("--namespace", default="")
    list_parser.add_argument("--memory-root", default=".agent_forge/memory")
    list_parser.add_argument("--json", action="store_true")


def _add_resume_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "resume",
        help="Resume from the latest checkpoint under a run directory.",
    )
    parser.add_argument("run_dir", help="Previous NanoHarness run directory.")
    parser.add_argument("--task", help="Override the continuation task.")
    parser.add_argument("--workspace", help="Override the checkpoint workspace.")
    parser.add_argument(
        "--answer",
        help="Answer the run's pending human-input request before continuing.",
    )
    parser.add_argument(
        "--request-id",
        default="",
        help="Select one request when the control root contains multiple pending inputs.",
    )
    parser.add_argument(
        "--decision",
        choices=["approved", "rejected"],
        help="Resolve the run's pending approval before continuing.",
    )
    parser.add_argument(
        "--operation-key",
        default="",
        help="Select one operation when multiple approvals are pending.",
    )
    parser.add_argument("--note", default="")
    _add_execution_environment_args(parser)
    _add_model_args(parser)
    _add_runtime_policy_args(parser)
    parser.add_argument("--output-root", default=".agent_forge/runs")
    parser.add_argument(
        "--agent-mode",
        default="single",
        choices=["single", "multi"],
    )
    _add_multi_agent_args(parser)
    _add_extension_args(parser)


def _add_model_args(
    parser: argparse.ArgumentParser,
    *,
    defaults: bool = True,
) -> None:
    parser.add_argument(
        "--provider",
        default=os.getenv("AGENT_FORGE_DEFAULT_LLM", "deepseek") if defaults else None,
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0 if defaults else None,
        help="Sampling temperature sent to the model (0.0-2.0).",
    )
    parser.add_argument("--max-steps", type=int, default=16 if defaults else None)
    parser.add_argument(
        "--max-context-chars",
        type=int,
        default=12000 if defaults else None,
    )
    parser.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=32768 if defaults else None,
        help="Total model context window used by request budgeting.",
    )
    parser.add_argument(
        "--reserved-output-tokens",
        type=int,
        default=4096 if defaults else None,
        help="Tokens reserved for model output before compacting history.",
    )
    parser.add_argument(
        "--model-context-window",
        type=int,
        default=32768 if defaults else None,
        help="Provider/model context capacity used to cap the runtime prompt budget.",
    )
    for option, destination, help_text in (
        ("native-tool-calling", "native_tool_calling", "native structured tool calls"),
        ("parallel-tool-calls", "parallel_tool_calls", "multiple tool calls per turn"),
        ("structured-output", "structured_output", "provider structured output"),
        ("reasoning-tokens", "reasoning_tokens", "separate reasoning tokens"),
        ("prompt-cache", "prompt_cache", "provider prompt caching"),
        ("supports-images", "supports_images", "image input"),
    ):
        parser.add_argument(
            f"--{option}",
            dest=destination,
            action=argparse.BooleanOptionalAction,
            default=(
                destination in {"native_tool_calling", "parallel_tool_calls"}
                if defaults
                else None
            ),
            help=f"Declare whether the selected model supports {help_text}.",
        )


def _add_runtime_policy_args(
    parser: argparse.ArgumentParser,
    *,
    defaults: bool = True,
) -> None:
    _add_tool_routing_arg(parser, defaults=defaults)
    parser.add_argument(
        "--approval-mode",
        default="trusted" if defaults else None,
        choices=["trusted", "on-write", "on-risk", "locked", "dry-run"],
    )
    parser.add_argument(
        "--auto-approve-writes",
        action=argparse.BooleanOptionalAction,
        default=True if defaults else None,
        help="Auto-approve writes; use --no-auto-approve-writes to persist a pending request.",
    )
    parser.add_argument(
        "--approval-root",
        default=".agent_forge/approvals" if defaults else None,
    )
    parser.add_argument(
        "--human-input-root",
        default=".agent_forge/human_input" if defaults else None,
    )
    parser.add_argument(
        "--operation-ledger-root",
        default=".agent_forge/operation_ledger" if defaults else None,
    )
    parser.add_argument(
        "--memory-root",
        default=".agent_forge/memory" if defaults else None,
    )
    parser.add_argument(
        "--memory-recall-limit",
        type=int,
        default=6 if defaults else None,
        help="Maximum active long-term memories injected into one run.",
    )
    parser.add_argument(
        "--max-tool-calls-per-turn",
        type=int,
        default=4 if defaults else None,
        help="Bound tool-call bursts from unstable model responses.",
    )
    parser.add_argument(
        "--cost-budget-usd",
        type=float,
        help="Stop one AgentLoop after its cumulative estimated model cost exceeds this value.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=900.0 if defaults else None,
        help="Wall-clock budget for one AgentLoop.",
    )
    parser.add_argument("--instruction-target", default="" if defaults else None)
    parser.add_argument(
        "--global-instruction-file",
        action="append",
        default=[] if defaults else None,
    )
    parser.add_argument("--runtime-instructions", default="" if defaults else None)
    parser.add_argument(
        "--instruction-max-bytes",
        type=int,
        default=2600 if defaults else None,
    )


def _add_multi_agent_args(
    parser: argparse.ArgumentParser,
    *,
    defaults: bool = True,
) -> None:
    parser.add_argument(
        "--profile",
        default="coding_fix" if defaults else None,
        choices=list_profiles(),
    )
    parser.add_argument(
        "--max-revision-rounds",
        type=int,
        default=2 if defaults else None,
    )


def _add_extension_args(
    parser: argparse.ArgumentParser,
    *,
    defaults: bool = True,
) -> None:
    parser.add_argument(
        "--skills",
        default="auto" if defaults else None,
        help="auto, none, or comma-separated built-in/custom skill names.",
    )
    parser.add_argument(
        "--skill-manifest",
        action="append",
        default=[] if defaults else None,
    )
    parser.add_argument("--mcp-config")
    parser.add_argument(
        "--mcp-tool",
        action="append",
        default=[] if defaults else None,
    )
    parser.add_argument(
        "--tool",
        dest="enabled_tools",
        action="append",
        default=None,
        help="Restrict the built-in registry to repeated explicit tool names.",
    )


def _add_execution_environment_args(
    parser: argparse.ArgumentParser,
    *,
    defaults: bool = True,
) -> None:
    parser.add_argument(
        "--execution-mode",
        choices=["local", "worktree", "container"],
        default="local" if defaults else None,
        help="Run in the checkout, an isolated git worktree, or an OCI container snapshot.",
    )
    parser.add_argument(
        "--network-policy",
        choices=["deny", "allow"],
        default="deny" if defaults else None,
    )
    parser.add_argument(
        "--keep-worktree",
        action=argparse.BooleanOptionalAction,
        default=True if defaults else None,
    )
    parser.add_argument(
        "--container-runtime",
        default="docker" if defaults else None,
    )
    parser.add_argument(
        "--container-image",
        default="python:3.11-slim" if defaults else None,
    )
    parser.add_argument(
        "--container-cpus",
        type=float,
        default=1.0 if defaults else None,
    )
    parser.add_argument(
        "--container-memory",
        default="1g" if defaults else None,
    )
    parser.add_argument(
        "--container-pids-limit",
        type=int,
        default=256 if defaults else None,
    )
    parser.add_argument(
        "--container-read-only",
        action=argparse.BooleanOptionalAction,
        default=True if defaults else None,
    )


def _add_tool_routing_arg(
    parser: argparse.ArgumentParser,
    *,
    defaults: bool = True,
) -> None:
    parser.add_argument(
        "--tool-routing",
        choices=["task-aware", "all"],
        default="task-aware" if defaults else None,
        help="Change model-visible schemas for ablation; runtime safety remains enabled.",
    )

"""兼容入口：公开 CLI 已拆到 ``agent_forge.cli`` 入站适配器。"""

from agent_forge.cli.dispatch import main, run_mini_cases_from_args
from agent_forge.cli.inspection import (
    print_report,
    print_skills,
    render_doctor,
    resolve_report_target,
    resolve_trace_target,
    run_tui,
)
from agent_forge.cli.operator import approve_request, respond_to_human_input
from agent_forge.cli.parser import build_parser
from agent_forge.cli.repository import (
    parse_skill_mode as _parse_skill_mode,
    parse_skill_names as _parse_skill_names,
    prepare_execution_environment,
    run_repository_task,
)
from agent_forge.cli.resume import (
    checkpoint_resume_workspace,
    continuation_task_with_human_response,
    latest_checkpoint_path,
    resume_repository_task,
    write_resume_link,
)

__all__ = [
    "approve_request",
    "build_parser",
    "checkpoint_resume_workspace",
    "continuation_task_with_human_response",
    "latest_checkpoint_path",
    "main",
    "prepare_execution_environment",
    "print_report",
    "print_skills",
    "render_doctor",
    "resolve_report_target",
    "resolve_trace_target",
    "respond_to_human_input",
    "resume_repository_task",
    "run_mini_cases_from_args",
    "run_repository_task",
    "run_tui",
    "write_resume_link",
]


if __name__ == "__main__":
    main()

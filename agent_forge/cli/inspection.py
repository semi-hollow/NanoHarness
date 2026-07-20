"""只读 CLI：环境检查、报告、trace、Skill registry 与轻量 TUI。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from agent_forge.bench.presentation.cli import run_swebench_from_args
from agent_forge.code_compass import inspect_symbol, render_symbol_card
from agent_forge.cli.repository import run_repository_task
from agent_forge.observability.api import (
    load_run_story,
    read_run_manifest,
    render_run_story,
    replay_trace_file,
)
from agent_forge.skills import SkillRegistry, build_default_skill_registry


def render_doctor() -> str:
    """返回适合 runtime/benchmark 的紧凑环境报告。"""

    rows = [
        ("python", sys.version.split()[0]),
        ("platform", f"{platform.system()} {platform.machine()}"),
        ("cwd", str(Path.cwd())),
        ("git", _command_version(["git", "--version"])),
        ("docker", _command_version(["docker", "--version"])),
        (
            "datasets",
            "installed"
            if importlib.util.find_spec("datasets")
            else "missing; install with python -m pip install -e '.[bench]'",
        ),
        (
            "swebench",
            "installed"
            if importlib.util.find_spec("swebench")
            else "missing; needed only for official --evaluate",
        ),
        ("DEEPSEEK_API_KEY", "set" if os.getenv("DEEPSEEK_API_KEY") else "missing"),
        ("AGENT_FORGE_BASE_URL", os.getenv("AGENT_FORGE_BASE_URL", "")),
        ("AGENT_FORGE_MODEL", os.getenv("AGENT_FORGE_MODEL", "")),
    ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name:<{width}} : {value}" for name, value in rows)


def print_report(target: str) -> None:
    report = resolve_report_target(target)
    print(report.read_text(encoding="utf-8"))


# 主要入口：用一个目标参数检查 run、artifact 或源码符号。
def render_inspection(target: str = "latest") -> str:
    """自动识别 inspection target，避免 report/replay/code-map 多套入口。"""

    if target == "latest":
        path = _latest_inspection_target()
    else:
        path = Path(target)

    if path.exists():
        if path.name == "run_manifest.json":
            path = path.parent
        if path.is_dir() and (path / "run_manifest.json").exists():
            return render_run_story(load_run_story(path))
        if path.is_file():
            manifest_root = _manifest_root(path)
            if manifest_root is not None:
                return _render_artifact_card(path, manifest_root)
            if path.name == "trace.json":
                return replay_trace_file(path)
            return path.read_text(encoding="utf-8")
        try:
            return resolve_report_target(str(path)).read_text(encoding="utf-8")
        except SystemExit:
            trace_path = resolve_trace_target(str(path))
            if trace_path.exists():
                return replay_trace_file(trace_path)
            raise

    try:
        return render_symbol_card(inspect_symbol(target))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def resolve_report_target(target: str) -> Path:
    """解析 ``latest``、run 目录或显式 report 文件。"""

    if target == "latest":
        target = str(_latest_pointer_target())
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
    """解析 ``latest``、run 目录或显式 trace 文件。"""

    if target == "latest":
        run_dir = _latest_pointer_target()
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
    """只读展示 Skill 版本、权限、依赖和回滚目标。"""

    manifests = args.manifest or []
    registry = (
        SkillRegistry()
        if args.no_builtins
        else build_default_skill_registry([])
    )
    try:
        if manifests:
            registry.load_manifests(manifests)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    specs = registry.list_specs(name=args.name)
    if args.json:
        print(json.dumps([spec.to_dict() for spec in specs], ensure_ascii=False, indent=2))
        return
    if not specs:
        print("No skills found.")
        return

    print("Skill Registry")
    for spec in specs:
        rollback = registry.rollback_target(spec.name, spec.version)
        rollback_label = f"{rollback.name}@{rollback.version}" if rollback else "-"
        print(f"- {spec.name}@{spec.version}")
        print(f"  owner       : {spec.owner or '-'}")
        print(f"  entrypoint  : {spec.entrypoint}")
        print(f"  permissions : {', '.join(spec.permissions) or '-'}")
        print(f"  dependencies: {', '.join(spec.dependencies) or '-'}")
        print(f"  rollback_to : {rollback_label}")
        print(f"  tags        : {', '.join(spec.tags) or '-'}")
        print(f"  tools       : {', '.join(spec.tool_names) or '-'}")


def run_tui() -> None:
    """为不记命令的本地用户提供轻量入口，不承诺完整终端产品。"""

    print("NanoHarness")
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


def _latest_pointer_target() -> Path:
    pointer = Path(".agent_forge/latest/bench.txt")
    if not pointer.exists():
        pointer = Path(".agent_forge/latest/run.txt")
    if not pointer.exists():
        raise SystemExit("No latest run pointer found.")
    return Path(pointer.read_text(encoding="utf-8").strip())


def _latest_inspection_target() -> Path:
    """优先选择 canonical single run；缺失时兼容 benchmark pointer。"""

    for pointer in (
        Path(".agent_forge/latest/run.txt"),
        Path(".agent_forge/latest/bench.txt"),
    ):
        if pointer.exists():
            return Path(pointer.read_text(encoding="utf-8").strip())
    raise SystemExit("No latest run pointer found.")


def _manifest_root(path: Path) -> Path | None:
    current = path.resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "run_manifest.json").exists():
            return candidate
    return None


def _render_artifact_card(path: Path, run_dir: Path) -> str:
    relative = path.resolve().relative_to(run_dir.resolve()).as_posix()
    manifest = read_run_manifest(run_dir / "run_manifest.json")
    artifact = next(
        (
            item
            for item in manifest.artifacts
            if item.relative_path == relative
        ),
        None,
    )
    if artifact is None:
        raise SystemExit(f"Artifact is not registered in run manifest: {relative}")
    lines = [
        "# Artifact Lineage",
        "",
        f"- path: `{artifact.relative_path}`",
        f"- kind / level: `{artifact.kind}` / `{artifact.evidence_level}`",
        f"- producer: `{artifact.producer_symbol}`",
        f"- flow stage: `{artifact.flow_stage}`",
        f"- consumers: `{', '.join(artifact.semantic_consumers) or '-'}`",
        f"- derived from: `{', '.join(artifact.derived_from) or '-'}`",
        f"- rebuildable: `{str(artifact.rebuildable).lower()}`",
        f"- deletion impact: {artifact.deletion_impact or '未登记'}",
        f"- sha256: `{artifact.sha256}`",
        f"- proves: {'；'.join(artifact.proves) or '没有登记正向 claim'}",
        (
            "- does not prove: "
            f"{'；'.join(artifact.does_not_prove) or '没有登记边界'}"
        ),
    ]
    return "\n".join(lines) + "\n"


def _command_version(command: list[str]) -> str:
    if shutil.which(command[0]) is None:
        return "missing"
    result = subprocess.run(command, text=True, capture_output=True)
    return (result.stdout or result.stderr).strip().splitlines()[0]

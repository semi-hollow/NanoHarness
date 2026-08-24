"""Usage read model 的文件读取与派生 artifact 发布 Adapter。

系统角色：严格读取最终 ``trace.json``，计算稳定 usage.json/report 路径，并原子发布 JSON；
指标本身由 Domain projector 产生。
输入：Trace path/usage mapping/rendered Markdown；输出：两个派生 artifact 路径。

折叠导航：1 trace read；2 path contract；3 publish。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# region 1. 严格读取最终 trace
def read_trace(path: Path) -> dict[str, Any]:

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read trace: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid trace JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"trace must contain a JSON object: {path}")
    return data
# endregion 1. Strict final trace read 结束


# region 2. Artifact 路径契约
def usage_artifact_paths(
    trace_file: Path,
    output_dir: str | Path | None = None,
) -> tuple[Path, Path]:

    target_dir = Path(output_dir) if output_dir else trace_file.parent
    if trace_file.name == "trace.json":
        return target_dir / "usage.json", target_dir / "usage_report.md"
    return (
        target_dir / f"{trace_file.stem}.usage.json",
        target_dir / f"{trace_file.stem}.usage_report.md",
    )
# endregion 2. Artifact path contract 结束


# region 3. 发布派生 usage artifacts
def write_usage_files(
    usage: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
    markdown: str,
) -> tuple[Path, Path]:

    json_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = json_path.with_suffix(json_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(usage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(json_path)
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path
# endregion 3. Publish derived usage artifacts 结束

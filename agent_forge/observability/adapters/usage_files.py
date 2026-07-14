from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

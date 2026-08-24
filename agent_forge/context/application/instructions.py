"""分层项目指令的发现、优先级合并和 provenance。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from agent_forge.contracts import JsonObject


BASE_INSTRUCTION_FILES = (
    "PROJECT_INSTRUCTIONS.md",
    "CLAUDE.md",
    "AGENTS.md",
    "FORGE.md",
)
LOCAL_OVERRIDE_FILES = (
    "CLAUDE.local.md",
    "AGENTS.override.md",
    "FORGE.local.md",
)


# 核心数据：一个指令来源的身份、优先级和预算结果。
@dataclass(frozen=True)
class InstructionSource:
    """``path/kind/precedence`` 定位来源，其余字段证明内容是否完整进入上下文。"""

    path: str
    kind: str
    precedence: int
    sha256: str
    original_bytes: int
    included_bytes: int
    truncated: bool
    content: str

    def to_evidence(self) -> JsonObject:
        """返回不包含指令正文的 trace 证据。"""

        return {
            "path": self.path,
            "kind": self.kind,
            "precedence": self.precedence,
            "sha256": self.sha256,
            "original_bytes": self.original_bytes,
            "included_bytes": self.included_bytes,
            "truncated": self.truncated,
        }


# 核心数据：一次指令解析的模型输入与完整来源证据。
@dataclass(frozen=True)
class InstructionResolution:
    """``content`` 是最终合并文本，``sources`` 按低到高优先级排列。"""

    content: str
    sources: tuple[InstructionSource, ...]
    precedence: tuple[str, ...]
    included_bytes: int
    max_bytes: int
    truncated: bool

    def to_evidence(self) -> JsonObject:
        """返回 Context trace 使用的稳定 JSON 结构。"""

        return {
            "sources": [source.to_evidence() for source in self.sources],
            "precedence": list(self.precedence),
            "bytes": self.included_bytes,
            "max_bytes": self.max_bytes,
            "truncated": self.truncated,
        }


# 核心数据：Instruction Resolver 的工作区、目标目录、覆盖和预算输入。
@dataclass(frozen=True)
class InstructionResolutionRequest:
    """显式 global 文件可在仓库外；自动发现的项目指令必须留在 ``workspace`` 内。"""

    workspace: str | Path
    active_path: str | Path = ""
    global_files: tuple[str, ...] = ()
    runtime_override: str = ""
    max_bytes: int = 2_600


# 主要入口：发现并按稳定优先级合并项目指令，同时返回 provenance。
def resolve_instructions(request: InstructionResolutionRequest) -> InstructionResolution:
    """发现并合并全局、仓库、目录、本地和运行时指令，同时保留来源证据。

    自动发现限定在 workspace 内；预算从高优先级来源向低优先级来源分配。
    返回正文及 path、hash、字节数和截断信息，不执行指令本身。

    伪代码：验证 workspace/global source -> 沿 repo root 到 active directory 收集
    base/local/runtime override -> 高优先级先分配 byte budget -> 返回正文与 provenance。
    """

    # region 1. 输入边界：解析工作区，并验证显式 global source
    if request.max_bytes < 1:
        raise ValueError("instruction max_bytes must be positive")
    workspace = Path(request.workspace).resolve()
    active_directory = _active_directory(workspace, request.active_path)
    discovered: list[tuple[str, str, str]] = []
    seen: set[Path] = set()
    for raw_path in request.global_files:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"global instruction file not found: {path}")
        discovered.append((str(path), "global", _read(path)))
        try:
            path.relative_to(workspace)
        except ValueError:
            pass
        else:
            seen.add(path)
    # endregion 1. 输入边界结束

    # region 2. 稳定发现：从仓库根逐层走到 active directory，再加入运行时覆盖
    # 越靠近 active directory、越晚发现的 local/runtime source 优先级越高；seen 保证
    # global 文件若恰好位于 workspace 内，不会在目录扫描时重复进入模型输入。
    directories = [workspace, *_relative_directories(workspace, active_directory)]
    for index, directory in enumerate(directories):
        kind = "repository" if index == 0 else "directory"
        for name in BASE_INSTRUCTION_FILES:
            _append_workspace_file(discovered, seen, workspace, directory / name, kind)
        for name in LOCAL_OVERRIDE_FILES:
            _append_workspace_file(
                discovered,
                seen,
                workspace,
                directory / name,
                "local_override",
            )
    if request.runtime_override.strip():
        discovered.append(
            ("<runtime_override>", "runtime_override", request.runtime_override.strip())
        )
    # endregion 2. 稳定发现结束

    # region 3. 预算与证据：优先保住高优先级正文，再恢复稳定展示顺序
    sources = _allocate_sources(discovered, request.max_bytes)
    blocks = [
        f"[instruction:{source.kind} path={source.path}]\n{source.content}"
        for source in sources
        if source.included_bytes
    ]
    content = "\n\n".join(blocks)
    return InstructionResolution(
        content=content
        or "No project instruction file was found; follow built-in runtime policy.",
        sources=tuple(sources),
        precedence=tuple(f"{source.kind}:{source.path}" for source in sources),
        included_bytes=sum(source.included_bytes for source in sources),
        max_bytes=request.max_bytes,
        truncated=any(source.truncated for source in sources),
    )
    # endregion 3. 预算与证据结束


def _active_directory(workspace: Path, active_path: str | Path) -> Path:
    candidate = Path(active_path) if active_path else workspace
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve()
    if resolved.is_file():
        resolved = resolved.parent
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("instruction active_path escapes workspace") from exc
    return resolved


def _relative_directories(workspace: Path, active_directory: Path) -> list[Path]:
    relative = active_directory.relative_to(workspace)
    current = workspace
    result: list[Path] = []
    for part in relative.parts:
        current = current / part
        result.append(current)
    return result


def _append_workspace_file(
    discovered: list[tuple[str, str, str]],
    seen: set[Path],
    workspace: Path,
    candidate: Path,
    kind: str,
) -> None:
    if not candidate.exists():
        return
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"instruction file escapes workspace: {candidate}") from exc
    if not resolved.is_file() or resolved in seen:
        return
    seen.add(resolved)
    discovered.append((str(resolved), kind, _read(resolved)))


def _allocate_sources(
    discovered: list[tuple[str, str, str]],
    max_bytes: int,
) -> list[InstructionSource]:
    """让高优先级来源先领取预算，同时保持返回列表的稳定优先级顺序。"""

    remaining = max_bytes
    allocated: list[InstructionSource] = []
    # discovered 越靠后优先级越高，因此反向遍历先分预算；完成后再反转回来，
    # 使渲染顺序与 provenance precedence 都保持从低到高、可重复比较。
    for precedence, (path, kind, content) in reversed(list(enumerate(discovered))):
        raw = content.encode("utf-8")
        included = _truncate_utf8(content, remaining)
        included_bytes = len(included.encode("utf-8"))
        remaining -= included_bytes
        allocated.append(
            InstructionSource(
                path=path,
                kind=kind,
                precedence=precedence,
                sha256=hashlib.sha256(raw).hexdigest(),
                original_bytes=len(raw),
                included_bytes=included_bytes,
                truncated=included_bytes < len(raw),
                content=included,
            )
        )
    return list(reversed(allocated))


def _truncate_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()

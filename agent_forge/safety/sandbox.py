from pathlib import Path


class WorkspaceSandbox:
    """把工具路径规范化到 workspace，并拒绝越界、符号链接逃逸和敏感文件。

    本类只治理文件路径；命令内容和进程运行环境分别由 CommandPolicy 与
    ``ExecutionEnvironment`` 负责。
    """

    def __init__(self, workspace_root: str | Path) -> None:

        self.workspace_root = Path(workspace_root).resolve()

    def resolve_path(self, path: str | Path) -> Path:

        p = Path(path)
        if not p.is_absolute():
            p = self.workspace_root / p
        return p.resolve()

    def is_sensitive_path(self, path: Path) -> bool:

        lowered_parts = [part.lower() for part in path.parts]
        name = path.name.lower()
        return (
            name == ".env"
            or "id_rsa" in name
            or name.endswith(".pem")
            or name.endswith(".key")
            or "credentials" in lowered_parts
            or "secrets" in lowered_parts
            or any("credentials" in part for part in lowered_parts)
            or any("secrets" in part for part in lowered_parts)
        )

    # 运行时端口：解析路径并拒绝 workspace 外部、符号链接逃逸等访问。
    def ensure_safe_path(self, path: str | Path) -> Path:
        """返回规范化安全路径；越界时抛出 ``PermissionError``。"""

        resolved = self.resolve_path(path)
        try:

            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("external_directory deny") from exc

        if self.is_sensitive_path(resolved):
            raise PermissionError("sensitive file deny")

        return resolved

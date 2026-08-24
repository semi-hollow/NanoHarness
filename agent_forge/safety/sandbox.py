"""所有文件 Tool 共用的 workspace path boundary。

系统角色：把相对/绝对输入解析成 canonical path，并拒绝 workspace escape、symlink escape
与敏感文件；它不判断命令或业务权限。
输入：workspace root + candidate path；输出：安全 resolved path 或 ``PermissionError``。

折叠导航：1 resolve；2 sensitive classification；3 enforce。
"""

from pathlib import Path


class WorkspaceSandbox:
    """把工具路径规范化到 workspace，并拒绝越界、符号链接逃逸和敏感文件。

    本类只治理文件路径；命令内容和进程运行环境分别由 CommandPolicy 与
    ``ExecutionEnvironment`` 负责。
    """

    def __init__(self, workspace_root: str | Path) -> None:

        self.workspace_root = Path(workspace_root).resolve()

    # region 1. Canonical 路径解析
    def resolve_path(self, path: str | Path) -> Path:

        p = Path(path)
        if not p.is_absolute():
            p = self.workspace_root / p
        return p.resolve()
    # endregion 1. Canonical path resolution 结束

    # region 2. 敏感路径分类
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
    # endregion 2. Sensitive path classification 结束

    # region 3. 强制 workspace 与敏感路径边界
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
    # endregion 3. Enforce boundaries 结束

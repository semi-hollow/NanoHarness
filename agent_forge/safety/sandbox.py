from pathlib import Path


def sandbox_policy_summary(workspace_root: str) -> dict[str, object]:

    return {
        "workspace_root": workspace_root,
        "path_escape_allowed": False,
        "side_effect_scope": "workspace-only",
    }


class WorkspaceSandbox:

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

    # 运行时端口：下方定义连接用例与外部实现。
    def ensure_safe_path(self, path: str | Path) -> Path:

        resolved = self.resolve_path(path)
        try:

            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("external_directory deny") from exc

        if self.is_sensitive_path(resolved):
            raise PermissionError("sensitive file deny")

        return resolved

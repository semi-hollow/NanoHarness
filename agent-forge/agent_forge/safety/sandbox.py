from pathlib import Path


class WorkspaceSandbox:
    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    def resolve_path(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = (self.workspace_root / p).resolve()
        else:
            p = p.resolve()
        return p

    def is_sensitive_path(self, path: Path) -> bool:
        name = path.name.lower()
        return (
            name == ".env"
            or "id_rsa" in name
            or name.endswith(".pem")
            or name.endswith(".key")
            or "credentials" in name
            or "secrets" in name
        )

    def ensure_safe_path(self, path: str | Path) -> Path:
        resolved = self.resolve_path(path)
        if not str(resolved).startswith(str(self.workspace_root)):
            raise PermissionError("external_directory deny")
        if self.is_sensitive_path(resolved):
            raise PermissionError("sensitive file deny")
        return resolved

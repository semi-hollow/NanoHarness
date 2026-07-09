from pathlib import Path


def sandbox_policy_summary(workspace_root: str) -> dict[str, object]:
    """Return report-friendly workspace boundary facts."""

    return {
        "workspace_root": workspace_root,
        "path_escape_allowed": False,
        "side_effect_scope": "workspace-only",
    }


class WorkspaceSandbox:
    """Path-level safety boundary for all file tools.

    This is the local version of "agent sandboxing": every file path produced by
    the model is resolved under one workspace root and checked for secret-like
    targets before tools read or write it.
    """

    def __init__(self, workspace_root: str | Path):
        """Resolve the workspace once so later checks compare absolute paths."""

        self.workspace_root = Path(workspace_root).resolve()

    def resolve_path(self, path: str | Path) -> Path:
        """Convert a user/tool path into an absolute path under workspace root."""

        p = Path(path)
        if not p.is_absolute():
            p = self.workspace_root / p
        return p.resolve()

    def is_sensitive_path(self, path: Path) -> bool:
        """Block common secret-bearing filenames and directories."""

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

    def ensure_safe_path(self, path: str | Path) -> Path:
        """Return a resolved path or raise if it escapes/sensitively targets."""

        resolved = self.resolve_path(path)
        try:
            # `relative_to` is the key escape check. It blocks ../ and absolute
            # paths outside the workspace after symlink/path normalization.
            resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError("external_directory deny") from exc

        if self.is_sensitive_path(resolved):
            raise PermissionError("sensitive file deny")

        return resolved

"""兼容导入：ArtifactStore 已迁移到 adapters。"""

from .adapters.artifact_files import ArtifactStore, FileArtifactRepository

__all__ = ["ArtifactStore", "FileArtifactRepository"]

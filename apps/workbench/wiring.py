from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.workbench.adapters.evidence_files import (
    FileEvidenceCatalog,
    read_json_file,
)
from apps.workbench.adapters.experiment_files import FileExperimentCatalog
from apps.workbench.application.services import WorkbenchServices
from apps.workbench.ports import EvidenceCatalogPort, ExperimentCatalogPort


def build_evidence_catalog(project_dir: Path) -> EvidenceCatalogPort:
    return FileEvidenceCatalog(project_dir)


def build_experiment_catalog(project_dir: Path) -> ExperimentCatalogPort:
    return FileExperimentCatalog(project_dir)


def build_workbench_services(project_dir: Path) -> WorkbenchServices:
    return WorkbenchServices(
        project_dir=project_dir,
        evidence=FileEvidenceCatalog(project_dir),
        experiments=FileExperimentCatalog(project_dir),
    )


def read_evidence_json(path: Path | None) -> dict[str, Any]:
    return read_json_file(path)

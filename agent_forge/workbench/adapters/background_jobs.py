from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from agent_forge.workbench.domain.models import WorkbenchCommand, WorkbenchJob


class BackgroundJobRunner:

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.jobs: dict[str, WorkbenchJob] = {}
        self._lock = threading.Lock()

    def start_job(self, command: WorkbenchCommand) -> WorkbenchJob:
        job = WorkbenchJob(
            id=uuid.uuid4().hex[:10],
            title=command.title,
            command=command.command,
            display_command=command.display_command or command.command,
            env_overrides=command.env,
        )
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return job

    def get_job(self, job_id: str) -> WorkbenchJob | None:
        with self._lock:
            return self.jobs.get(job_id)

    def latest_jobs(self) -> list[WorkbenchJob]:
        with self._lock:
            return sorted(
                self.jobs.values(),
                key=lambda job: job.started_at,
                reverse=True,
            )[:20]

    def _run_job(self, job: WorkbenchJob) -> None:
        try:
            env = os.environ.copy()
            env.update(job.env_overrides)
            process = subprocess.run(
                job.command,
                cwd=self.project_dir,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            job.exit_code = process.returncode
            job.output = process.stdout
            job.status = "succeeded" if process.returncode == 0 else "failed"
        except Exception as exc:
            job.exit_code = -1
            job.output = str(exc)
            job.status = "failed"
        finally:
            job.finished_at = time.time()

UiState = BackgroundJobRunner

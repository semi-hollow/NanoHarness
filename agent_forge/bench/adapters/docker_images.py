"""只操作精确 tag 的 Docker evaluator-image 运行时。"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable

from agent_forge.bench.application.campaign_lifecycle import (
    ExactImageIdentity,
    ExactImageRuntimePort,
    validate_exact_image_coordinates,
)


class DockerExactImageRuntime(ExactImageRuntimePort):
    """以 argv 调用 Docker；不会按 image id 删除，也不会执行 broad prune。"""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        executable_resolver: Callable[[str], str | None] = shutil.which,
    ) -> None:
        executable = executable_resolver("docker")
        if not executable:
            raise RuntimeError("docker executable is unavailable")
        self._executable = executable
        self._runner = runner

    def inspect(self, tag: str) -> ExactImageIdentity | None:
        validate_exact_image_coordinates(tag, "linux/amd64")
        process = self._run(["image", "inspect", tag], timeout=60)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).lower()
            if "no such image" in detail or "no such object" in detail:
                return None
            raise RuntimeError(
                f"docker image inspect failed (exit {process.returncode})"
            )
        try:
            payload = json.loads(process.stdout)
            record = payload[0]
            image_id = str(record["Id"])
            repository = tag.rsplit(":", 1)[0]
            matches = sorted(
                {
                    str(item)
                    for item in record["RepoDigests"]
                    if str(item).startswith(f"{repository}@sha256:")
                }
            )
            platform = f"{record['Os']}/{record['Architecture']}"
            variant = str(record.get("Variant") or "")
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError(
                "docker image inspect returned invalid identity"
            ) from exc
        if len(matches) != 1:
            raise RuntimeError("docker image inspect returned ambiguous RepoDigests")
        if variant:
            platform = f"{platform}/{variant}"
        return {
            "tag": tag,
            "repo_digest": matches[0],
            "image_id": image_id,
            "platform": platform,
        }

    def pull(self, tag: str, platform: str) -> None:
        validate_exact_image_coordinates(tag, platform)
        process = self._run(
            ["pull", "--platform", platform, tag],
            timeout=3600,
        )
        if process.returncode != 0:
            raise RuntimeError(f"docker image pull failed (exit {process.returncode})")

    def remove_exact_tag(self, tag: str) -> None:
        validate_exact_image_coordinates(tag, "linux/amd64")
        process = self._run(["image", "rm", tag], timeout=120)
        if process.returncode != 0:
            raise RuntimeError(
                f"docker exact-tag removal failed (exit {process.returncode})"
            )

    def _run(
        self,
        arguments: list[str],
        *,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        return self._runner(
            [self._executable, *arguments],
            text=True,
            capture_output=True,
            timeout=timeout,
        )


class ColimaDockerDataFreeSpaceProbe:
    """读取 Colima VM 内 Docker data filesystem 的可用字节数。"""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        executable_resolver: Callable[[str], str | None] = shutil.which,
    ) -> None:
        executable = executable_resolver("colima")
        if not executable:
            raise RuntimeError("colima executable is unavailable")
        self._executable = executable
        self._runner = runner

    def __call__(self) -> int:
        argv = [
            self._executable,
            "ssh",
            "--",
            "df",
            "-Pk",
            "/var/lib/docker",
        ]
        process = self._runner(
            argv,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if process.returncode != 0:
            raise RuntimeError(
                f"Colima Docker data free-space probe failed (exit {process.returncode})"
            )
        return parse_posix_df_available_bytes(process.stdout)


def parse_posix_df_available_bytes(output: str) -> int:
    """解析冻结的 ``df -Pk`` 最后一行，并把 1K block 转成字节。"""

    lines = [line.split() for line in output.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines[-1]) < 6:
        raise RuntimeError("Docker data free-space probe returned invalid df output")
    try:
        available_kib = int(lines[-1][3])
    except ValueError as exc:
        raise RuntimeError(
            "Docker data free-space probe returned a non-numeric value"
        ) from exc
    if available_kib < 0 or lines[-1][-1] != "/var/lib/docker":
        raise RuntimeError("Docker data free-space probe returned an unexpected mount")
    return available_kib * 1024

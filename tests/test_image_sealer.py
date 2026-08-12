from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from agent_forge.bench.adapters.campaign_files import FileCampaignJournal
from agent_forge.bench.adapters.docker_images import (
    ColimaDockerDataFreeSpaceProbe,
    DockerExactImageRuntime,
    parse_posix_df_available_bytes,
)
from agent_forge.bench.application.campaign_lifecycle import ExactImageIdentity
from agent_forge.bench.application.image_sealer import (
    ImageSealRequest,
    SequentialImageSealer,
)


PLATFORM = "linux/amd64"


def _identity(tag: str, suffix: str = "observed") -> ExactImageIdentity:
    digest = hashlib.sha256(f"{tag}:{suffix}".encode()).hexdigest()
    return {
        "tag": tag,
        "repo_digest": f"{tag.rsplit(':', 1)[0]}@sha256:{digest}",
        "image_id": f"sha256:{digest}",
        "platform": PLATFORM,
    }


class FakeRuntime:
    def __init__(self) -> None:
        self.current: dict[str, ExactImageIdentity] = {}
        self.pull_results: dict[str, ExactImageIdentity] = {}
        self.events: list[tuple[str, str]] = []
        self.free = 10_000
        self.on_pull: Callable[[str], None] | None = None
        self.crash_pull: set[str] = set()
        self.crash_remove: set[str] = set()
        self.inspect_count: dict[str, int] = {}
        self.retag_at_inspect: dict[str, tuple[int, ExactImageIdentity]] = {}

    def inspect(self, tag: str) -> ExactImageIdentity | None:
        count = self.inspect_count.get(tag, 0) + 1
        self.inspect_count[tag] = count
        retag = self.retag_at_inspect.get(tag)
        if retag is not None and retag[0] == count:
            self.current[tag] = dict(retag[1])
        identity = self.current.get(tag)
        return dict(identity) if identity is not None else None

    def pull(self, tag: str, platform: str) -> None:
        assert platform == PLATFORM
        self.events.append(("pull", tag))
        if self.on_pull is not None:
            self.on_pull(tag)
        self.current[tag] = dict(self.pull_results.get(tag, _identity(tag)))
        if tag in self.crash_pull:
            raise RuntimeError("simulated pull crash")

    def remove_exact_tag(self, tag: str) -> None:
        self.events.append(("remove", tag))
        self.current.pop(tag, None)
        if tag in self.crash_remove:
            raise RuntimeError("simulated removal crash")


def _sealer(
    tmp_path: Path,
    runtime: FakeRuntime,
    state_path: str = ".agent_forge/images/seal.json",
) -> SequentialImageSealer:
    return SequentialImageSealer(
        FileCampaignJournal(tmp_path),
        state_path,
        runtime,
        minimum_free_bytes=100,
        free_space_probe=lambda: runtime.free,
    )


def test_preexisting_image_is_sealed_and_retained(tmp_path: Path) -> None:
    tag = "swebench/demo:latest"
    runtime = FakeRuntime()
    runtime.current[tag] = _identity(tag, "preexisting")

    assert _sealer(tmp_path, runtime).seal((ImageSealRequest(tag, PLATFORM),)) == (
        _identity(tag, "preexisting"),
    )
    assert runtime.events == []
    state = json.loads((tmp_path / ".agent_forge/images/seal.json").read_text())
    assert state["entries"][0]["cleanup"] == "retained_preexisting"
    assert state["status"] == "complete"


def test_pull_intent_precedes_pull_and_owned_exact_tag_is_removed(
    tmp_path: Path,
) -> None:
    tag = "swebench/demo:latest"
    runtime = FakeRuntime()

    def inspect_intent(_: str) -> None:
        state = FileCampaignJournal(tmp_path).read(".agent_forge/images/seal.json")
        assert state is not None
        assert state["entries"][0]["phase"] == "pull_intent"

    runtime.on_pull = inspect_intent
    identities = _sealer(tmp_path, runtime).seal((ImageSealRequest(tag, PLATFORM),))

    assert identities == (_identity(tag),)
    assert runtime.events == [("pull", tag), ("remove", tag)]
    assert runtime.current == {}
    assert all("prune" not in operation for operation, _ in runtime.events)


def test_retagged_owned_image_is_retained_and_seal_fails(tmp_path: Path) -> None:
    tag = "swebench/demo:latest"
    runtime = FakeRuntime()
    runtime.retag_at_inspect[tag] = (3, _identity(tag, "retagged"))

    with pytest.raises(RuntimeError, match="identity changed"):
        _sealer(tmp_path, runtime).seal((ImageSealRequest(tag, PLATFORM),))
    assert runtime.events == [("pull", tag)]
    assert runtime.current[tag] == _identity(tag, "retagged")


def test_crash_after_pull_retains_uncertain_image_on_recovery(tmp_path: Path) -> None:
    tag = "swebench/demo:latest"
    runtime = FakeRuntime()
    runtime.crash_pull.add(tag)
    request = (ImageSealRequest(tag, PLATFORM),)

    with pytest.raises(RuntimeError, match="pull crash"):
        _sealer(tmp_path, runtime).seal(request)
    runtime.crash_pull.clear()
    assert _sealer(tmp_path, runtime).seal(request) == (_identity(tag),)
    assert runtime.events == [("pull", tag)]
    assert runtime.current[tag] == _identity(tag)


def test_crash_after_remove_never_causes_second_delete(tmp_path: Path) -> None:
    tag = "swebench/demo:latest"
    runtime = FakeRuntime()
    runtime.crash_remove.add(tag)
    request = (ImageSealRequest(tag, PLATFORM),)

    with pytest.raises(RuntimeError, match="removal crash"):
        _sealer(tmp_path, runtime).seal(request)
    runtime.crash_remove.clear()
    assert _sealer(tmp_path, runtime).seal(request) == (_identity(tag),)
    assert runtime.events == [("pull", tag), ("remove", tag)]


def test_free_space_gate_blocks_before_pull_then_can_resume(tmp_path: Path) -> None:
    tag = "swebench/demo:latest"
    runtime = FakeRuntime()
    runtime.free = 99
    request = (ImageSealRequest(tag, PLATFORM),)

    with pytest.raises(RuntimeError, match="insufficient free space"):
        _sealer(tmp_path, runtime).seal(request)
    assert runtime.events == []
    runtime.free = 100
    assert _sealer(tmp_path, runtime).seal(request) == (_identity(tag),)


def test_state_path_cannot_escape_project(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot contain"):
        _sealer(tmp_path, FakeRuntime(), "../image-seal.json")


def test_zero_of_ten_local_images_are_sealed_strictly_sequentially(
    tmp_path: Path,
) -> None:
    tags = tuple(f"swebench/demo-{index}:latest" for index in range(10))
    runtime = FakeRuntime()
    requests = tuple(ImageSealRequest(tag, PLATFORM) for tag in tags)

    assert _sealer(tmp_path, runtime).seal(requests) == tuple(
        _identity(tag) for tag in tags
    )
    assert runtime.events == [
        event for tag in tags for event in (("pull", tag), ("remove", tag))
    ]


def test_docker_runtime_uses_exact_argv_and_parses_identity(tmp_path: Path) -> None:
    tag = "swebench/demo:latest"
    expected = _identity(tag)
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            payload = [
                {
                    "Id": expected["image_id"],
                    "RepoDigests": [expected["repo_digest"]],
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            ]
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    runtime = DockerExactImageRuntime(
        runner=runner,
        executable_resolver=lambda _: "/usr/local/bin/docker",
    )
    assert runtime.inspect(tag) == expected
    runtime.pull(tag, PLATFORM)
    runtime.remove_exact_tag(tag)
    assert calls == [
        ["/usr/local/bin/docker", "image", "inspect", tag],
        ["/usr/local/bin/docker", "pull", "--platform", PLATFORM, tag],
        ["/usr/local/bin/docker", "image", "rm", tag],
    ]


def test_docker_runtime_distinguishes_missing_image_and_daemon_failure() -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 1, "", "No such image: demo"),
            subprocess.CompletedProcess([], 1, "", "daemon unavailable"),
        ]
    )
    runtime = DockerExactImageRuntime(
        runner=lambda *_args, **_kwargs: next(results),
        executable_resolver=lambda _: "/usr/local/bin/docker",
    )

    assert runtime.inspect("swebench/demo:latest") is None
    with pytest.raises(RuntimeError, match="inspect failed"):
        runtime.inspect("swebench/demo:latest")


def test_colima_probe_reads_docker_vm_filesystem_not_host_disk() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        output = (
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/vda1 103000000 60000000 43000000 59% /var/lib/docker\n"
        )
        return subprocess.CompletedProcess(argv, 0, output, "")

    probe = ColimaDockerDataFreeSpaceProbe(
        runner=runner,
        executable_resolver=lambda _: "/opt/homebrew/bin/colima",
    )

    assert probe() == 43_000_000 * 1024
    assert calls == [
        [
            "/opt/homebrew/bin/colima",
            "ssh",
            "--",
            "df",
            "-Pk",
            "/var/lib/docker",
        ]
    ]


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n",
        "x 1 2 nope 4 /var/lib/docker\n",
        "x 1 2 3 4 /unexpected\n",
    ],
)
def test_colima_probe_rejects_ambiguous_df_output(output: str) -> None:
    with pytest.raises(RuntimeError, match="free-space probe"):
        parse_posix_df_available_bytes(output)

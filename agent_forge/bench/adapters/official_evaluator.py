"""SWE-bench official harness 的进程与 Docker 生命周期 Adapter。

系统角色：把已经冻结的 predictions 交给 official evaluator，再把显式报告结果写回
``BenchCaseResult``；本文件不根据 candidate diff 或本地测试自行判断 resolved。
输入：``BenchRunSummary`` 与同一轮 ``SwebenchRunRequest``。
输出：official command/output、逐 Case official status、报告路径和清理告警。

折叠导航：1 official 生命周期；2 镜像 ownership；3 结果不可用收口；4 命令构造。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from typing import Any

from agent_forge.bench.adapters.official_results import (
    apply_official_results,
    parse_official_results,
)
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchRunSummary
from agent_forge.bench.ports import OfficialEvaluatorPort


# SWE-bench 生成报告时会枚举本机全部 Docker 镜像。如果另一路评测恰好在
# 枚举期间删除自有镜像，docker-py 会读到过期条目并抛出 ImageNotFound，
# 即使该 Case 的测试本身已经结束。模型生成仍可并发，但 official 镜像的
# “准备、评测、报告、释放”生命周期必须在当前进程内串行执行。
_OFFICIAL_IMAGE_LIFECYCLE_LOCK = threading.Lock()


class SwebenchOfficialEvaluator(OfficialEvaluatorPort):
    """串行保护 official 镜像生命周期，并消费 official harness 的显式结果。"""

    # region 1. Official 生命周期：preflight → harness → report parse → owned cleanup
    def evaluate(
        self,
        summary: BenchRunSummary,
        request: SwebenchRunRequest,
    ) -> None:
        """在进程级镜像锁内完成一次 official evaluation 生命周期。"""

        with _OFFICIAL_IMAGE_LIFECYCLE_LOCK:
            self._evaluate_serial(summary, request)

    def _evaluate_serial(
        self,
        summary: BenchRunSummary,
        request: SwebenchRunRequest,
    ) -> None:
        """运行 official harness，并让 parser 而非进程退出码决定逐 Case 结果。

        伪代码：检查 harness → 准备本轮镜像 lease → 执行官方命令
        → 解析 aggregate/per-case reports → 写回 Case → 释放本轮 owned 镜像。
        """

        # Harness 不可用时所有 Case 显式标记 unavailable，不能降级成本地正确性。
        if importlib.util.find_spec("swebench") is None:
            self._mark_unavailable(summary)
            return

        leases: list[dict[str, Any]] = []
        cleanup_warnings: list[str] = []
        try:
            leases = self._prepare_official_images(summary, request)
        except RuntimeError as exc:
            self._mark_image_unavailable(summary, str(exc))
            return

        # 命令退出码是运行级诊断；逐 Case resolved/unresolved 只读 official report。
        command = self._command(summary, request)
        summary.official_eval_command = command
        try:
            process = subprocess.run(
                command,
                text=True,
                capture_output=True,
                cwd=str(summary.output_dir),
            )
            summary.official_eval_exit_code = process.returncode
            output = f"STDOUT:\n{process.stdout}\nSTDERR:\n{process.stderr}"
            summary.official_eval_output = output[-20000:]
            instance_ids = [result.instance_id for result in summary.case_results]
            parsed = parse_official_results(
                summary.output_dir,
                summary.run_id,
                instance_ids,
            )
            summary.official_eval_report_path = str(parsed.report_path or "")
            apply_official_results(
                summary.case_results,
                parsed,
                process_exit_code=process.returncode,
            )
            summary.official_eval_warnings = list(parsed.warnings)
        finally:
            # 只释放本次新拉取的 tag；预先存在的宿主镜像不属于当前 Evaluator。
            cleanup_warnings = self._release_owned_images(leases)
            summary.official_eval_warnings.extend(cleanup_warnings)
            if cleanup_warnings:
                summary.official_eval_exit_code = 125
                summary.official_eval_output = (
                    summary.official_eval_output
                    + "\nOFFICIAL_IMAGE_CLEANUP:\n"
                    + "\n".join(cleanup_warnings)
                )[-20000:]
                for result in summary.case_results:
                    result.official_evaluation_status = "official_eval_error"
                    result.official_evaluation_detail = summary.official_eval_output
                    result.evaluation_status = "official_eval_error"
    # endregion 1. Official 生命周期结束

    # region 2. 镜像 ownership：只登记并释放当前 evaluation 新拉取的 tag
    @classmethod
    def _prepare_official_images(
        cls,
        summary: BenchRunSummary,
        request: SwebenchRunRequest,
    ) -> list[dict[str, Any]]:
        """按显式平台拉取 official tag；只把本次新增的 tag 视为 owned。"""

        namespace = "" if request.namespace_empty else request.official_namespace
        if not request.official_platform or not namespace:
            return []
        leases: list[dict[str, Any]] = []
        try:
            for result in summary.case_results:
                tag = cls._official_image_tag(
                    namespace,
                    request.official_platform,
                    result.instance_id,
                )
                identity = cls._inspect_image(tag)
                owned = False
                if identity is None:
                    pull = subprocess.run(
                        [
                            "docker",
                            "pull",
                            "--platform",
                            request.official_platform,
                            tag,
                        ],
                        text=True,
                        capture_output=True,
                    )
                    if pull.returncode != 0:
                        detail = (pull.stderr or pull.stdout).strip()
                        raise RuntimeError(
                            f"official image pull failed for {tag}: {detail}"
                        )
                    owned = True
                    # Pull 成功后立即登记 ownership；后续 inspect/平台核验失败也能精确清理。
                    leases.append({"tag": tag, "owned": True})
                    identity = cls._inspect_image(tag)
                if identity is None:
                    raise RuntimeError(
                        f"official image inspect failed after pull: {tag}"
                    )
                observed_platform = (
                    f"{identity.get('Os', '')}/{identity.get('Architecture', '')}"
                )
                if observed_platform != request.official_platform:
                    raise RuntimeError(
                        "official image platform drift: "
                        f"tag={tag} expected={request.official_platform} "
                        f"observed={observed_platform}"
                    )
                lease = {
                    "tag": tag,
                    "platform": observed_platform,
                    "image_id": str(identity.get("Id") or ""),
                    "repo_digests": sorted(
                        str(item) for item in (identity.get("RepoDigests") or [])
                    ),
                    "owned": owned,
                }
                if owned:
                    leases[-1] = lease
                else:
                    leases.append(lease)
                summary.official_eval_images.append(dict(lease))
        except Exception:
            cls._release_owned_images(leases)
            raise
        return leases

    @staticmethod
    def _official_image_tag(
        namespace: str,
        platform: str,
        instance_id: str,
    ) -> str:
        architecture = {
            "linux/amd64": "x86_64",
            "linux/arm64": "arm64",
        }[platform]
        key = f"sweb.eval.{architecture}.{instance_id.lower()}:latest"
        return f"{namespace}/{key}".replace("__", "_1776_")

    @staticmethod
    def _inspect_image(tag: str) -> dict[str, Any] | None:
        process = subprocess.run(
            ["docker", "image", "inspect", tag],
            text=True,
            capture_output=True,
        )
        if process.returncode != 0:
            return None
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid docker inspect JSON for {tag}") from exc
        if not isinstance(payload, list) or len(payload) != 1:
            raise RuntimeError(f"unexpected docker inspect payload for {tag}")
        identity = payload[0]
        if not isinstance(identity, dict):
            raise RuntimeError(f"unexpected docker inspect identity for {tag}")
        return identity

    @staticmethod
    def _release_owned_images(leases: list[dict[str, Any]]) -> list[str]:
        """逆序释放 owned lease；清理失败作为 evaluator error 证据返回。"""

        warnings: list[str] = []
        for lease in reversed(leases):
            if not lease.get("owned"):
                continue
            tag = str(lease.get("tag") or "")
            process = subprocess.run(
                ["docker", "image", "rm", tag],
                text=True,
                capture_output=True,
            )
            if process.returncode != 0:
                warnings.append(
                    "owned official image cleanup failed: "
                    f"{tag}: {(process.stderr or process.stdout).strip()}"
                )
        return warnings
    # endregion 2. 镜像 ownership 结束

    # region 3. 不可用收口：区分 package 缺失与 image preflight 失败
    @staticmethod
    def _mark_unavailable(summary: BenchRunSummary) -> None:
        summary.official_eval_exit_code = 127
        summary.official_eval_output = (
            "swebench package is not installed. Install SWE-bench and rerun "
            "with --evaluate."
        )
        for result in summary.case_results:
            result.official_evaluation_status = "official_eval_unavailable"
            result.official_evaluation_detail = summary.official_eval_output
            result.evaluation_status = "official_eval_unavailable"

    @staticmethod
    def _mark_image_unavailable(summary: BenchRunSummary, detail: str) -> None:
        summary.official_eval_exit_code = 125
        summary.official_eval_output = detail[-20000:]
        summary.official_eval_warnings = ["official image preflight failed"]
        for result in summary.case_results:
            result.official_evaluation_status = "official_eval_error"
            result.official_evaluation_detail = summary.official_eval_output
            result.evaluation_status = "official_eval_error"
    # endregion 3. 不可用收口结束

    # region 4. 命令构造：冻结 dataset、prediction、worker、cache 与 namespace 参数
    @staticmethod
    def _command(
        summary: BenchRunSummary,
        request: SwebenchRunRequest,
    ) -> list[str]:
        """只构造 official harness argv，不执行命令也不解释结果。"""

        command = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            summary.dataset_name,
            "--split",
            summary.split,
            "--predictions_path",
            str(summary.predictions_path),
            "--max_workers",
            str(request.max_workers),
            "--cache_level",
            request.official_cache_level,
            "--run_id",
            summary.run_id,
        ]
        instance_ids = [result.instance_id for result in summary.case_results]
        if instance_ids:
            command.extend(["--instance_ids", *instance_ids])
        # 发布镜像避免本机构建时重新访问已经漂移的上游依赖；空 namespace 仅保留为
        # 显式兼容选项，不能再根据宿主架构静默改变 official evaluator 身份。
        official_namespace = (
            "" if request.namespace_empty else request.official_namespace
        )
        command.extend(["--namespace", official_namespace])
        return command
    # endregion 4. 命令构造结束

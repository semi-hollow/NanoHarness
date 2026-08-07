"""大样本 benchmark cohort 的可复现实验合同。

阅读入口是 ``load_benchmark_cohort``：它读取一个已提交的 JSON manifest，校验
case 集合、分片互斥性和摘要，再由 ``select_shard`` 返回本次 campaign 的确切分母。
本模块不加载数据集，也不选择“容易成功”的题。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COHORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, kw_only=True)
class CohortSelection:
    """一次 campaign 实际消费的固定 cohort 分片。"""

    cohort_id: str
    shard: str
    dataset_name: str
    dataset_revision: str
    split: str
    universe_size: int
    selection_method: str
    selection_seed: str
    universe_sha256: str
    cohort_sha256: str
    case_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """把样本来源写入 campaign identity，避免结果脱离分母。"""

        return {
            "cohort_id": self.cohort_id,
            "shard": self.shard,
            "dataset_name": self.dataset_name,
            "dataset_revision": self.dataset_revision,
            "split": self.split,
            "universe_size": self.universe_size,
            "selection_method": self.selection_method,
            "selection_seed": self.selection_seed,
            "universe_sha256": self.universe_sha256,
            "cohort_sha256": self.cohort_sha256,
            "case_count": len(self.case_ids),
            "case_ids": list(self.case_ids),
        }


@dataclass(frozen=True, kw_only=True)
class BenchmarkCohortManifest:
    """预注册母集及其互斥执行分片。"""

    cohort_id: str
    dataset_name: str
    dataset_revision: str
    split: str
    universe_size: int
    selection_method: str
    selection_seed: str
    universe_sha256: str
    cohort_sha256: str
    case_ids: tuple[str, ...]
    shard_order: tuple[str, ...]
    shards: dict[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BenchmarkCohortManifest":
        """解析并校验 manifest；失败时在任何付费模型调用前终止。"""

        if int(payload.get("schema_version") or 0) != COHORT_SCHEMA_VERSION:
            raise ValueError("unsupported benchmark cohort schema")
        selection = payload.get("selection")
        if not isinstance(selection, dict):
            raise ValueError("cohort selection must be an object")
        raw_shards = payload.get("shards")
        if not isinstance(raw_shards, dict):
            raise ValueError("cohort shards must be an object")

        manifest = cls(
            cohort_id=str(payload.get("cohort_id") or ""),
            dataset_name=str(payload.get("dataset_name") or ""),
            dataset_revision=str(payload.get("dataset_revision") or ""),
            split=str(payload.get("split") or ""),
            universe_size=int(payload.get("universe_size") or 0),
            selection_method=str(selection.get("method") or ""),
            selection_seed=str(selection.get("seed") or ""),
            universe_sha256=str(selection.get("universe_sha256") or ""),
            cohort_sha256=str(selection.get("cohort_sha256") or ""),
            case_ids=tuple(str(item) for item in payload.get("case_ids") or ()),
            shard_order=tuple(str(item) for item in payload.get("shard_order") or ()),
            shards={
                str(name): tuple(str(item) for item in values)
                for name, values in raw_shards.items()
                if isinstance(values, list)
            },
        )
        manifest._validate()
        return manifest

    def select_shard(self, shard: str) -> CohortSelection:
        """返回指定分片；分片名或内容错误时不允许回退到隐式样本。"""

        try:
            selected_case_ids = self.shards[shard]
        except KeyError as exc:
            choices = ", ".join(self.shard_order)
            raise ValueError(f"unknown cohort shard {shard!r}; choose one of: {choices}") from exc
        return CohortSelection(
            cohort_id=self.cohort_id,
            shard=shard,
            dataset_name=self.dataset_name,
            dataset_revision=self.dataset_revision,
            split=self.split,
            universe_size=self.universe_size,
            selection_method=self.selection_method,
            selection_seed=self.selection_seed,
            universe_sha256=self.universe_sha256,
            cohort_sha256=self.cohort_sha256,
            case_ids=selected_case_ids,
        )

    def _validate(self) -> None:
        required_text = {
            "cohort_id": self.cohort_id,
            "dataset_name": self.dataset_name,
            "dataset_revision": self.dataset_revision,
            "split": self.split,
            "selection.method": self.selection_method,
            "selection.seed": self.selection_seed,
            "selection.universe_sha256": self.universe_sha256,
            "selection.cohort_sha256": self.cohort_sha256,
        }
        missing = [name for name, value in required_text.items() if not value]
        if missing:
            raise ValueError(f"cohort manifest is missing: {', '.join(missing)}")
        if not self.case_ids or len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("cohort case_ids must be non-empty and unique")
        if self.universe_size < len(self.case_ids):
            raise ValueError("cohort cannot be larger than its source universe")
        if not self.shard_order or set(self.shard_order) != set(self.shards):
            raise ValueError("shard_order must name every shard exactly once")

        flattened: list[str] = []
        for shard_name in self.shard_order:
            shard_case_ids = self.shards[shard_name]
            if not shard_case_ids or len(set(shard_case_ids)) != len(shard_case_ids):
                raise ValueError(
                    f"cohort shard {shard_name!r} must be non-empty and unique"
                )
            flattened.extend(shard_case_ids)
        if tuple(flattened) != self.case_ids:
            raise ValueError("ordered shards must be disjoint and exactly cover case_ids")
        if _case_ids_sha256(self.case_ids) != self.cohort_sha256:
            raise ValueError("cohort_sha256 does not match ordered case_ids")


def load_benchmark_cohort(path: str | Path) -> BenchmarkCohortManifest:
    """从版本化 JSON 文件读取 cohort；这是 CLI 和其他入口的公共边界。"""

    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark cohort manifest must contain an object")
    return BenchmarkCohortManifest.from_dict(payload)


def _case_ids_sha256(case_ids: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(case_ids).encode("utf-8")).hexdigest()

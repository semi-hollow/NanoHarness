"""SWE-bench CaseSource Adapter。

系统角色：从显式 JSON/JSONL 或固定 HuggingFace dataset revision 加载原始 Case，并按
instance id/limit 生成 typed ``BenchCase``；显式请求缺失时 fail closed。
输入：``SwebenchRunRequest``；输出：非空 Case list。

折叠导航：1 filter/normalize；2 local file；3 HuggingFace source。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase
from agent_forge.bench.ports import CaseSourcePort


class SwebenchCaseSource(CaseSourcePort):
    # region 1. Filter / normalize：显式 instance id 必须全部存在
    def load(self, request: SwebenchRunRequest) -> list[BenchCase]:
        wanted = set(request.instance_ids)
        raw_cases = (
            self._load_cases_file(request.cases_file)
            if request.cases_file
            else self._load_huggingface_cases(
                request.dataset_name,
                request.split,
                request.dataset_revision,
            )
        )
        if wanted:
            available_instance_ids = {
                str(raw.get("instance_id") or raw.get("id") or "")
                for raw in raw_cases
            }
            missing_instance_ids = sorted(wanted - available_instance_ids)
            if missing_instance_ids:
                raise RuntimeError(
                    "Explicitly requested SWE-bench instance_ids were not found "
                    f"in the dataset: {', '.join(missing_instance_ids)}"
                )
        cases: list[BenchCase] = []
        # 保持数据源顺序，逐条转 Domain；limit 只在过滤后生效。
        for raw in raw_cases:
            case = BenchCase.from_mapping(raw)
            if wanted and case.instance_id not in wanted:
                continue
            cases.append(case)
            if request.limit and len(cases) >= request.limit:
                break
        if not cases:
            raise RuntimeError("No SWE-bench cases matched the requested filters.")
        return cases
    # endregion 1. Filter / normalize 结束

# region 2. 本地 JSON / JSONL 数据源
    @staticmethod
    def _load_cases_file(cases_file: str | None) -> list[dict[str, Any]]:
        path = Path(cases_file or "")
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("JSON cases file must contain a list of objects.")
            return [dict(item) for item in data if isinstance(item, dict)]
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("JSONL case rows must contain objects.")
            rows.append(item)
        return rows
    # endregion 2. Local source 结束

    # region 3. HuggingFace source：dependency 与 revision 都显式
    @staticmethod
    def _load_huggingface_cases(
        dataset_name: str,
        split: str,
        dataset_revision: str = "",
    ) -> list[dict[str, Any]]:
        if importlib.util.find_spec("datasets") is None:
            raise RuntimeError(
                "Install benchmark extras first: python -m pip install -e '.[bench]'. "
                "Alternatively pass --cases-file with SWE-bench-shaped JSONL rows."
            )
        from datasets import load_dataset

        load_options: dict[str, str] = {"split": split}
        if dataset_revision:
            load_options["revision"] = dataset_revision
        dataset = load_dataset(dataset_name, **load_options)
        return [dict(row) for row in dataset]
    # endregion 3. HuggingFace source 结束

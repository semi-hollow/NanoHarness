from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.bench.domain.models import BenchCase


class SwebenchCaseSource:
    def load(self, request: SwebenchRunRequest) -> list[BenchCase]:
        wanted = set(request.instance_ids)
        raw_cases = (
            self._load_cases_file(request.cases_file)
            if request.cases_file
            else self._load_huggingface_cases(request.dataset_name, request.split)
        )
        cases: list[BenchCase] = []
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

    @staticmethod
    def _load_huggingface_cases(
        dataset_name: str,
        split: str,
    ) -> list[dict[str, Any]]:
        if importlib.util.find_spec("datasets") is None:
            raise RuntimeError(
                "Install benchmark extras first: python -m pip install -e '.[bench]'. "
                "Alternatively pass --cases-file with SWE-bench-shaped JSONL rows."
            )
        from datasets import load_dataset

        dataset = load_dataset(dataset_name, split=split)
        return [dict(row) for row in dataset]

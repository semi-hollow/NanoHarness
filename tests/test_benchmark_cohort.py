import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.bench.adapters.dataset import SwebenchCaseSource
from agent_forge.bench.domain.campaign import BenchmarkCampaignRequest
from agent_forge.bench.domain.cohort import load_benchmark_cohort
from agent_forge.bench.domain.config import SwebenchRunRequest
from agent_forge.cli.parser import build_parser


PROJECT_ROOT = Path(__file__).parents[1]
COHORT_PATH = PROJECT_ROOT / "benchmarks/showcase/canonical-50-v1.json"


class BenchmarkCohortTest(unittest.TestCase):
    def test_benchmark_cli_accepts_frozen_dataset_revision(self):
        parser = build_parser()

        swebench_args = parser.parse_args(
            ["bench", "swebench", "--dataset-revision", "immutable-sha"]
        )
        campaign_args = parser.parse_args(
            ["bench", "campaign", "--dataset-revision", "immutable-sha"]
        )

        self.assertEqual(swebench_args.dataset_revision, "immutable-sha")
        self.assertEqual(campaign_args.dataset_revision, "immutable-sha")

    def test_dataset_loader_passes_frozen_revision_to_huggingface(self):
        captured = {}
        fake_datasets = types.ModuleType("datasets")

        def fake_load_dataset(name, **options):
            captured["name"] = name
            captured["options"] = options
            return [{"instance_id": "case-1"}]

        fake_datasets.load_dataset = fake_load_dataset
        with (
            patch("importlib.util.find_spec", return_value=object()),
            patch.dict(
                sys.modules,
                {"datasets": fake_datasets},
            ),
        ):
            rows = SwebenchCaseSource._load_huggingface_cases(
                "owner/dataset",
                "test",
                "immutable-sha",
            )

        self.assertEqual(rows, [{"instance_id": "case-1"}])
        self.assertEqual(captured["name"], "owner/dataset")
        self.assertEqual(
            captured["options"],
            {"split": "test", "revision": "immutable-sha"},
        )

    def test_dataset_loader_rejects_missing_explicit_instance_ids(self):
        available_case = {
            "instance_id": "case-available",
            "repo": "owner/repo",
            "base_commit": "abc123",
            "problem_statement": "Fix the bug.",
        }
        request = SwebenchRunRequest(
            dataset_name="owner/dataset",
            split="test",
            limit=2,
            instance_ids=("case-available", "case-missing"),
        )

        with patch.object(
            SwebenchCaseSource,
            "_load_huggingface_cases",
            return_value=[available_case],
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Explicitly requested SWE-bench instance_ids were not found.*case-missing",
            ):
                SwebenchCaseSource().load(request)

    def test_checked_in_canonical_cohort_has_five_disjoint_ten_case_waves(self):
        cohort = load_benchmark_cohort(COHORT_PATH)

        waves = [cohort.select_shard(name) for name in cohort.shard_order]

        self.assertEqual(len(cohort.case_ids), 50)
        self.assertEqual([len(wave.case_ids) for wave in waves], [10] * 5)
        flattened = [case_id for wave in waves for case_id in wave.case_ids]
        self.assertEqual(flattened, list(cohort.case_ids))
        self.assertEqual(len(set(flattened)), 50)

    def test_campaign_identity_binds_selected_cohort(self):
        cohort = load_benchmark_cohort(COHORT_PATH).select_shard("wave-1")
        request = BenchmarkCampaignRequest(
            benchmark=SwebenchRunRequest(
                dataset_name=cohort.dataset_name,
                dataset_revision=cohort.dataset_revision,
                split=cohort.split,
            ),
            case_ids=cohort.case_ids,
            campaign_id="cohort-test",
            regression_set=f"{cohort.cohort_id}:{cohort.shard}",
            repetitions=1,
            cohort=cohort,
        )

        identity = request.identity()

        self.assertEqual(identity["cohort"]["cohort_id"], cohort.cohort_id)
        self.assertEqual(identity["cohort"]["shard"], "wave-1")
        self.assertEqual(identity["cohort"]["case_count"], 10)
        self.assertEqual(identity["case_ids"], list(cohort.case_ids))

    def test_manifest_rejects_overlapping_or_reordered_shards(self):
        payload = json.loads(COHORT_PATH.read_text(encoding="utf-8"))
        payload["shards"]["wave-2"][0] = payload["shards"]["wave-1"][0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                load_benchmark_cohort(path)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agent_forge.workbench.adapters.evidence_files import FileEvidenceCatalog
from agent_forge.workbench.application.review_projection import (
    build_lab1_review,
    build_lab2_review,
    build_mini50_review,
)
from agent_forge.workbench.presentation.http import INDEX_HTML, _render_workspace_view


PROJECT_ROOT = Path(__file__).parents[1]
LAB1_RUN = (
    PROJECT_ROOT
    / ".agent_forge/runs/showcases"
    / "lab1-governed-change-control__2026-08-17_00-59-30__4d0d7db"
    / "showcase.json"
)
LAB2_RUN = (
    PROJECT_ROOT
    / ".agent_forge/runs/showcases"
    / "lab2-checkout-policy-agents__2026-08-17_00-03-39__4028377"
    / "fanout/fanout_summary.json"
)
LOCAL_CONTROL_EVIDENCE = LAB1_RUN.is_file() and LAB2_RUN.is_file()
MINI50_ROOT = (
    PROJECT_ROOT
    / ".agent_forge/runs/benchmarks/swebench-verified-mini-50-infrastructure-completion"
    / "mini50-v1.2-infra-completion-deepseek-v4-flash-3ec537113a"
)
LOCAL_MINI50_EVIDENCE = MINI50_ROOT.is_dir()


class ReviewReadyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = FileEvidenceCatalog(PROJECT_ROOT).evidence_sources()

    @unittest.skipUnless(
        LOCAL_CONTROL_EVIDENCE,
        "canonical Lab raw evidence is intentionally local-only",
    )
    def test_review_manifest_pins_complete_lab1_and_lab2(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "benchmarks/showcase/evidence-review-v1.json").read_text(
                encoding="utf-8"
            )
        )
        for key in ("governed", "orchestration"):
            source = next(item for item in self.sources if item.key == key)
            configured = manifest["sources"][key]
            self.assertEqual(source.run_key, configured["canonical_run"])
            self.assertIsNotNone(source.primary_path)
            assert source.primary_path is not None
            self.assertEqual(
                hashlib.sha256(source.primary_path.read_bytes()).hexdigest(),
                configured["canonical_sha256"],
            )

    @unittest.skipUnless(
        LOCAL_CONTROL_EVIDENCE,
        "canonical Lab raw evidence is intentionally local-only",
    )
    def test_lab1_projection_observes_full_state_chain_and_invariants(self) -> None:
        source = next(item for item in self.sources if item.key == "governed")
        review = build_lab1_review(PROJECT_ROOT, source)
        self.assertEqual(
            review.state_sequence,
            ("waiting_human", "waiting_approval", "completed"),
        )
        self.assertTrue(all(item.observed for item in review.invariants))
        self.assertEqual(
            {item.owner for item in review.authorities},
            {"HumanInput", "Approval", "Operation Ledger", "Checkpoint", "Trace"},
        )

    @unittest.skipUnless(
        LOCAL_CONTROL_EVIDENCE,
        "canonical Lab raw evidence is intentionally local-only",
    )
    def test_lab2_projection_matches_real_batches_and_finalizer(self) -> None:
        source = next(item for item in self.sources if item.key == "orchestration")
        review = build_lab2_review(PROJECT_ROOT, source)
        self.assertEqual(
            review.batches,
            (("pricing-policy", "shipping-policy"), ("edge-case-verifier",)),
        )
        self.assertEqual(review.conflicts, ())
        self.assertEqual(review.final_decision, "PASS")
        self.assertIsNotNone(review.finalizer_trace)

    @unittest.skipUnless(
        LOCAL_MINI50_EVIDENCE,
        "canonical Mini-50 trajectories are intentionally local-only",
    )
    def test_mini50_main_path_is_exactly_the_canonical_50(self) -> None:
        evaluation = next(item for item in self.sources if item.key == "evaluation")
        cases = [
            item
            for item in self.sources
            if item.category_key == "evaluation" and item.item_key != "overview"
        ]
        review = build_mini50_review(PROJECT_ROOT, evaluation, self.sources)
        self.assertEqual(len(cases), 50)
        self.assertEqual(len({item.item_key for item in cases}), 50)
        self.assertEqual(
            (review.resolved, review.unresolved, review.empty_patch),
            (28, 16, 6),
        )
        self.assertEqual(review.total_launches, 61)
        self.assertFalse(review.correctness_rerun)
        self.assertTrue(all(item.source_key for item in review.representatives))

    def test_review_overviews_separate_contract_from_observed_artifact(self) -> None:
        keys = (
            ("governed", "orchestration", "evaluation")
            if LOCAL_CONTROL_EVIDENCE
            else ("evaluation",)
        )
        for key in keys:
            rendered = _render_workspace_view(
                PROJECT_ROOT,
                source_key=key,
                view="overview",
                sources=self.sources,
            )
            self.assertIn("DESIGN CONTRACT", rendered)
            self.assertIn("OBSERVED ARTIFACT", rendered)
            self.assertIn("打开对应架构章节", rendered)
            self.assertIn("source-identity--compact", rendered)

    def test_url_state_supports_refresh_history_and_copy_link(self) -> None:
        required = (
            "pageParams.get('source')",
            "window.history[operation]",
            "window.addEventListener('popstate'",
            "copyReviewLink",
            "source=governed&amp;view=overview",
            "source=orchestration&amp;view=overview",
            "source=evaluation&amp;view=overview",
        )
        for value in required:
            self.assertIn(value, INDEX_HTML)

    def test_missing_canonical_lab_evidence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "benchmarks/showcase/evidence-review-v1.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "sources": {
                            "governed": {"canonical_run": "canonical-lab1"},
                            "orchestration": {"canonical_run": "canonical-lab2"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            stale_governed = root / ".agent_forge/runs/showcases/stale-lab1"
            stale_governed.mkdir(parents=True)
            (stale_governed / "showcase.json").write_text(
                json.dumps({"scenario": "governed", "status": "completed"}),
                encoding="utf-8",
            )
            stale_fanout = (
                root / ".agent_forge/runs/showcases/stale-lab2/fanout"
            )
            stale_fanout.mkdir(parents=True)
            (stale_fanout / "fanout_summary.json").write_text(
                json.dumps({"status": "passed", "results": []}),
                encoding="utf-8",
            )

            sources = FileEvidenceCatalog(root).evidence_sources()
            governed = next(item for item in sources if item.key == "governed")
            orchestration = next(
                item for item in sources if item.key == "orchestration"
            )

        self.assertFalse(governed.available)
        self.assertEqual(governed.run_key, "canonical-lab1")
        self.assertFalse(orchestration.available)
        self.assertEqual(orchestration.run_key, "canonical-lab2")

    @unittest.skipUnless(
        LOCAL_MINI50_EVIDENCE,
        "representative Case trajectory is intentionally local-only",
    )
    def test_representative_case_anatomy_includes_observed_base_revision(self) -> None:
        rendered = _render_workspace_view(
            PROJECT_ROOT,
            source_key="evaluation:case:2",
            view="overview",
            sources=self.sources,
        )
        self.assertIn("CASE ANATOMY", rendered)
        self.assertIn("django/django @ 69331bb851c34f05bc77e9fc24020fe6908b9cd5", rendered)


if __name__ == "__main__":
    unittest.main()

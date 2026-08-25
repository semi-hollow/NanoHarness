import json
import unittest
from pathlib import Path

from agent_forge.multi_agent.domain.fanout import (
    FANOUT_MECHANISM_EVIDENCE_SCHEMA_VERSION,
)
from apps.workbench.adapters.evidence_files import FileEvidenceCatalog
from apps.workbench.application.review_projection import (
    build_fanout_review,
    current_git_revision,
)
from apps.workbench.presentation.http import INDEX_HTML, _render_workspace_view


PROJECT_ROOT = Path(__file__).parents[1]


class ReviewReadyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = FileEvidenceCatalog(PROJECT_ROOT).evidence_sources()

    def test_current_multi_agent_evidence_is_versioned_and_schema_current(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "benchmarks/showcase/evidence-review-v1.json").read_text(
                encoding="utf-8"
            )
        )
        configured = manifest["sources"]["orchestration"]
        evidence_path = PROJECT_ROOT / configured["canonical_artifact"]
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(
            evidence["schema_version"],
            FANOUT_MECHANISM_EVIDENCE_SCHEMA_VERSION,
        )
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(len(evidence["plan_digest"]), 64)
        self.assertTrue(all(evidence["assertions"].values()))
        serialized = json.dumps(evidence, ensure_ascii=False)
        for removed in (
            "plan_generation" + "_id",
            '"bat' + 'ches"',
            "effective" + "_plan",
            "replan" + "_round",
        ):
            self.assertNotIn(removed, serialized)
        self.assertNotIn("/Users/", serialized)

    def test_current_multi_agent_review_renders_frozen_plan_live_and_frontier(self) -> None:
        source = next(item for item in self.sources if item.key == "orchestration")
        review = build_fanout_review(PROJECT_ROOT, source)
        self.assertEqual(review.status, "passed")
        self.assertEqual(
            [task.status for task in review.tasks],
            ["integrated", "integrated"],
        )
        self.assertEqual(review.coordination_events, ("READY", "FEEDBACK", "UPDATE"))
        self.assertEqual(review.final_decision, "PASS")
        rendered = _render_workspace_view(
            PROJECT_ROOT,
            source_key="orchestration",
            view="overview",
            sources=self.sources,
        )
        for value in (
            "CURRENT MULTI-AGENT RUNTIME",
            "Deeply Frozen FanoutPlan",
            "Observed Launch Waves",
            "Strict Integration Frontier",
            "READY → FEEDBACK → UPDATE",
            "Task / Attempt 治理结果",
        ):
            self.assertIn(value, rendered)
        self.assertNotIn("Historical Lab", rendered)
        self.assertNotIn("Observed " + "Bat" + "ches", rendered)

    def test_non_multi_agent_stable_surfaces_still_discover_and_render(self) -> None:
        category_keys = {source.category_key for source in self.sources}
        self.assertTrue({"governed", "orchestration", "evaluation"} <= category_keys)
        for key in ("governed", "evaluation"):
            rendered = _render_workspace_view(
                PROJECT_ROOT,
                source_key=key,
                view="overview",
                sources=self.sources,
            )
            self.assertIn("DESIGN CONTRACT", rendered)
            self.assertIn("OBSERVED ARTIFACT", rendered)

    def test_manifest_uses_stable_explicit_architecture_anchors(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "benchmarks/showcase/evidence-review-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {
                key: value["architecture_anchor"]
                for key, value in manifest["sources"].items()
            },
            {
                "governed": "#durability",
                "orchestration": "#system",
                "evaluation": "#evaluation",
            },
        )

    def test_review_links_bind_current_revision_and_url_state(self) -> None:
        revision = current_git_revision(PROJECT_ROOT)
        rendered = _render_workspace_view(
            PROJECT_ROOT,
            source_key="orchestration",
            view="overview",
            sources=self.sources,
        )
        self.assertIn(f"/blob/{revision}/docs/", rendered)
        for value in (
            "pageParams.get('source')",
            "window.history[operation]",
            "window.addEventListener('popstate'",
            "copyReviewLink",
            "source=governed&amp;view=overview",
            "source=orchestration&amp;view=overview",
            "source=evaluation&amp;view=overview",
        ):
            self.assertIn(value, INDEX_HTML)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from apps.workbench.adapters.evidence_files import FileEvidenceCatalog
from apps.workbench.application.review_projection import (
    build_lab1_review,
    build_lab2_review,
    build_mini50_review,
    current_git_revision,
)
from apps.workbench.presentation.http import INDEX_HTML, _render_workspace_view
from scripts.review_preflight import (
    EVIDENCE_TREE_ALGORITHM,
    _evidence_tree,
    _evidence_tree_integrity,
    _evidence_tree_patterns,
)
from scripts.migrate_context_semantic_naming_v3 import verify_applied_migration


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
            run_root = (
                source.primary_path.parent
                if key == "governed"
                else source.primary_path.parent.parent
            )
            count, digest = _evidence_tree(
                run_root,
                _evidence_tree_patterns(configured),
            )
            evidence_tree = configured["evidence_tree"]
            self.assertEqual(evidence_tree["algorithm"], EVIDENCE_TREE_ALGORITHM)
            self.assertEqual(count, evidence_tree["file_count"])
            self.assertEqual(digest, evidence_tree["sha256"])

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

        rendered = _render_workspace_view(
            PROJECT_ROOT,
            source_key="orchestration",
            view="overview",
            sources=self.sources,
        )
        for value in (
            "Fanout Algorithm Map · CURRENT",
            "Worker AgentLoop",
            "Candidate Diff",
            "Four Conflict Gates",
            "Scope Violation Gate",
            "Planner Agent",
            "Conflict Resolver Agent",
            "FUTURE / NOT IMPLEMENTED",
        ):
            self.assertIn(value, rendered)

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
        revision = current_git_revision(PROJECT_ROOT)
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
            self.assertIn(f"/blob/{revision}/docs/", rendered)

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

    def test_context_semantic_migration_manifest_is_auditable(self) -> None:
        migration_path = (
            PROJECT_ROOT / "migrations/context-semantic-naming-v3-manifest.json"
        )
        migration = json.loads(migration_path.read_text(encoding="utf-8"))
        entries = migration["files"]

        self.assertEqual(migration["checkpoint_contract"]["old_schema_version"], 2)
        self.assertEqual(migration["checkpoint_contract"]["new_schema_version"], 3)
        self.assertEqual(
            migration["checkpoint_contract"]["old_field"], "session_digest"
        )
        self.assertEqual(
            migration["checkpoint_contract"]["new_field"],
            "conversation_history_digest",
        )
        self.assertEqual(migration["closure"]["inspected_file_count"], 209)
        self.assertEqual(migration["closure"]["touched_file_count"], 76)
        self.assertEqual(len(entries), 76)
        self.assertEqual(sum(item["checkpoint_count"] for item in entries), 3_078)
        self.assertEqual(
            sum("/runs/benchmarks/" in item["path"] for item in entries),
            50,
        )
        self.assertEqual(
            sum("/showcases/lab1-" in item["path"] for item in entries),
            12,
        )
        self.assertEqual(
            sum("/showcases/lab2-" in item["path"] for item in entries),
            12,
        )
        for item in entries:
            self.assertEqual(len(item["old_sha256"]), 64)
            self.assertEqual(len(item["new_sha256"]), 64)
            self.assertEqual(len(item["normalized_semantic_sha256"]), 64)
            self.assertNotEqual(item["old_sha256"], item["new_sha256"])
            self.assertNotIn("/archive/", item["path"])
            self.assertTrue(item["transform"])
            path = PROJECT_ROOT / item["path"]
            if item["path"].startswith(".agent_forge/"):
                continue
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                item["new_sha256"],
            )

    @unittest.skipUnless(
        LOCAL_CONTROL_EVIDENCE and LOCAL_MINI50_EVIDENCE,
        "canonical raw evidence is intentionally local-only",
    )
    def test_applied_context_semantic_migration_verifies_full_local_closure(
        self,
    ) -> None:
        self.assertEqual(
            verify_applied_migration(PROJECT_ROOT),
            {
                "closure_files": 209,
                "migrated_files": 76,
                "checkpoint_objects": 3_078,
            },
        )

    def test_representative_case_rationale_is_complete_and_sanitized(self) -> None:
        manifest = json.loads(
            (PROJECT_ROOT / "benchmarks/showcase/evidence-review-v1.json").read_text(
                encoding="utf-8"
            )
        )
        representatives = manifest["sources"]["evaluation"]["representative_cases"]
        self.assertEqual(len(representatives), 3)
        self.assertEqual(
            {item["role"] for item in representatives},
            {"resolved", "unresolved", "empty_patch"},
        )
        required = {
            "selection_reason",
            "outcome",
            "patch_status",
            "key_turning_point",
            "success_reason",
            "root_cause",
            "what_to_inspect",
            "evidence_boundary",
            "provenance",
        }
        for item in representatives:
            self.assertTrue(required <= item.keys())
            self.assertEqual(len(item["provenance"]["trace_sha256"]), 64)
        serialized = json.dumps(representatives, ensure_ascii=False)
        for forbidden in ("/Users/", "api_key", "provider_secret", "raw_prompt"):
            self.assertNotIn(forbidden, serialized)

        rendered = _render_workspace_view(
            PROJECT_ROOT,
            source_key="evaluation",
            view="overview",
            sources=self.sources,
        )
        self.assertIn("Why selected", rendered)
        self.assertIn("What to inspect", rendered)
        self.assertIn("incomplete_api_propagation", json.dumps(representatives))
        self.assertIn(
            "command_capability_recovery_dead_end", json.dumps(representatives)
        )

    def test_evidence_tree_detects_nested_artifact_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "showcase.json").write_text("{}", encoding="utf-8")
            trace = root / "phases/run/trace.json"
            trace.parent.mkdir(parents=True)
            trace.write_text('{"status":"completed"}', encoding="utf-8")
            configured: dict[str, object] = {
                "evidence_tree": {
                    "algorithm": EVIDENCE_TREE_ALGORITHM,
                    "include": ["showcase.json", "phases/**/*"],
                }
            }
            count, digest = _evidence_tree(
                root,
                _evidence_tree_patterns(configured),
            )
            evidence_tree = configured["evidence_tree"]
            assert isinstance(evidence_tree, dict)
            evidence_tree["file_count"] = count
            evidence_tree["sha256"] = digest

            self.assertTrue(_evidence_tree_integrity(root, configured)[0])
            trace.write_text('{"status":"failed"}', encoding="utf-8")
            self.assertFalse(_evidence_tree_integrity(root, configured)[0])

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
            stale_fanout = root / ".agent_forge/runs/showcases/stale-lab2/fanout"
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
        self.assertIn(
            "django/django @ 69331bb851c34f05bc77e9fc24020fe6908b9cd5", rendered
        )


if __name__ == "__main__":
    unittest.main()

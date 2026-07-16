import tempfile
import time
import unittest
from pathlib import Path

from agent_forge.context.adapters import JsonLongTermMemoryRepository
from agent_forge.context.api import build_evidence_reference
from agent_forge.context.application import LongTermMemoryService
from agent_forge.context.domain import EvidenceReference
from agent_forge.context.memory import Memory
from agent_forge.runtime.adapters import RepositoryContextAssembler


class LongTermMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repository = JsonLongTermMemoryRepository(self.root / "memory")
        self.service = LongTermMemoryService(self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_candidate_is_not_recalled_until_evidence_backed_promotion(self) -> None:
        record = self.service.propose(
            namespace="repo-a",
            key="test command",
            kind="constraint",
            content="Run python -m unittest before completion.",
            confidence=0.9,
            importance=0.9,
        )

        self.assertEqual(
            self.service.recall(
                "complete the implementation",
                namespace="repo-a",
                agent_name="CodingAgent",
            ),
            [],
        )

        promoted = self.service.promote(
            record.memory_id,
            [EvidenceReference("human", "review-1")],
        )
        recalled = self.service.recall(
            "complete the implementation",
            namespace="repo-a",
            agent_name="CodingAgent",
        )

        self.assertEqual(promoted.status, "active")
        self.assertEqual([item.memory_id for item in recalled], [record.memory_id])

    def test_local_file_evidence_records_content_hash(self) -> None:
        evidence_path = self.root / "decision.md"
        evidence_path.write_text("approved architecture decision\n", encoding="utf-8")

        evidence = build_evidence_reference(str(evidence_path))

        self.assertEqual(evidence.source_type, "local_file")
        self.assertEqual(evidence.path, str(evidence_path.resolve()))
        self.assertEqual(len(evidence.sha256), 64)

    def test_workspace_and_agent_private_memory_do_not_leak(self) -> None:
        record = self.service.propose(
            namespace="repo-a",
            key="parser failure",
            kind="failure_pattern",
            content="Parser failures require inspecting malformed JSON.",
            scope="agent_private",
            agent_name="Reviewer",
            confidence=0.9,
            importance=0.8,
        )
        self.service.promote(
            record.memory_id,
            [EvidenceReference("trace", "run-1")],
        )

        self.assertEqual(
            self.service.recall(
                "parser failure",
                namespace="repo-b",
                agent_name="Reviewer",
            ),
            [],
        )
        self.assertEqual(
            self.service.recall(
                "parser failure",
                namespace="repo-a",
                agent_name="Implementer",
            ),
            [],
        )
        self.assertEqual(
            len(
                self.service.recall(
                    "parser failure",
                    namespace="repo-a",
                    agent_name="Reviewer",
                )
            ),
            1,
        )

    def test_new_active_record_supersedes_same_key(self) -> None:
        old = self.service.propose(
            namespace="repo-a",
            key="validation command",
            kind="decision",
            content="Run the old test command.",
        )
        self.service.promote(old.memory_id, [EvidenceReference("human", "old")])
        new = self.service.propose(
            namespace="repo-a",
            key="validation command",
            kind="decision",
            content="Run the new test command.",
        )
        promoted = self.service.promote(
            new.memory_id,
            [EvidenceReference("human", "new")],
        )

        self.assertEqual(promoted.supersedes, old.memory_id)
        self.assertEqual(self.repository.get(old.memory_id).status, "superseded")
        recalled = self.service.recall(
            "validation command",
            namespace="repo-a",
            agent_name="CodingAgent",
        )
        self.assertEqual([item.memory_id for item in recalled], [new.memory_id])

    def test_expired_record_is_not_recalled(self) -> None:
        record = self.service.propose(
            namespace="repo-a",
            key="temporary constraint",
            kind="constraint",
            content="Use the temporary endpoint.",
            importance=1.0,
            expires_at=time.time() - 1,
        )
        self.service.promote(record.memory_id, [EvidenceReference("human", "temp")])

        self.assertEqual(
            self.service.recall(
                "temporary endpoint",
                namespace="repo-a",
                agent_name="CodingAgent",
            ),
            [],
        )

    def test_recalled_memory_is_rendered_as_separate_context_section(self) -> None:
        record = self.service.propose(
            namespace="repo-a",
            key="parser convention",
            kind="fact",
            content="The parser accepts JSON objects only.",
            confidence=1.0,
            importance=0.8,
        )
        self.service.promote(
            record.memory_id,
            [EvidenceReference("test", "parser-contract")],
        )
        memory = Memory()
        memory.seed_long_term(
            self.service.recall(
                "parser JSON",
                namespace="repo-a",
                agent_name="CodingAgent",
            )
        )
        (self.root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")

        report = RepositoryContextAssembler().build(
            task="inspect parser JSON behavior",
            workspace=str(self.root),
            memory=memory,
            tools=[],
            active_skill_cards=[],
            max_chars=4_000,
            permission_summary="read allowed",
        )

        self.assertEqual(len(report.long_term_memory), 1)
        rendered = report.render()
        self.assertIn("long_term_memory", rendered)
        self.assertIn(record.memory_id, rendered)
        self.assertIn("parser-contract", rendered)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from agent_forge.runtime.adapters.thread_json import JsonConversationThreadRepository
from apps.operator_console.application import ConversationThreadLibrary


class ConversationThreadLibraryTest(unittest.TestCase):
    def test_navigation_updates_share_canonical_thread_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = JsonConversationThreadRepository(Path(tmp) / "threads")
            library = ConversationThreadLibrary(repository)

            thread = library.create(
                task="Repair settlement reconciliation and verify focused tests",
                workspace=tmp,
            )
            renamed = library.rename(thread.thread_id, "结算幂等修复")
            pinned = library.toggle_pinned(thread.thread_id)

            self.assertEqual(renamed.title, "结算幂等修复")
            self.assertTrue(pinned.pinned)
            self.assertEqual(library.list_active()[0].thread_id, thread.thread_id)

            library.set_archived(thread.thread_id)
            self.assertEqual(library.list_active(), [])
            persisted = repository.get(thread.thread_id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertTrue(persisted.archived)


if __name__ == "__main__":
    unittest.main()

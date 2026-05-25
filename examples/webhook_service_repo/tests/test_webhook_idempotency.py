import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.event_store import EventStore
from src.job_queue import JobQueue
from src.models import sample_payload, valid_headers
from src.webhook_handler import handle_webhook


class TestWebhookIdempotency(unittest.TestCase):
    def test_duplicate_event_is_ignored_without_duplicate_side_effects(self):
        store = EventStore()
        queue = JobQueue()
        payload = sample_payload(event_id="evt_dup_001")

        first = handle_webhook(payload, valid_headers(), store, queue)
        second = handle_webhook(payload, valid_headers(), store, queue)

        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "duplicate_ignored")
        self.assertEqual(store.count("evt_dup_001"), 1)
        self.assertEqual(queue.count("evt_dup_001"), 1)

    def test_single_valid_event_is_accepted(self):
        store = EventStore()
        queue = JobQueue()
        payload = sample_payload(event_id="evt_single_001")

        result = handle_webhook(payload, valid_headers(), store, queue)

        self.assertEqual(result["status"], "accepted")
        self.assertEqual(store.count("evt_single_001"), 1)
        self.assertEqual(queue.count("evt_single_001"), 1)


if __name__ == "__main__":
    unittest.main()

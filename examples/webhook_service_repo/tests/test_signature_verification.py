import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.event_store import EventStore
from src.job_queue import JobQueue
from src.models import invalid_headers, sample_payload
from src.webhook_handler import handle_webhook


class TestSignatureVerification(unittest.TestCase):
    def test_invalid_signature_has_no_side_effects(self):
        store = EventStore()
        queue = JobQueue()
        payload = sample_payload(event_id="evt_bad_sig")

        result = handle_webhook(payload, invalid_headers(), store, queue)

        self.assertEqual(result["status"], "unauthorized")
        self.assertEqual(result["code"], 401)
        self.assertEqual(store.count("evt_bad_sig"), 0)
        self.assertEqual(queue.count("evt_bad_sig"), 0)


if __name__ == "__main__":
    unittest.main()

import http.client
import unittest
from unittest.mock import patch

from agent_forge.runtime.llm_client import OpenAICompatibleLLMClient


class LLMClientTransportTest(unittest.TestCase):
    def test_incomplete_read_becomes_structured_request_failure(self):
        client = OpenAICompatibleLLMClient(
            base_url="http://local.test/v1",
            api_key="test-key",
            model="test-model",
        )
        with patch("urllib.request.urlopen", side_effect=http.client.IncompleteRead(b"partial")):
            response = client.chat([], [])

        self.assertIsNotNone(response.error)
        self.assertEqual(response.error["code"], "request_failed")
        self.assertIn("IncompleteRead", response.error["message"])


if __name__ == "__main__":
    unittest.main()

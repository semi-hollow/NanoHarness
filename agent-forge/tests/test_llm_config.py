import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_forge.runtime.llm_config import load_llm_profile, resolve_llm_config


class TestLLMConfig(unittest.TestCase):
    def test_cli_values_override_env(self):
        with patch.dict(
            "os.environ",
            {
                "AGENT_FORGE_BASE_URL": "http://env",
                "AGENT_FORGE_API_KEY": "env-key",
                "AGENT_FORGE_MODEL": "env-model",
            },
            clear=True,
        ):
            config = resolve_llm_config(
                provider="openai",
                base_url="http://cli",
                api_key="cli-key",
                model="cli-model",
            )

        self.assertEqual(config.base_url, "http://cli")
        self.assertEqual(config.api_key, "cli-key")
        self.assertEqual(config.model, "cli-model")

    def test_profile_values_override_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "profiles.json"
            profile_file.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "ollama": {
                                "provider": "openai",
                                "base_url": "http://localhost:11434/v1",
                                "api_key": "ollama",
                                "model": "qwen2.5-coder:7b",
                                "timeout": 60,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"AGENT_FORGE_MODEL": "env-model"}, clear=True):
                config = resolve_llm_config(
                    provider="mock",
                    profile="ollama",
                    profile_file=str(profile_file),
                )

        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.base_url, "http://localhost:11434/v1")
        self.assertEqual(config.api_key, "ollama")
        self.assertEqual(config.model, "qwen2.5-coder:7b")
        self.assertEqual(config.timeout, 60)

    def test_load_profile_reports_missing_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_file = Path(tmpdir) / "profiles.json"
            profile_file.write_text(json.dumps({"profiles": {"known": {"provider": "mock"}}}), encoding="utf-8")

            with self.assertRaises(ValueError):
                load_llm_profile("missing", str(profile_file))

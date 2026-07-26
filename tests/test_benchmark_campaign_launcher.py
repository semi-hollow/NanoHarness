import unittest
from unittest.mock import patch

from examples import benchmark_campaign


class BenchmarkCampaignLauncherTest(unittest.TestCase):
    @patch("agent_forge.cli.dispatch.main")
    @patch("examples.benchmark_campaign.ensure_swebench")
    @patch("examples.benchmark_campaign.ensure_docker")
    @patch("examples.benchmark_campaign.load_or_store_deepseek_key")
    @patch("examples.benchmark_campaign.os.chdir")
    def test_launcher_uses_verified_official_ten_slot_contract(
        self,
        _chdir,
        load_key,
        ensure_runtime,
        ensure_official_harness,
        forge_main,
    ):
        benchmark_campaign.main()

        argv = forge_main.call_args.args[0]
        self.assertEqual(argv[:2], ["bench", "campaign"])
        self.assertEqual(argv[argv.index("--dataset") + 1], benchmark_campaign.DATASET)
        self.assertEqual(argv[argv.index("--repetitions") + 1], "1")
        self.assertEqual(argv[argv.index("--official-cache-level") + 1], "instance")
        self.assertIn("--evaluate", argv)
        self.assertIn("--allow-dirty", argv)
        load_key.assert_called_once()
        ensure_runtime.assert_called_once()
        ensure_official_harness.assert_called_once()


if __name__ == "__main__":
    unittest.main()

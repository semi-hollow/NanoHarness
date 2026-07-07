import tempfile
import unittest
from pathlib import Path

from agent_forge.safety.sandbox import WorkspaceSandbox
from agent_forge.tools.read_file import ReadFileTool


class ReadFileToolTest(unittest.TestCase):
    def test_reads_requested_line_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "module.py"
            target.write_text("\n".join(f"line {i}" for i in range(1, 301)), encoding="utf-8")
            tool = ReadFileTool(WorkspaceSandbox(root))

            observation = tool.execute({"path": "module.py", "offset": 200, "limit": 3})

            self.assertTrue(observation.success, observation.content)
            self.assertIn("window=200-202", observation.content)
            self.assertIn("200: line 200", observation.content)
            self.assertIn("202: line 202", observation.content)
            self.assertNotIn("1: line 1", observation.content)

    def test_accepts_string_offsets_from_llm_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "module.py"
            target.write_text("\n".join(f"line {i}" for i in range(1, 20)), encoding="utf-8")
            tool = ReadFileTool(WorkspaceSandbox(root))

            observation = tool.execute({"path": "module.py", "offset": "10", "limit": "2"})

            self.assertTrue(observation.success, observation.content)
            self.assertIn("window=10-11", observation.content)


if __name__ == "__main__":
    unittest.main()

import unittest
from agent_forge.context.repo_map import build_repo_map
from agent_forge.context.rag import retrieve
from agent_forge.context.token_budget import truncate
from agent_forge.context.memory import Memory
from agent_forge.context.context_builder import build_context_report
from agent_forge.context.file_ranker import rank_files
from agent_forge.context.symbol_search import symbol_search
from agent_forge.runtime.observation import Observation
class T(unittest.TestCase):
    def test_context(self):
        m=build_repo_map('.')
        self.assertIn('calculator.py',m)
        self.assertTrue(retrieve('calculator',m.splitlines()))
        self.assertIn('[truncated]',truncate('x'*3000,100))
        mem=Memory(); mem.add('a'); self.assertEqual(mem.recent()[-1],'a')
        mem.set('style','safe'); self.assertEqual(mem.get('style'),'safe')
        mem.add_observation(Observation('read_file',True,'ok'))
        self.assertEqual(mem.recent_observations()[-1].tool_name,'read_file')
        self.assertIn('style=safe',mem.summary())
        mem.clear(); self.assertEqual(mem.recent(),[])

    def test_symbol_search_and_file_ranker(self):
        hits = symbol_search("add", ".")
        self.assertTrue(any(hit.name == "add" and hit.kind == "def" for hit in hits))
        ranked = rank_files("calculator add", build_repo_map(".").splitlines(), ".")
        self.assertIn("examples/demo_repo/src/calculator.py", ranked[:5])

    def test_context_budget_report(self):
        mem = Memory()
        mem.add("prefer safe tools")
        report = build_context_report("fix calculator add", build_repo_map("."), mem, max_chars=300, root=".", tools=[{"name":"read_file"}])
        self.assertTrue(report.repo_map)
        self.assertTrue(report.selected_files)
        self.assertIn("read_file", report.available_tools)
        self.assertIn("dangerous commands denied", report.permission_summary)
        self.assertIsInstance(report.total_chars, int)
        self.assertIn("truncated:", report.render())

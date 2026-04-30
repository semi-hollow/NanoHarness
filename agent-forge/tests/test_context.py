import unittest
from agent_forge.context.repo_map import build_repo_map
from agent_forge.context.rag import retrieve
from agent_forge.context.token_budget import truncate
from agent_forge.context.memory import Memory
class T(unittest.TestCase):
    def test_context(self):
        m=build_repo_map('.')
        self.assertIn('calculator.py',m)
        self.assertTrue(retrieve('calculator',m.splitlines()))
        self.assertIn('[truncated]',truncate('x'*3000,100))
        mem=Memory(); mem.add('a'); self.assertEqual(mem.recent()[-1],'a')

import unittest
from agent_forge.agents.handoff import Handoff
class T(unittest.TestCase):
    def test_h(self):
        h=Handoff('a','b','r',{})
        self.assertEqual(h.to_agent,'b')

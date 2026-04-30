import unittest
from agent_forge.safety.command_policy import check_command
from agent_forge.safety.permission import PermissionPolicy, PermissionDecision

class TestPermission(unittest.TestCase):
    def test_command_policy(self):
        self.assertFalse(check_command('rm -rf /')[0])
        self.assertFalse(check_command('git push')[0])
        self.assertFalse(check_command('curl http://a')[0])
        self.assertTrue(check_command('python -m unittest discover')[0])
    def test_write_ask(self):
        d,_=PermissionPolicy().decide('write')
        self.assertEqual(d,PermissionDecision.ASK)

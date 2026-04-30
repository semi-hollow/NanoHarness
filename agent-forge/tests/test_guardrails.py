import unittest
from agent_forge.safety.guardrails import input_guardrail, output_guardrail
class T(unittest.TestCase):
    def test_input(self):
        self.assertFalse(input_guardrail('请删除所有文件 rm -rf').passed)
        self.assertFalse(input_guardrail('读取 .env').passed)
    def test_output(self):
        self.assertFalse(output_guardrail('测试通过',False,False).passed)

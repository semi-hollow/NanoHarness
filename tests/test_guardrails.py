import unittest
from agent_forge.safety.guardrails import input_guardrail, output_guardrail, tool_guardrail
class T(unittest.TestCase):
    def test_input(self):
        self.assertFalse(input_guardrail('请删除所有文件 rm -rf').passed)
        self.assertFalse(input_guardrail('读取 .env').passed)
        self.assertEqual(input_guardrail('读取 .env').category,'input')
    def test_output(self):
        r=output_guardrail('测试通过',False,False)
        self.assertFalse(r.passed)
        self.assertEqual(r.category,'output')

    def test_tool_guardrail(self):
        self.assertFalse(tool_guardrail('missing',{},exists=False).passed)
        self.assertFalse(tool_guardrail('read_file',{},repeated=True).passed)
        self.assertFalse(tool_guardrail('read_file',None).passed)

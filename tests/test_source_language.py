from __future__ import annotations

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "agent_forge"
CHINESE = re.compile(r"[\u4e00-\u9fff]")
DIRECTIVES = ("#!", "# noqa", "# type:", "# pragma", "# nosec", "# fmt:")


class SourceLanguageTest(unittest.TestCase):
    def test_explanatory_comments_and_docstrings_use_chinese(self) -> None:
        """教学型源码以中文解释；工具指令类注释不参与语言约束。"""

        issues: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                docstring = ast.get_docstring(node, clean=False)
                if docstring and not CHINESE.search(docstring):
                    issues.append(
                        f"{path.relative_to(PROJECT_ROOT)}:"
                        f"{getattr(node, 'lineno', 1)} 英文 docstring"
                    )
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type != tokenize.COMMENT:
                    continue
                lowered = token.string.lower()
                if CHINESE.search(token.string) or lowered.startswith(DIRECTIVES):
                    continue
                issues.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{token.start[0]} "
                    f"英文注释 {token.string}"
                )
        self.assertEqual(issues, [], "源码说明应统一使用中文")


if __name__ == "__main__":
    unittest.main()

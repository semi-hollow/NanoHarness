import unittest
from src.calculator import add
class T(unittest.TestCase):
    def test_add(self): self.assertEqual(add(2,3),5)
if __name__=="__main__": unittest.main()

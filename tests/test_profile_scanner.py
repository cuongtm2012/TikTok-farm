import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.profile_scanner import parse_count_text


class TestParseCountText(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_count_text("1234"), 1234)

    def test_k(self):
        self.assertEqual(parse_count_text("1.2K"), 1200)

    def test_m(self):
        self.assertEqual(parse_count_text("3.5M"), 3500000)

    def test_empty(self):
        self.assertEqual(parse_count_text(""), 0)


if __name__ == "__main__":
    unittest.main()

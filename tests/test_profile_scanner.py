import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.profile_scanner import (
    extract_counters_from_body_text,
    merge_profile_stats,
    parse_count_text,
)


class TestParseCountText(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_count_text("1234"), 1234)

    def test_k(self):
        self.assertEqual(parse_count_text("1.2K"), 1200)

    def test_m(self):
        self.assertEqual(parse_count_text("3.5M"), 3500000)

    def test_empty(self):
        self.assertEqual(parse_count_text(""), 0)

    def test_vietnamese_body_counters(self):
        body = "long đẹp trai 142 đã follow 2477 follower 30.8k lượt thích"
        stats = extract_counters_from_body_text(body)
        self.assertEqual(stats["following"], 142)
        self.assertEqual(stats["followers"], 2477)
        self.assertEqual(stats["likes"], 30800)

    def test_english_body_counters(self):
        body = "142 following 2477 followers 30.8k likes"
        stats = extract_counters_from_body_text(body)
        self.assertEqual(stats["following"], 142)
        self.assertEqual(stats["followers"], 2477)
        self.assertEqual(stats["likes"], 30800)

    def test_merge_json_overrides_wrong_dom(self):
        dom = {"followers": 142, "following": 2477, "likes": 0, "stats_extracted": True}
        body = extract_counters_from_body_text("142 đã follow 2477 follower 30.8k lượt thích")
        json_stats = {"followers": 2477, "following": 142, "likes": 30800, "_json_stats": True}
        merged = merge_profile_stats(dom, body, json_stats)
        self.assertEqual(merged["followers"], 2477)
        self.assertEqual(merged["following"], 142)
        self.assertEqual(merged["likes"], 30800)


if __name__ == "__main__":
    unittest.main()

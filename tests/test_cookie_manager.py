import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cookie_manager import CookieManager


class TestCookieManager(unittest.TestCase):
    def test_parse_cookie_string(self):
        cookies = CookieManager.parse_cookie_string("sessionid=abc123; tt_chain_token=xyz")
        self.assertTrue(CookieManager.validate_session(cookies))
        self.assertEqual(CookieManager.get_session_id(cookies), "abc123")

    def test_parse_sessionid(self):
        cookies = CookieManager.parse_sessionid("onlysid")
        self.assertEqual(cookies[0]["name"], "sessionid")

    def test_to_playwright_expiry(self):
        raw = [{"name": "a", "value": "b", "expiry": 999}]
        out = CookieManager.to_playwright_format(raw)
        self.assertEqual(out[0]["expires"], 999)


if __name__ == "__main__":
    unittest.main()

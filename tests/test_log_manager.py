import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database import Database
from src.log_manager import LogManager


class TestLogManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(driver="sqlite", path=self.tmp.name)
        self.lm = LogManager(self.db)

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_log_and_query(self):
        self.lm.log(1, "farm", "INFO", "Farm started", {"scrolls": 5})
        logs = self.lm.get_logs(account_id=1, limit=10)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["message"], "Farm started")
        self.assertEqual(logs[0]["log_type"], "farm")

    def test_sanitize_password(self):
        self.lm.log(2, "sync", "INFO", "ok", {"password": "secret", "followers": 100})
        logs = self.lm.get_logs(account_id=2, limit=1)
        self.assertNotIn("password", logs[0].get("details", {}))
        self.assertEqual(logs[0]["details"].get("followers"), 100)

    def test_clear_logs(self):
        self.lm.log(3, "health", "WARNING", "check")
        n = self.lm.clear_logs(3)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self.lm.get_logs(account_id=3), [])


if __name__ == "__main__":
    unittest.main()

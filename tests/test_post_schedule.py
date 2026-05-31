import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.post_engine import validate_schedule


class TestValidateSchedule(unittest.TestCase):
    def test_too_soon(self):
        dt = datetime.utcnow() + timedelta(minutes=5)
        ok, msg = validate_schedule(dt)
        self.assertFalse(ok)
        self.assertIn("20 minutes", msg)

    def test_minute_not_multiple_of_5(self):
        dt = datetime.utcnow() + timedelta(hours=1)
        dt = dt.replace(minute=7, second=0, microsecond=0)
        ok, msg = validate_schedule(dt)
        self.assertFalse(ok)

    def test_valid(self):
        dt = datetime.utcnow() + timedelta(hours=2)
        dt = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
        ok, _ = validate_schedule(dt)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

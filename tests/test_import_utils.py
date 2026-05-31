"""Tests for seller format and cookie parsing (SPEC v2.1)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.import_utils import parse_cookie_string, parse_seller_line, parse_seller_bulk


def test_parse_cookie_string():
    cookies = parse_cookie_string("sessionid=abc123; msToken=xyz")
    assert len(cookies) == 2
    assert cookies[0]["name"] == "sessionid"
    assert cookies[0]["domain"] == ".tiktok.com"


def test_parse_seller_line_basic():
    row = parse_seller_line("user1|pass1|e@mail.com|mpass|sessionid=abc|uid99")
    assert row["username"] == "user1"
    assert row["password"] == "pass1"
    assert row["uid"] == "uid99"
    data = json.loads(row["cookie_data"])
    assert any(c["name"] == "sessionid" for c in data)


def test_parse_seller_line_cookie_with_pipe():
    line = "user|pass|e@x.com|mp|part1=1|part2=2|uid1"
    row = parse_seller_line(line)
    assert row["uid"] == "uid1"
    cookies = json.loads(row["cookie_data"])
    assert len(cookies) >= 1


def test_parse_seller_bulk_skips_empty():
    items, errs = parse_seller_bulk("good|pass|||sid=a|\n\n# comment\n", default_proxy_id=2)
    assert len(items) == 1
    assert items[0]["proxy_id"] == 2
    assert len(errs) == 0


if __name__ == "__main__":
    test_parse_cookie_string()
    test_parse_seller_line_basic()
    test_parse_seller_line_cookie_with_pipe()
    test_parse_seller_bulk_skips_empty()
    print("All tests passed")

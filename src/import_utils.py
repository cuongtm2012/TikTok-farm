# TikTok Farm — CSV / bulk import helpers

import csv
import io
from typing import List, Dict, Tuple


ACCOUNT_CSV_FIELDS = ["username", "proxy_id", "password", "status", "notes"]
PROXY_CSV_FIELDS = ["ip", "port", "protocol", "username", "password", "status"]

ACCOUNT_CSV_TEMPLATE = """username,proxy_id,password,status,notes
user1,1,pass123,pending,pilot 1
user2,2,pass456,warming,pilot 2
"""

PROXY_CSV_TEMPLATE = """ip,port,protocol,username,password,status
1.2.3.4,8080,http,,,active
5.6.7.8,3128,socks5,user,pass,active
"""


def parse_csv_text(content: str) -> Tuple[List[Dict], List[str]]:
    """Parse CSV string into list of row dicts. Returns (rows, errors)."""
    errors: List[str] = []
    if not content or not content.strip():
        return [], ["Empty CSV content"]

    try:
        reader = csv.DictReader(io.StringIO(content.strip()))
        if not reader.fieldnames:
            return [], ["Missing CSV header row"]

        rows = []
        for i, row in enumerate(reader, start=2):
            cleaned = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not any(cleaned.values()):
                continue
            rows.append(cleaned)
        return rows, errors
    except Exception as e:
        return [], [str(e)]


def normalize_account_row(row: Dict) -> Dict:
    return {
        "username": row.get("username") or row.get("user") or "",
        "proxy_id": int(row.get("proxy_id") or row.get("proxy") or 0),
        "password": row.get("password") or row.get("pass") or "",
        "status": row.get("status") or "",
        "notes": row.get("notes") or row.get("note") or "",
    }


def normalize_proxy_row(row: Dict) -> Dict:
    return {
        "ip": row.get("ip") or "",
        "port": int(row.get("port") or 0),
        "protocol": (row.get("protocol") or "http").lower(),
        "username": row.get("username") or "",
        "password": row.get("password") or "",
        "status": row.get("status") or "active",
    }

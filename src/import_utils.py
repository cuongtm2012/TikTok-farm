# TikTok Farm — CSV / bulk / seller import helpers

import base64
import csv
import io
import json
import re
import urllib.parse
from typing import List, Dict, Tuple, Any, Optional


ACCOUNT_CSV_FIELDS = ["username", "proxy_id", "password", "status", "notes", "cookie_data"]
PROXY_CSV_FIELDS = ["ip", "port", "protocol", "username", "password", "status"]

ACCOUNT_CSV_TEMPLATE = """username,proxy_id,password,status,notes,cookie_data
user1,1,pass123,pending,pilot 1,
user2,2,pass456,warming,pilot 2,
"""

PROXY_CSV_TEMPLATE = """ip,port,protocol,username,password,status
1.2.3.4,8080,http,,,active
5.6.7.8,3128,socks5,user,pass,active
"""


def parse_cookie_string(cookie_str: str) -> List[Dict[str, Any]]:
    """'name=value; name=value' → Playwright cookie objects (via CookieManager)."""
    from src.cookie_manager import CookieManager

    return CookieManager.parse_cookie_string(cookie_str)


def parse_seller_cookie_field(cookie_str: str) -> List[Dict[str, Any]]:
    """
    Parse COOKIES column from seller lines: cookie string, JSON, base64, or bare sessionid.
    """
    from src.cookie_manager import CookieManager

    s = (cookie_str or "").strip()
    if not s:
        return []

    if "%3D" in s or "%3B" in s or "%7C" in s:
        try:
            s = urllib.parse.unquote(s)
        except Exception:
            pass

    # JSON before semicolon parser (avoids mis-reading {"sessionid":"..."})
    if s.startswith("{") or s.startswith("["):
        try:
            obj = json.loads(s)
            if isinstance(obj, list):
                return CookieManager.to_playwright_format(obj)
            if isinstance(obj, dict):
                if isinstance(obj.get("cookies"), list):
                    return CookieManager.to_playwright_format(obj["cookies"])
                flat = [
                    {"name": str(k), "value": str(v)}
                    for k, v in obj.items()
                    if v is not None and str(v).strip()
                ]
                if flat:
                    return CookieManager.to_playwright_format(flat)
        except json.JSONDecodeError:
            pass

    cookies = CookieManager.parse_cookie_string(s)
    if cookies:
        return cookies

    # Bare sessionid token (no name=value)
    if "=" not in s and ";" not in s:
        return CookieManager.parse_sessionid(s)

    # Base64-encoded cookie string or JSON
    if len(s) >= 12 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", s):
        for raw in (s, s + "=" * (-len(s) % 4)):
            try:
                decoded = base64.b64decode(raw).decode("utf-8", errors="ignore").strip()
                if decoded:
                    cookies = CookieManager.parse_cookie_string(decoded)
                    if cookies:
                        return cookies
            except Exception:
                pass
        try:
            decoded = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode(
                "utf-8", errors="ignore"
            ).strip()
            if decoded:
                cookies = CookieManager.parse_cookie_string(decoded)
                if cookies:
                    return cookies
        except Exception:
            pass

    return []


def parse_seller_line(line: str) -> Dict[str, Any]:
    """
    Parse seller format: USER|PASS|EMAIL|EMAIL_PASS|COOKIES|UID
    If COOKIES contain '|', use 6+ fields: cookie = join(parts[4:-1]), uid = last part.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        raise ValueError("Empty line")

    # Some sellers use tabs instead of pipes
    if "\t" in line and line.count("\t") >= line.count("|"):
        parts = [p.strip() for p in line.split("\t")]
    else:
        parts = [p.strip() for p in line.split("|")]

    if len(parts) < 2:
        raise ValueError("Invalid format: need at least USER|PASS")

    username = parts[0].strip().lstrip("@")
    password = parts[1].strip()
    email = ""
    email_pass = ""
    cookie_str = ""
    uid = ""

    if len(parts) == 3:
        # USER|PASS|COOKIES
        cookie_str = parts[2]
    elif len(parts) == 4 and "=" in parts[3]:
        # USER|PASS|EMAIL|COOKIES (no email_pass / uid)
        email = parts[2]
        cookie_str = parts[3]
    else:
        email = parts[2] if len(parts) > 2 else ""
        email_pass = parts[3] if len(parts) > 3 else ""
        if len(parts) >= 6:
            uid = parts[-1]
            cookie_str = "|".join(parts[4:-1])
        elif len(parts) == 5:
            cookie_str = parts[4]
        elif len(parts) > 4:
            cookie_str = parts[4]

    if not username:
        raise ValueError("Missing username")

    cookies = parse_seller_cookie_field(cookie_str)
    cookie_data = json.dumps(cookies) if cookies else ""

    notes_parts = []
    if email:
        notes_parts.append(f"email={email}")
    if email_pass:
        notes_parts.append(f"email_pass={email_pass[:4]}***")
    if uid:
        notes_parts.append(f"uid={uid}")

    return {
        "username": username,
        "password": password,
        "email": email,
        "email_password": email_pass,
        "cookie_data": cookie_data,
        "uid": uid,
        "notes": "; ".join(notes_parts),
    }


def parse_seller_bulk(text: str, default_proxy_id: int = 0) -> Tuple[List[Dict], List[Dict]]:
    """Parse multiline seller paste. Returns (items, errors)."""
    items: List[Dict] = []
    errors: List[Dict] = []

    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = parse_seller_line(line)
            row["proxy_id"] = default_proxy_id
            row["status"] = "pending"
            items.append(row)
        except ValueError as e:
            errors.append({"row": i, "error": str(e)})

    return items, errors


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
    cookie_raw = row.get("cookie_data") or row.get("cookies") or ""
    cookie_data = ""
    if cookie_raw:
        if cookie_raw.strip().startswith("["):
            cookie_data = cookie_raw.strip()
        else:
            cookies = parse_cookie_string(cookie_raw)
            cookie_data = json.dumps(cookies) if cookies else ""

    return {
        "username": row.get("username") or row.get("user") or "",
        "proxy_id": int(row.get("proxy_id") or row.get("proxy") or 0),
        "password": row.get("password") or row.get("pass") or "",
        "status": row.get("status") or "",
        "notes": row.get("notes") or row.get("note") or "",
        "cookie_data": cookie_data,
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

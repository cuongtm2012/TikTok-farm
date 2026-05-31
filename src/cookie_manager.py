# TikTok Farm — Cookie parsing & Playwright storage (tiktok-uploader auth.py pattern)

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DOMAIN = ".tiktok.com"
DEFAULT_EXPIRES = 2147483647
VALID_SAMESITE = {"Strict", "Lax", "None"}


class CookieManager:
    """Parse and normalize TikTok cookies for Playwright."""

    @staticmethod
    def parse_cookie_string(raw: str) -> List[Dict[str, Any]]:
        """Parse 'name=value; name=value' or JSON list → cookie dicts."""
        if not raw or not str(raw).strip():
            return []

        text = str(raw).strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return CookieManager.to_playwright_format(parsed)
            except json.JSONDecodeError:
                pass

        cookies: List[Dict[str, Any]] = []
        for part in text.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if not name:
                continue
            cookies.append(
                {
                    "name": name,
                    "value": value.strip(),
                    "domain": DEFAULT_DOMAIN,
                    "path": "/",
                }
            )
        return CookieManager.to_playwright_format(cookies)

    @staticmethod
    def parse_sessionid(session_id: str) -> List[Dict[str, Any]]:
        """Single sessionid string → minimal cookie set."""
        sid = (session_id or "").strip()
        if not sid:
            return []
        return CookieManager.to_playwright_format(
            [{"name": "sessionid", "value": sid, "domain": DEFAULT_DOMAIN, "path": "/"}]
        )

    @staticmethod
    def parse_cookie_line(line: str) -> List[Dict[str, Any]]:
        """Parse seller pipe line: USER|PASS|EMAIL|EMAIL_PASS|COOKIES|UID."""
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            return []
        if len(parts) >= 6:
            cookie_str = "|".join(parts[4:-1]).strip()
        else:
            cookie_str = parts[4].strip()
        if not cookie_str:
            return []
        if cookie_str.startswith("sessionid=") or ";" not in cookie_str and "=" not in cookie_str:
            return CookieManager.parse_sessionid(cookie_str)
        return CookieManager.parse_cookie_string(cookie_str)

    @staticmethod
    def parse_netscape_file(path: str) -> List[Dict[str, Any]]:
        """Parse Netscape cookies.txt → Playwright cookies."""
        cookies: List[Dict[str, Any]] = []
        p = Path(path)
        if not p.is_file():
            return cookies
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, path, secure, expiry, name, value = parts[:7]
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain if domain.startswith(".") else f".{domain}",
                    "path": path or "/",
                    "expires": int(expiry) if expiry.isdigit() else DEFAULT_EXPIRES,
                    "httpOnly": False,
                    "secure": secure.upper() == "TRUE",
                }
            )
        return CookieManager.to_playwright_format(cookies)

    @staticmethod
    def to_playwright_format(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize keys: expiry→expires, fix sameSite."""
        out: List[Dict[str, Any]] = []
        for c in cookies:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            item = {
                "name": str(c["name"]),
                "value": str(c.get("value", "")),
                "domain": c.get("domain") or DEFAULT_DOMAIN,
                "path": c.get("path") or "/",
            }
            exp = c.get("expires", c.get("expiry", DEFAULT_EXPIRES))
            try:
                item["expires"] = int(exp) if exp else DEFAULT_EXPIRES
            except (TypeError, ValueError):
                item["expires"] = DEFAULT_EXPIRES
            ss = c.get("sameSite")
            if ss in VALID_SAMESITE:
                item["sameSite"] = ss
            if c.get("httpOnly") is not None:
                item["httpOnly"] = bool(c["httpOnly"])
            if c.get("secure") is not None:
                item["secure"] = bool(c["secure"])
            else:
                item["secure"] = True
            out.append(item)
        return out

    @staticmethod
    def save_to_storage_state(cookies: List[Dict[str, Any]], output_path: str) -> bool:
        """Write Playwright storage_state JSON."""
        if not cookies:
            return False
        try:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "cookies": CookieManager.to_playwright_format(cookies),
                "origins": [],
            }
            path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            return True
        except Exception as e:
            logger.warning(f"save_to_storage_state failed: {e}")
            return False

    @staticmethod
    def from_storage_state(path: str) -> List[Dict[str, Any]]:
        """Read storage_state.json → cookies list."""
        p = Path(path)
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return CookieManager.to_playwright_format(data.get("cookies") or [])
        except Exception as e:
            logger.warning(f"from_storage_state failed: {e}")
            return []

    @staticmethod
    def validate_session(cookies: List[Dict[str, Any]]) -> bool:
        return CookieManager.get_session_id(cookies) is not None

    @staticmethod
    def get_session_id(cookies: List[Dict[str, Any]]) -> Optional[str]:
        for c in cookies:
            if c.get("name") == "sessionid" and c.get("value"):
                return str(c["value"])
        return None

    @staticmethod
    def cookies_from_account_data(cookie_data: Any) -> List[Dict[str, Any]]:
        """Load cookies from DB field (JSON list or cookie string)."""
        if not cookie_data:
            return []
        if isinstance(cookie_data, list):
            return CookieManager.to_playwright_format(cookie_data)
        if isinstance(cookie_data, str):
            return CookieManager.parse_cookie_string(cookie_data)
        return []

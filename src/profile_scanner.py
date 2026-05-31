# TikTok Farm — Browser-based public profile scanner (no ms_token / TikTokApi)

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeout
except ImportError:
    PlaywrightTimeout = Exception  # type: ignore

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
]

BROWSER_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-CH-UA": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _scan_context_id(username: str) -> int:
    """Ephemeral context key (does not collide with normal account IDs)."""
    return 900_000_000 + (hash(username.lower()) & 0x7FFFFFFF) % 99_000_000


def parse_count_text(raw: str) -> int:
    """Parse TikTok count strings: 1.2M, 12K, 1,234."""
    if not raw:
        return 0
    s = raw.strip().replace(",", "").replace(" ", "").upper()
    if not s or s in ("-", "—"):
        return 0
    try:
        if s.endswith("K"):
            return int(float(s[:-1]) * 1_000)
        if s.endswith("M"):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith("B"):
            return int(float(s[:-1]) * 1_000_000_000)
        return int(float(s))
    except ValueError:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0


def _fail(username: str, error: str, error_type: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "error_type": error_type,
        "username": username,
        "scanned_at": _utc_now(),
    }


def _ok(username: str, **fields) -> Dict[str, Any]:
    return {
        "success": True,
        "error": None,
        "error_type": None,
        "username": username,
        "display_name": fields.get("display_name", ""),
        "bio": fields.get("bio", ""),
        "avatar_url": fields.get("avatar_url", ""),
        "followers": fields.get("followers", 0),
        "following": fields.get("following", 0),
        "likes": fields.get("likes", 0),
        "total_posts": fields.get("total_posts", 0),
        "verified": fields.get("verified", False),
        "private_account": fields.get("private_account", False),
        "scanned_at": _utc_now(),
    }


class ProfileScanner:
    """Fetch TikTok profile stats via Playwright (public profile page)."""

    NAV_TIMEOUT_MS = 15_000
    MAX_RETRIES = 2

    def __init__(self, browser_manager):
        self.browser = browser_manager
        self._last_scan_at: float = 0
        self._min_interval_sec = 1.0

    def status(self) -> Dict[str, Any]:
        playwright_ok = False
        engine = "unknown"
        try:
            from src.browser_manager import PLAYWRIGHT_AVAILABLE, CAMOUFOX_AVAILABLE

            playwright_ok = PLAYWRIGHT_AVAILABLE
            if self.browser.use_camoufox and CAMOUFOX_AVAILABLE:
                engine = "Camoufox"
            elif PLAYWRIGHT_AVAILABLE:
                engine = "Chromium"
        except Exception:
            pass
        return {
            "method": "browser",
            "ready": playwright_ok,
            "engine": engine,
            "headless": getattr(self.browser, "headless", True),
            "message": "Profile scanner ready" if playwright_ok else "Playwright not installed",
        }

    async def _rate_limit_wait(self):
        import time

        elapsed = time.time() - self._last_scan_at
        if elapsed < self._min_interval_sec:
            await asyncio.sleep(self._min_interval_sec - elapsed)
        self._last_scan_at = time.time()

    async def fetch_profile(self, username: str, proxy_url: Optional[str] = None) -> Dict[str, Any]:
        username = (username or "").strip().lstrip("@")
        if not username:
            return _fail("", "Username is required", "not_found")

        await self._rate_limit_wait()

        last_error = "Profile scan failed"
        last_type = "timeout"

        for attempt in range(self.MAX_RETRIES):
            ua = USER_AGENTS[attempt % len(USER_AGENTS)]
            ctx_id = _scan_context_id(f"{username}_{attempt}")
            try:
                result = await self._scan_once(username, proxy_url, ua, ctx_id)
                if result.get("success") or result.get("error_type") in (
                    "not_found",
                    "private",
                    "blocked",
                ):
                    return result
                last_error = result.get("error") or last_error
                last_type = result.get("error_type") or last_type
                if result.get("error_type") == "captcha" and attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(3)
                    continue
            except Exception as e:
                logger.warning(f"Profile scan attempt {attempt + 1} for @{username}: {e}")
                last_error = str(e)
                last_type = "timeout" if "timeout" in str(e).lower() else "captcha"
            finally:
                try:
                    await self.browser.close_context(ctx_id)
                except Exception:
                    pass

        return _fail(username, last_error, last_type)

    async def _scan_once(
        self,
        username: str,
        proxy_url: Optional[str],
        user_agent: str,
        ctx_id: int,
    ) -> Dict[str, Any]:
        url = f"https://www.tiktok.com/@{username}"

        context = await self.browser.create_context(
            ctx_id,
            proxy_url=proxy_url,
            user_agent=user_agent,
            extra_http_headers=BROWSER_HEADERS,
        )
        page = await context.new_page()

        try:
            response = await page.goto(
                url,
                timeout=self.NAV_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )
            status = response.status if response else 0

            if status == 403:
                return _fail(
                    username,
                    "TikTok blocked this IP. Try a different proxy.",
                    "blocked",
                )

            await asyncio.sleep(1.5)
            body_text = (await page.inner_text("body")).lower() if await page.query_selector("body") else ""

            if self._is_cloudflare(body_text):
                return _fail(
                    username,
                    "Cloudflare challenge detected. Retry with residential proxy.",
                    "captcha",
                )

            if "something went wrong" in body_text:
                await asyncio.sleep(3)
                await page.reload(wait_until="domcontentloaded", timeout=self.NAV_TIMEOUT_MS)
                body_text = (await page.inner_text("body")).lower() if await page.query_selector("body") else ""

            final_url = page.url.lower()
            if "/foryou" in final_url or final_url.rstrip("/").endswith("tiktok.com"):
                if f"/@{username.lower()}" not in final_url:
                    return _fail(
                        username,
                        f"Account @{username} not found or banned.",
                        "not_found",
                    )

            if self._is_private(body_text):
                display = await self._text(page, "[data-e2e='user-title'], h1, .share-title")
                return _ok(
                    username,
                    display_name=display or username,
                    private_account=True,
                )

            try:
                await page.wait_for_selector(
                    "[data-e2e='user-follower-count'], .count-infos, h2[data-e2e='user-follower-count']",
                    timeout=8000,
                )
            except PlaywrightTimeout:
                pass

            parsed = await self._extract_from_dom(page)
            if not parsed.get("followers") and not parsed.get("following"):
                parsed = await self._extract_fallback(page, username, parsed)

            if not parsed.get("followers") and not parsed.get("following") and not parsed.get("likes"):
                return _fail(
                    username,
                    "Profile not accessible — stats not found on page.",
                    "not_found",
                )

            return _ok(username, **parsed)

        except PlaywrightTimeout:
            return _fail(
                username,
                "Timeout connecting to TikTok. Proxy may be slow or blocked.",
                "timeout",
            )

    @staticmethod
    def _is_cloudflare(text: str) -> bool:
        markers = ("checking your browser", "cloudflare", "cf-browser-verification", "just a moment")
        return any(m in text for m in markers)

    @staticmethod
    def _is_private(text: str) -> bool:
        return "this account is private" in text or "account is private" in text

    async def _text(self, page, selector: str) -> str:
        try:
            el = await page.query_selector(selector)
            if el:
                return (await el.inner_text()).strip()
        except Exception:
            pass
        return ""

    async def _extract_from_dom(self, page) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "display_name": "",
            "bio": "",
            "avatar_url": "",
            "followers": 0,
            "following": 0,
            "likes": 0,
            "total_posts": 0,
            "verified": False,
        }

        data["display_name"] = await self._text(
            page, "[data-e2e='user-title'], h1[data-e2e='user-title'], .share-title"
        )
        data["bio"] = await self._text(page, "[data-e2e='user-bio'], .share-desc, h2[data-e2e='user-bio']")

        try:
            verified = await page.query_selector("[data-e2e='verified-icon'], .verified-badge")
            data["verified"] = verified is not None
        except Exception:
            pass

        try:
            img = await page.query_selector(
                "img[data-e2e='user-avatar'], img[class*='ImgAvatar'], .avatar img"
            )
            if img:
                data["avatar_url"] = await img.get_attribute("src") or ""
        except Exception:
            pass

        # data-e2e counters (current TikTok layout)
        e2e_map = {
            "followers": "[data-e2e='followers-count'], [data-e2e='user-follower-count']",
            "following": "[data-e2e='following-count'], [data-e2e='user-following-count']",
            "likes": "[data-e2e='likes-count'], [data-e2e='user-likes-count']",
        }
        for key, sel in e2e_map.items():
            txt = await self._text(page, sel)
            if txt:
                data[key] = parse_count_text(txt)

        # Legacy .count-infos layout
        try:
            counts = await page.query_selector_all(".count-infos .count, .count-infos strong")
            texts = []
            for el in counts[:5]:
                t = (await el.inner_text()).strip()
                if t:
                    texts.append(t)
            if len(texts) >= 3 and not data["followers"]:
                data["followers"] = parse_count_text(texts[0])
                data["following"] = parse_count_text(texts[1])
                data["likes"] = parse_count_text(texts[2])
        except Exception:
            pass

        # Video count tab
        posts_txt = await self._text(page, "[data-e2e='user-post-count'], [data-e2e='videos-count']")
        if posts_txt:
            data["total_posts"] = parse_count_text(posts_txt)

        return data

    async def _extract_fallback(
        self, page, username: str, partial: Dict[str, Any]
    ) -> Dict[str, Any]:
        data = dict(partial)

        # JSON-LD / SIGI_STATE in page
        try:
            scripts = await page.query_selector_all("script")
            for script in scripts:
                content = await script.inner_text()
                if not content or "followerCount" not in content:
                    continue
                m = re.search(r'"followerCount"\s*:\s*(\d+)', content)
                if m:
                    data["followers"] = int(m.group(1))
                m = re.search(r'"followingCount"\s*:\s*(\d+)', content)
                if m:
                    data["following"] = int(m.group(1))
                m = re.search(r'"heartCount"\s*:\s*(\d+)', content)
                if m:
                    data["likes"] = int(m.group(1))
                m = re.search(r'"videoCount"\s*:\s*(\d+)', content)
                if m:
                    data["total_posts"] = int(m.group(1))
                if data.get("followers"):
                    break
        except Exception:
            pass

        if not data.get("followers"):
            title = await page.title()
            # e.g. "Jack (@jack) | TikTok" or counts in title
            nums = re.findall(r"([\d.,]+[KMB]?)\s*Followers", title, re.I)
            if nums:
                data["followers"] = parse_count_text(nums[0])

        if not data.get("display_name"):
            data["display_name"] = username

        return data

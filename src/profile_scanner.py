# TikTok Farm — Browser-based public profile scanner (no ms_token / TikTokApi)

import asyncio
import json
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


# Number before label — EN + VI (body text is lowercased before matching)
_COUNTER_BODY_PATTERNS = {
    "following": [
        r"([\d.,]+[kmb]?)\s*(?:following|đã\s*follow|đang\s*follow|subscriptions?)\b",
    ],
    "followers": [
        r"([\d.,]+[kmb]?)\s*(?:followers?|người\s*follow|fan)\b",
    ],
    "likes": [
        r"([\d.,]+[kmb]?)\s*(?:likes?|lượt\s*thích|tim)\b",
    ],
}

_NUMERIC_STAT_KEYS = ("followers", "following", "likes", "total_posts")


def extract_counters_from_body_text(body_text: str) -> Dict[str, Any]:
    """Parse labeled counters from visible page text (EN/VI)."""
    data: Dict[str, Any] = {}
    if not body_text:
        return data
    text = body_text.lower()
    for key, patterns in _COUNTER_BODY_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m:
                data[key] = parse_count_text(m.group(1))
                data["stats_extracted"] = True
                break
    return data


def merge_profile_stats(*sources: Dict[str, Any]) -> Dict[str, Any]:
    """Merge extractions; later sources override (JSON / body over DOM)."""
    merged: Dict[str, Any] = {
        "display_name": "",
        "bio": "",
        "avatar_url": "",
        "followers": 0,
        "following": 0,
        "likes": 0,
        "total_posts": 0,
        "verified": False,
        "private_account": False,
    }
    for src in sources:
        if not src:
            continue
        for key in ("display_name", "bio", "avatar_url"):
            if src.get(key):
                merged[key] = src[key]
        for key in _NUMERIC_STAT_KEYS:
            if key in src and src[key] is not None:
                merged[key] = int(src[key] or 0)
        if src.get("verified"):
            merged["verified"] = True
        if src.get("private_account"):
            merged["private_account"] = True
        if src.get("stats_extracted") or src.get("_json_stats"):
            merged["stats_extracted"] = True
    return merged


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
        "followers": int(fields.get("followers", 0) or 0),
        "following": int(fields.get("following", 0) or 0),
        "likes": int(fields.get("likes", 0) or 0),
        "total_posts": int(fields.get("total_posts", 0) or 0),
        "verified": fields.get("verified", False),
        "private_account": fields.get("private_account", False),
        "scanned_at": _utc_now(),
    }


def _profile_stats_readable(parsed: dict, body_text: str = "") -> bool:
    """True if we parsed a profile page (0 counts are valid — not falsy)."""
    if parsed.get("stats_extracted") or parsed.get("_json_stats"):
        return True
    if any(int(parsed.get(k) or 0) > 0 for k in ("followers", "following", "likes")):
        return True
    # Profile header + counter labels on page (e.g. "0 Followers")
    if parsed.get("display_name") and re.search(
        r"\b(followers|following|likes)\b", body_text, re.I
    ):
        return True
    return False


class ProfileScanner:
    """Fetch TikTok profile stats via Playwright (public profile page)."""

    NAV_TIMEOUT_MS = 15_000
    MAX_RETRIES = 2

    def __init__(self, browser_manager, log_manager=None, account_manager=None):
        self.browser = browser_manager
        self.log_mgr = log_manager
        self.account_mgr = account_manager
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

    def _log_scan(self, account_id: Optional[int], result: dict):
        if not self.log_mgr:
            return
        aid = account_id if account_id is not None else 0
        if result.get("success"):
            if result.get("private_account"):
                self.log_mgr.log(
                    aid,
                    "sync",
                    "WARNING",
                    f"@{result.get('username')}: private account",
                    result,
                )
            else:
                self.log_mgr.log(
                    aid,
                    "sync",
                    "SUCCESS",
                    (
                        f"Profile scanned: {result.get('followers', 0):,} followers, "
                        f"{result.get('likes', 0):,} likes"
                    ),
                    result,
                )
        else:
            self.log_mgr.log(
                aid,
                "sync",
                "ERROR",
                result.get("error") or "Profile scan failed",
                result,
            )

    async def fetch_profile(
        self,
        username: str,
        proxy_url: Optional[str] = None,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        username = (username or "").strip().lstrip("@")
        if not username:
            return _fail("", "Username is required", "not_found")

        await self._rate_limit_wait()

        last_error = "Profile scan failed"
        last_type = "timeout"

        for attempt in range(self.MAX_RETRIES):
            ua = USER_AGENTS[attempt % len(USER_AGENTS)]
            # Use real account browser profile + cookies when syncing a farm account
            ctx_id = (
                account_id
                if account_id is not None and account_id < 900_000_000
                else _scan_context_id(f"{username}_{attempt}")
            )
            try:
                result = await self._scan_once(
                    username, proxy_url, ua, ctx_id, account_id=account_id
                )
                if result.get("success") or result.get("error_type") in (
                    "not_found",
                    "private",
                    "blocked",
                ):
                    self._log_scan(account_id, result)
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

        fail = _fail(username, last_error, last_type)
        self._log_scan(account_id, fail)
        return fail

    async def _scan_once(
        self,
        username: str,
        proxy_url: Optional[str],
        user_agent: str,
        ctx_id: int,
        account_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        url = f"https://www.tiktok.com/@{username}"

        context = await self.browser.create_context(
            ctx_id,
            proxy_url=proxy_url,
            user_agent=user_agent,
            extra_http_headers=BROWSER_HEADERS,
        )
        page = await context.new_page()

        if account_id is not None and account_id < 900_000_000 and self.account_mgr:
            try:
                from src.cookie_manager import CookieManager

                account = self.account_mgr.get_account(account_id)
                if account and account.cookie_data:
                    cookies = CookieManager.parse_cookie_string(account.cookie_data)
                    if cookies:
                        await context.add_cookies(cookies)
                        logger.info(
                            f"Profile scan: injected cookies for account {account_id}"
                        )
            except Exception as e:
                logger.debug(f"Cookie inject for profile scan {account_id}: {e}")

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

            await asyncio.sleep(2)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

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

            if "log in to tiktok" in body_text or (
                "log in" in body_text and "qr code" in body_text
            ):
                return _fail(
                    username,
                    "TikTok session expired — update cookies (sessionid) for this account.",
                    "login_required",
                )

            if (
                "couldn't find this account" in body_text
                or "this page isn't available" in body_text
                or "account has been banned" in body_text
            ):
                hint = (
                    f"Public profile @{username} not found on TikTok. "
                    "Check the username is correct (not only seller UID). "
                )
                if account_id is not None:
                    hint += "If cookies are old, paste fresh cookies and sync again."
                return _fail(username, hint.strip(), "not_found")

            private_flag = self._is_private(body_text)

            try:
                await page.wait_for_selector(
                    "[data-e2e='user-follower-count'], .count-infos, "
                    "h2[data-e2e='user-follower-count'], strong[data-e2e='followers-count']",
                    timeout=12000,
                )
            except PlaywrightTimeout:
                pass

            dom = await self._extract_from_dom(page)
            body = extract_counters_from_body_text(body_text)
            json_stats = await self._extract_json_stats(page)
            fallback = await self._extract_fallback(page, username, {})
            parsed = merge_profile_stats(dom, fallback, body, json_stats, {"private_account": private_flag})

            if not _profile_stats_readable(parsed, body_text):
                return _fail(
                    username,
                    "Could not read follower stats (TikTok layout/proxy). Account may still be fine — try another proxy or refresh cookies.",
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

    async def _parse_count_element(self, el) -> int:
        """Prefer title attribute (full number) over abbreviated visible text."""
        try:
            title = await el.get_attribute("title")
            if title and re.search(r"[\d]", title):
                val = parse_count_text(title)
                if val > 0:
                    return val
            txt = (await el.inner_text()).strip()
            if txt:
                return parse_count_text(txt)
        except Exception:
            pass
        return 0

    async def _read_stat(self, page, selector_csv: str) -> int:
        for sel in selector_csv.split(","):
            sel = sel.strip()
            if not sel:
                continue
            try:
                elements = await page.query_selector_all(sel)
            except Exception:
                elements = []
            for el in elements:
                val = await self._parse_count_element(el)
                if val > 0:
                    return val
        return 0

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
            "following": "[data-e2e='following-count'], [data-e2e='user-following-count']",
            "followers": "[data-e2e='followers-count'], [data-e2e='user-follower-count']",
            "likes": "[data-e2e='likes-count'], [data-e2e='user-likes-count']",
        }
        for key, sel in e2e_map.items():
            val = await self._read_stat(page, sel)
            if val > 0:
                data[key] = val
                data["stats_extracted"] = True

        # Legacy .count-infos — TikTok order: Following | Followers | Likes
        try:
            counts = await page.query_selector_all(".count-infos .count, .count-infos strong")
            texts = []
            for el in counts[:5]:
                t = (await el.inner_text()).strip()
                if t and re.search(r"[\d]", t):
                    texts.append(t)
            if len(texts) >= 3 and not any(data.get(k) for k in ("followers", "following", "likes")):
                data["following"] = parse_count_text(texts[0])
                data["followers"] = parse_count_text(texts[1])
                data["likes"] = parse_count_text(texts[2])
                data["stats_extracted"] = True
        except Exception:
            pass

        # Video count tab
        posts_txt = await self._text(page, "[data-e2e='user-post-count'], [data-e2e='videos-count']")
        if posts_txt:
            data["total_posts"] = parse_count_text(posts_txt)

        return data

    async def _extract_json_stats(self, page) -> Dict[str, Any]:
        """Parse SIGI_STATE / universal data JSON (authoritative follower counts)."""
        data: Dict[str, Any] = {}
        try:
            for script_id in (
                "__UNIVERSAL_DATA_FOR_REHYDRATION__",
                "SIGI_STATE",
            ):
                el = await page.query_selector(f"script#{script_id}")
                if not el:
                    continue
                raw = await el.inner_text()
                if not raw:
                    continue
                blob = json.loads(raw)
                stats = self._stats_from_json_blob(blob)
                if stats:
                    data.update(stats)
                    data["_json_stats"] = True
                    data["stats_extracted"] = True
                    break
        except Exception as e:
            logger.debug(f"embedded JSON extract: {e}")
        return data

    @staticmethod
    def _stats_from_json_blob(obj: Any) -> Dict[str, int]:
        """Walk JSON for followerCount / followingCount / heart / video fields."""
        found: Dict[str, int] = {}

        def walk(node):
            if isinstance(node, dict):
                def _as_int(val: Any) -> Optional[int]:
                    if isinstance(val, (int, float)):
                        return int(val)
                    if isinstance(val, str):
                        digits = re.sub(r"[^\d]", "", val)
                        return int(digits) if digits else None
                    return None

                if "followerCount" in node:
                    v = _as_int(node.get("followerCount"))
                    if v is not None:
                        found["followers"] = v
                if "followingCount" in node:
                    v = _as_int(node.get("followingCount"))
                    if v is not None:
                        found["following"] = v
                # TikTok JSON blobs sometimes use either "heart" or "heartCount" for likes.
                if "heart" in node:
                    v = _as_int(node.get("heart"))
                    if v is not None:
                        found["likes"] = v
                if "heartCount" in node:
                    v = _as_int(node.get("heartCount"))
                    if v is not None:
                        found["likes"] = v
                if "videoCount" in node:
                    v = _as_int(node.get("videoCount"))
                    if v is not None:
                        found["total_posts"] = v
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(obj)
        return found

    @staticmethod
    async def _extract_from_body_counters(body_text: str, partial: Dict[str, Any]) -> Dict[str, Any]:
        """Parse labeled counters from visible page text (EN/VI)."""
        data = dict(partial)
        labeled = extract_counters_from_body_text(body_text)
        data.update(labeled)
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

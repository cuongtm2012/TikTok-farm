# TikTok Farm — Post Engine v4 (tiktok-uploader selectors + flow, async Playwright)

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from src.cookie_manager import CookieManager

logger = logging.getLogger(__name__)

UPLOAD_TIMEOUT_DEFAULT_MS = 200_000


# ---- Exceptions ----


class PostError(Exception):
    """Base post engine error."""


class CookieExpiredError(PostError):
    """Cookies expired — need fresh session."""


class UploadTimeoutError(PostError):
    """Media upload timed out."""


class PostRejectedError(PostError):
    """TikTok rejected the post."""


class ScheduleInvalidError(PostError):
    """Schedule time invalid."""


# ---- Schedule validation (tiktok-uploader) ----


def validate_schedule(dt: datetime) -> tuple:
    """Returns (is_valid, error_message). Uses UTC for comparison."""
    if dt.tzinfo is None:
        now = datetime.utcnow()
        dt_cmp = dt
    else:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        dt_cmp = dt.astimezone(timezone.utc).replace(tzinfo=None)

    if dt_cmp < now + timedelta(minutes=20):
        return False, "Schedule must be at least 20 minutes in the future"
    if dt_cmp > now + timedelta(days=10):
        return False, "Schedule cannot be more than 10 days in advance"
    if dt_cmp.minute % 5 != 0:
        return False, "Schedule minute must be a multiple of 5"
    return True, ""


def parse_schedule_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T")[:19])
    except ValueError:
        return None


def sanitize_caption(text: str) -> str:
    """Remove BMP-out-of-range chars (problematic emojis for TikTok editor)."""
    if not text:
        return ""
    return "".join(c for c in text if ord(c) <= 0xFFFF)


def load_selectors(config_path: str = "config/selectors.yaml") -> Dict[str, Any]:
    path = Path(config_path)
    if not path.is_file():
        path = Path(__file__).resolve().parent.parent / "config" / "selectors.yaml"
    if path.is_file():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _format_caption(caption: str, hashtags: str) -> str:
    caption = sanitize_caption(caption or "")
    if hashtags:
        tags = " ".join(f"#{t.strip().lstrip('#')}" for t in hashtags.split() if t.strip())
        caption = f"{caption}\n\n{tags}".strip() if caption else tags
    return caption


# ---- Post engine ----


class PostEngine:
    """Upload TikTok video/slideshow via Creator Center (Playwright)."""

    def __init__(self, browser_manager, account_manager=None, cookie_manager=None, log_manager=None):
        self.browser = browser_manager
        self.account_mgr = account_manager
        self.cookie_mgr = cookie_manager or CookieManager()
        self.log_mgr = log_manager
        self._login_cache: Dict[int, bool] = {}
        self.selectors = load_selectors()
        self.upload_sel = self.selectors.get("upload", {})
        self.cover_sel = self.selectors.get("cover", {})
        self.schedule_sel = self.selectors.get("schedule", {})
        self.login_sel = self.selectors.get("login_indicators", {})

    def _upload_url(self) -> str:
        return self.upload_sel.get("upload_page") or "https://www.tiktok.com/creator-center/upload?lang=en"

    def _upload_timeout(self) -> int:
        return int(self.upload_sel.get("upload_timeout_ms") or UPLOAD_TIMEOUT_DEFAULT_MS)

    def _log_post(self, account_id: int, level: str, message: str, details: dict = None):
        if self.log_mgr:
            self.log_mgr.log(account_id, "post", level, message, details)

    async def _locator(self, page, xpath: str):
        if not xpath:
            return None
        return page.locator(f"xpath={xpath}").first

    async def _click_xpath(self, page, xpath: str, timeout: int = 5000) -> bool:
        try:
            loc = await self._locator(page, xpath)
            if loc and await loc.count():
                await loc.click(timeout=timeout)
                return True
        except Exception as e:
            logger.debug(f"Click xpath failed ({xpath[:40]}...): {e}")
        return False

    async def _wait_xpath(self, page, xpath: str, timeout: int = 30000) -> bool:
        try:
            await page.wait_for_selector(f"xpath={xpath}", timeout=timeout)
            return True
        except Exception:
            return False

    async def _persist_session(self, account_id: int, page) -> None:
        try:
            cookies = await page.context.cookies()
            if self.account_mgr and cookies:
                self.account_mgr.save_cookies(account_id, cookies)
            await self.browser.save_storage_state(account_id)
        except Exception as e:
            logger.warning(f"[Account {account_id}] Session persist failed: {e}")

    async def _inject_cookies(self, account_id: int, cookie_data: Optional[str]) -> None:
        cookies = self.cookie_mgr.cookies_from_account_data(cookie_data)
        if not cookies:
            return
        if not self.cookie_mgr.validate_session(cookies):
            logger.warning(f"[Account {account_id}] No sessionid in cookies")
        path = self.browser._storage_state_path(account_id)
        self.cookie_mgr.save_to_storage_state(cookies, str(path))

    async def _check_cookie_expired(self, page) -> bool:
        url = page.url.lower()
        if self.login_sel.get("login_url_fragment", "/login") in url:
            return True
        try:
            body = (await page.inner_text("body")).lower()
            if self.login_sel.get("cookie_expired_text", "").lower() in body:
                return True
        except Exception:
            pass
        return False

    async def _ensure_login(
        self,
        page,
        account_id: int,
        username: str = "",
        password: str = "",
        cookie_data: Optional[str] = None,
    ) -> bool:
        if self._login_cache.get(account_id):
            return True

        await self._inject_cookies(account_id, cookie_data)

        try:
            await self.browser.navigate_safe(page, "https://www.tiktok.com/")
            await asyncio.sleep(2)

            if await self._check_cookie_expired(page):
                if cookie_data:
                    cookies = self.cookie_mgr.cookies_from_account_data(cookie_data)
                    if cookies:
                        await page.context.add_cookies(self.cookie_mgr.to_playwright_format(cookies))
                        await page.reload()
                        await asyncio.sleep(2)

            if await self._check_cookie_expired(page):
                raise CookieExpiredError("Session expired — update cookies for this account")

            logged_in = await page.query_selector(
                '[data-e2e="user-avatar"], a[href*="/@"] img, [class*="avatar"]'
            )
            if logged_in:
                self._login_cache[account_id] = True
                await self._persist_session(account_id, page)
                return True

            if cookie_data:
                cookies = self.cookie_mgr.cookies_from_account_data(cookie_data)
                if cookies:
                    await page.context.add_cookies(self.cookie_mgr.to_playwright_format(cookies))
                    await page.reload()
                    await asyncio.sleep(3)
                    if not await self._check_cookie_expired(page):
                        self._login_cache[account_id] = True
                        await self._persist_session(account_id, page)
                        return True

            raise CookieExpiredError("Not logged in — provide valid session cookies")
        except CookieExpiredError:
            raise
        except Exception as e:
            logger.error(f"[Account {account_id}] Login check error: {e}")
            return False

    async def _dismiss_split_window(self, page) -> None:
        xpath = self.upload_sel.get("split_window")
        if xpath:
            await self._click_xpath(page, xpath, timeout=3000)
            await asyncio.sleep(0.5)

    async def _set_caption(self, page, text: str) -> None:
        xpath = self.upload_sel.get("description")
        if xpath:
            loc = await self._locator(page, xpath)
            if loc and await loc.count():
                await loc.click()
                await loc.fill(text)
                return
        fallback = await page.query_selector(
            '[data-e2e="caption-input"], div[contenteditable="true"], textarea'
        )
        if fallback:
            await fallback.click()
            try:
                await fallback.fill(text)
            except Exception:
                await fallback.type(text, delay=15)

    async def _set_interactivity(self, page) -> None:
        for key in ("comment_toggle", "stitch_toggle", "duet_toggle"):
            xpath = self.upload_sel.get(key)
            if not xpath:
                continue
            try:
                loc = await self._locator(page, xpath)
                if loc and await loc.count():
                    checked = await loc.is_checked()
                    if not checked:
                        await loc.check()
            except Exception:
                pass

    async def _set_cover(self, page, cover_path: str) -> None:
        if not cover_path or not Path(cover_path).is_file():
            return
        if not await self._click_xpath(page, self.cover_sel.get("edit_cover_button", ""), 8000):
            return
        await asyncio.sleep(1)
        await self._click_xpath(page, self.cover_sel.get("upload_cover_tab", ""), 5000)
        await asyncio.sleep(0.5)
        inp_xpath = self.cover_sel.get("upload_cover_input")
        if inp_xpath:
            loc = await self._locator(page, inp_xpath)
            if loc:
                await loc.set_input_files(cover_path)
                await asyncio.sleep(2)
        await self._click_xpath(page, self.cover_sel.get("confirm_cover", ""), 8000)
        await asyncio.sleep(1)

    async def _set_schedule(self, page, schedule_dt: datetime) -> None:
        ok, msg = validate_schedule(schedule_dt)
        if not ok:
            raise ScheduleInvalidError(msg)

        sw = self.schedule_sel.get("switch")
        if not sw:
            logger.warning("Schedule switch selector missing — skipping schedule UI")
            return

        if not await self._click_xpath(page, sw, 5000):
            logger.warning("Slideshow may not support schedule — continuing without schedule")
            return

        await asyncio.sleep(1)
        await self._click_xpath(page, self.schedule_sel.get("date_picker", ""), 5000)
        await asyncio.sleep(0.5)

        target_day = schedule_dt.day
        days_xpath = self.schedule_sel.get("calendar_valid_days")
        if days_xpath:
            locators = page.locator(f"xpath={days_xpath}")
            count = await locators.count()
            for i in range(count):
                el = locators.nth(i)
                txt = (await el.inner_text()).strip()
                if txt.isdigit() and int(txt) == target_day:
                    await el.click()
                    break

        await self._click_xpath(page, self.schedule_sel.get("time_picker", ""), 5000)
        hour = schedule_dt.hour % 12 or 12
        minute = (schedule_dt.minute // 5) * 5
        hour_xpath = self.schedule_sel.get("timepicker_hours")
        min_xpath = self.schedule_sel.get("timepicker_minutes")
        if hour_xpath:
            loc = page.locator(f"xpath={hour_xpath}")
            if await loc.count():
                await loc.first.click()
        if min_xpath:
            loc = page.locator(f"xpath={min_xpath}")
            if await loc.count():
                await loc.first.click()
        logger.info(f"Schedule set ~{hour}:{minute:02d} (UI may need manual verify)")

    async def _upload_media(self, page, file_paths: List[str]) -> None:
        if not file_paths:
            raise PostRejectedError("No media files to upload")

        xpath = self.upload_sel.get("file_input")
        timeout = self._upload_timeout()
        uploaded = False

        if xpath:
            loc = await self._locator(page, xpath)
            if loc and await loc.count():
                await loc.set_input_files(file_paths if len(file_paths) > 1 else file_paths[0])
                uploaded = True

        if not uploaded:
            inp = await page.query_selector('input[type="file"]')
            if inp:
                await inp.set_input_files(file_paths if len(file_paths) > 1 else file_paths[0])
                uploaded = True

        if not uploaded:
            raise PostRejectedError("Could not find file upload input")

        finished = self.upload_sel.get("upload_finished")
        if finished:
            if not await self._wait_xpath(page, finished, timeout=timeout):
                raise UploadTimeoutError("Media upload processing timed out")
        else:
            await asyncio.sleep(min(30, timeout // 1000))

    async def _click_post(self, page) -> bool:
        xpath = self.upload_sel.get("post_button")
        if xpath and await self._click_xpath(page, xpath, 10000):
            return True
        for sel in (
            'button:has-text("Post")',
            '[data-e2e="post_video_button"]',
            '[data-e2e="post-button"]',
        ):
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                return True
        return False

    async def _wait_post_confirmation(self, page, timeout: int = 60000) -> bool:
        xpath = self.upload_sel.get("post_confirmation")
        if xpath:
            return await self._wait_xpath(page, xpath, timeout=timeout)
        await asyncio.sleep(10)
        return True

    async def _do_upload(
        self,
        account_id: int,
        file_paths: List[str],
        caption: str = "",
        hashtags: str = "",
        schedule_dt: Optional[datetime] = None,
        cover_path: Optional[str] = None,
        username: str = "",
        password: str = "",
        cookie_data: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = {
            "success": False,
            "post_id": None,
            "post_url": None,
            "tiktok_post_id": None,
            "error": None,
            "posted_at": None,
        }

        self._log_post(account_id, "INFO", "Upload started")
        page = await self.browser.get_page(account_id, proxy_url)
        await self.browser.apply_stealth(page.context)

        await self._ensure_login(page, account_id, username, password, cookie_data)

        upload_url = self._upload_url()
        if not await self.browser.navigate_safe(page, upload_url, timeout=60000):
            raise UploadTimeoutError("Failed to load TikTok upload page")

        await asyncio.sleep(3)
        if await self._check_cookie_expired(page):
            raise CookieExpiredError("Redirected to login on upload page")

        await self._upload_media(page, file_paths)
        await self._dismiss_split_window(page)

        full_caption = _format_caption(caption, hashtags)
        if full_caption:
            await self._set_caption(page, full_caption)
            await asyncio.sleep(1)

        await self._set_interactivity(page)

        if cover_path:
            await self._set_cover(page, cover_path)

        if schedule_dt:
            try:
                await self._set_schedule(page, schedule_dt)
            except ScheduleInvalidError:
                raise
            except Exception as e:
                logger.warning(f"[Account {account_id}] Schedule UI failed: {e}")

        if not await self._click_post(page):
            raise PostRejectedError("Could not find Post button")

        await self._wait_post_confirmation(page)
        result["success"] = True
        result["posted_at"] = datetime.now().isoformat()
        result["tiktok_post_id"] = await self._extract_post_id(page)
        result["post_url"] = page.url if "/video/" in page.url else None
        await self._persist_session(account_id, page)
        self._log_post(
            account_id,
            "SUCCESS",
            "Post published successfully",
            {"post_url": result.get("post_url"), "tiktok_post_id": result.get("tiktok_post_id")},
        )
        return result

    async def upload_video(
        self,
        account_id: int,
        video_path: str,
        caption: str = "",
        hashtags: str = "",
        affiliate_link: str = "",
        username: str = "",
        password: str = "",
        cookie_data: Optional[str] = None,
        proxy_url: Optional[str] = None,
        schedule_dt: Optional[datetime] = None,
        cover_path: Optional[str] = None,
        max_retries: int = 2,
    ) -> Dict:
        """Upload a single video (mp4)."""
        video_file = Path(video_path)
        if not video_file.is_file():
            return {"success": False, "error": f"Video not found: {video_path}", "media_type": "video"}

        if schedule_dt:
            ok, msg = validate_schedule(schedule_dt)
            if not ok:
                return {"success": False, "error": msg, "error_type": "schedule_invalid"}

        return await self._upload_with_retry(
            account_id=account_id,
            file_paths=[str(video_file.resolve())],
            caption=caption,
            hashtags=hashtags,
            affiliate_link=affiliate_link,
            username=username,
            password=password,
            cookie_data=cookie_data,
            proxy_url=proxy_url,
            schedule_dt=schedule_dt,
            cover_path=cover_path,
            max_retries=max_retries,
            media_type="video",
        )

    async def upload_slideshow(
        self,
        account_id: int,
        images_dir: str,
        caption: str = "",
        hashtags: str = "",
        affiliate_link: str = "",
        username: str = "",
        password: str = "",
        cookie_data: Optional[str] = None,
        proxy_url: Optional[str] = None,
        schedule_dt: Optional[datetime] = None,
        cover_path: Optional[str] = None,
        max_retries: int = 2,
    ) -> Dict:
        """Upload multiple images as a slideshow."""
        img_dir = Path(images_dir)
        patterns = ["*.png", "*.jpg", "*.jpeg", "slide_*.png"]
        image_files: List[Path] = []
        for pat in patterns:
            image_files.extend(img_dir.glob(pat))
        image_files = sorted(set(image_files))[:10]
        if not image_files:
            return {"success": False, "error": f"No images found in {images_dir}"}

        file_paths = [str(f.resolve()) for f in image_files]
        if schedule_dt:
            ok, msg = validate_schedule(schedule_dt)
            if not ok:
                return {"success": False, "error": msg, "error_type": "schedule_invalid"}

        return await self._upload_with_retry(
            account_id=account_id,
            file_paths=file_paths,
            caption=caption,
            hashtags=hashtags,
            affiliate_link=affiliate_link,
            username=username,
            password=password,
            cookie_data=cookie_data,
            proxy_url=proxy_url,
            schedule_dt=schedule_dt,
            cover_path=cover_path or (str(image_files[0]) if image_files else None),
            max_retries=max_retries,
            media_type="slideshow",
        )

    async def _upload_with_retry(
        self,
        account_id: int,
        file_paths: List[str],
        caption: str,
        hashtags: str,
        affiliate_link: str,
        username: str,
        password: str,
        cookie_data: Optional[str],
        proxy_url: Optional[str],
        schedule_dt: Optional[datetime],
        cover_path: Optional[str],
        max_retries: int,
        media_type: str,
    ) -> Dict:
        logger.info(f"[Account {account_id}] Starting {media_type} upload ({len(file_paths)} file(s))")
        last_error = "Upload failed"

        for attempt in range(max_retries + 1):
            try:
                result = await self._do_upload(
                    account_id=account_id,
                    file_paths=file_paths,
                    caption=caption,
                    hashtags=hashtags,
                    schedule_dt=schedule_dt,
                    cover_path=cover_path,
                    username=username,
                    password=password,
                    cookie_data=cookie_data,
                    proxy_url=proxy_url,
                )
                result["media_type"] = media_type
                if affiliate_link:
                    result["affiliate_link"] = affiliate_link
                return result
            except CookieExpiredError as e:
                self.clear_login_cache(account_id)
                self._log_post(account_id, "ERROR", str(e), {"error_type": "cookie_expired"})
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "cookie_expired",
                    "media_type": media_type,
                }
            except ScheduleInvalidError as e:
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "schedule_invalid",
                    "media_type": media_type,
                }
            except UploadTimeoutError as e:
                last_error = str(e)
                if attempt < max_retries:
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
            except PostRejectedError as e:
                return {
                    "success": False,
                    "error": str(e),
                    "error_type": "rejected",
                    "media_type": media_type,
                }
            except Exception as e:
                last_error = str(e)
                logger.error(f"[Account {account_id}] Upload attempt {attempt + 1}: {e}", exc_info=True)
                if attempt < max_retries:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
            finally:
                try:
                    await self.browser.close_context(account_id)
                except Exception:
                    pass

        self._log_post(
            account_id,
            "ERROR",
            f"Upload failed: {last_error}",
            {"error_type": "timeout", "media_type": media_type},
        )
        return {
            "success": False,
            "error": f"Upload timeout after retries: {last_error}",
            "error_type": "timeout",
            "media_type": media_type,
        }

    async def get_post_stats(self, account_id: int, post_url: str) -> Optional[Dict]:
        try:
            page = await self.browser.get_page(account_id)
            if not await self.browser.navigate_safe(page, post_url):
                return None
            await asyncio.sleep(5)
            stats = {}
            for key, sel in (
                ("views", '[data-e2e="video-views"]'),
                ("likes", '[data-e2e="like-count"]'),
                ("comments", '[data-e2e="comment-count"]'),
                ("shares", '[data-e2e="share-count"]'),
            ):
                el = await page.query_selector(sel)
                if el:
                    stats[key] = await el.inner_text()
            return stats
        except Exception as e:
            logger.error(f"[Account {account_id}] get_post_stats: {e}")
            return None

    async def _extract_post_id(self, page) -> Optional[str]:
        try:
            url = page.url
            if "/video/" in url:
                return url.split("/video/")[-1].split("?")[0]
        except Exception:
            pass
        return None

    def clear_login_cache(self, account_id: Optional[int] = None):
        if account_id is not None:
            self._login_cache.pop(account_id, None)
        else:
            self._login_cache.clear()

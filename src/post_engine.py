# TikTok Farm - Post Engine Module
# Uploads slideshow posts to TikTok via Camoufox/Playwright

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class PostEngine:
    """Handles TikTok post upload via Playwright automation.

    Handles login session management, slideshow upload, caption/hashtag entry,
    and affiliate link tagging.
    """

    # TikTok upload URL
    UPLOAD_URL = "https://www.tiktok.com/upload"
    LOGIN_URL = "https://www.tiktok.com/login"

    def __init__(self, browser_manager, account_manager=None):
        self.browser = browser_manager
        self.account_mgr = account_manager
        self._login_cache: Dict[int, bool] = {}  # account_id -> logged_in

    async def _persist_session(self, account_id: int, page) -> None:
        """Save cookies to DB and browser storage_state file."""
        try:
            cookies = await page.context.cookies()
            if self.account_mgr and cookies:
                self.account_mgr.save_cookies(account_id, cookies)
            await self.browser.save_storage_state(account_id)
        except Exception as e:
            logger.warning(f"[Account {account_id}] Session persist failed: {e}")

    async def _ensure_login(
        self,
        page,
        account_id: int,
        username: str = "",
        password: str = "",
        cookie_data: Optional[str] = None,
    ) -> bool:
        """Ensure the account is logged in. Uses cookies if available, otherwise tries credentials."""
        # Check if we already logged in this session
        if account_id in self._login_cache and self._login_cache[account_id]:
            return True

        try:
            # First try: navigate and see if already logged in
            await self.browser.navigate_safe(page, "https://www.tiktok.com/")
            await asyncio.sleep(3)

            # Check if we're logged in by looking for user avatar or profile element
            logged_in = await page.query_selector(
                'a[href*="/@"][class*="avatar"], '
                'a[href*="/@"] img[alt], '
                '[data-e2e="user-avatar"], [data-e2e="profile-icon"], '
                '[class*="avatar"]'
            )

            if logged_in:
                logger.info(f"[Account {account_id}] Already logged in")
                self._login_cache[account_id] = True
                await self._persist_session(account_id, page)
                return True

            # Second try: use cookies if available
            if cookie_data:
                try:
                    cookies = json.loads(cookie_data)
                    if isinstance(cookies, list):
                        await page.context.add_cookies(cookies)
                        await page.reload()
                        await asyncio.sleep(3)

                        logged_in = await page.query_selector(
                            '[data-e2e="user-avatar"], [class*="avatar"]'
                        )
                        if logged_in:
                            logger.info(f"[Account {account_id}] Logged in via cookies")
                            self._login_cache[account_id] = True
                            await self._persist_session(account_id, page)
                            return True
                except Exception as e:
                    logger.warning(f"[Account {account_id}] Cookie login failed: {e}")

            # Third try: manual login (requires credentials)
            if username and password:
                logger.info(f"[Account {account_id}] Attempting login with credentials")
                await self.browser.navigate_safe(page, self.LOGIN_URL)
                await asyncio.sleep(3)

                # Click "Use phone / email / username" option
                use_username_btn = await page.query_selector(
                    'div:has-text("Use phone / email / username"), '
                    'a:has-text("Log in with"), [data-e2e="login-with-username"]'
                )
                if use_username_btn:
                    await use_username_btn.click()
                    await asyncio.sleep(2)

                # Fill in username
                username_input = await page.query_selector(
                    'input[name="username"], input[placeholder*="username"], '
                    'input[placeholder*="Username"], input[autocomplete="username"]'
                )
                if username_input:
                    await username_input.fill(username)
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                # Fill in password
                password_input = await page.query_selector(
                    'input[type="password"], input[name="password"]'
                )
                if password_input:
                    await password_input.fill(password)
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                # Click login button
                login_btn = await page.query_selector(
                    'button:has-text("Log in"), button:has-text("Login"), '
                    '[type="submit"]'
                )
                if login_btn:
                    await login_btn.click()
                    await asyncio.sleep(5)

                # Check if login succeeded
                logged_in = await page.query_selector(
                    '[data-e2e="user-avatar"], [class*="avatar"]'
                )
                if logged_in:
                    logger.info(f"[Account {account_id}] Login successful")
                    self._login_cache[account_id] = True
                    await self._persist_session(account_id, page)
                    return True
                else:
                    logger.warning(f"[Account {account_id}] Login failed - check credentials")
                    return False

            logger.warning(f"[Account {account_id}] No login method available")
            return False

        except Exception as e:
            logger.error(f"[Account {account_id}] Login error: {e}", exc_info=True)
            return False

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
    ) -> Dict:
        """Upload a slideshow post to TikTok.

        Args:
            account_id: Account ID
            images_dir: Directory containing slide images (from content pipeline)
            caption: Post caption text
            hashtags: Space-separated hashtags (without #)
            affiliate_link: TikTok Shop affiliate link
            username: Account username (for login)
            password: Account password (for login)
            cookie_data: JSON cookie data (optional)
            proxy_url: Proxy URL

        Returns:
            Dict with post results
        """
        logger.info(f"[Account {account_id}] Starting slideshow upload from {images_dir}")

        result = {
            "success": False,
            "post_id": None,
            "error": None,
            "posted_at": None,
        }

        try:
            page = await self.browser.get_page(account_id, proxy_url)

            # Ensure login
            logged_in = await self._ensure_login(page, account_id, username, password, cookie_data)
            if not logged_in:
                result["error"] = "Login failed"
                logger.error(f"[Account {account_id}] Cannot upload: {result['error']}")
                return result

            # Navigate to upload page
            await self.browser.navigate_safe(page, self.UPLOAD_URL)
            await asyncio.sleep(5)

            # Check if upload page loaded
            upload_ready = await page.query_selector(
                '[data-e2e="upload-button"], [class*="upload"], input[type="file"]'
            )
            if not upload_ready:
                logger.warning(f"[Account {account_id}] Upload page may not have loaded fully")
                await asyncio.sleep(3)

            # Click "Select files" or upload button
            # TikTok's upload uses a hidden file input
            file_input = await page.query_selector('input[type="file"]')
            if not file_input:
                # Try to click the upload button to reveal the file dialog
                upload_btn = await page.query_selector(
                    'button:has-text("Select"), button:has-text("Upload"), '
                    '[data-e2e="upload-button"]'
                )
                if upload_btn:
                    await upload_btn.click()
                    await asyncio.sleep(2)
                    file_input = await page.query_selector('input[type="file"]')

            if not file_input:
                result["error"] = "Could not find file upload input"
                logger.error(f"[Account {account_id}] {result['error']}")
                return result

            # Get image files
            img_dir = Path(images_dir)
            image_files = sorted(img_dir.glob("slide_*.png")) + sorted(img_dir.glob("*.jpg"))
            if not image_files:
                result["error"] = f"No images found in {images_dir}"
                logger.error(f"[Account {account_id}] {result['error']}")
                return result

            # Upload files (limit to first few for slideshow)
            upload_files = image_files[:10]  # TikTok allows up to 10 slides
            file_paths = [str(f) for f in upload_files]

            # Set the file input value
            # For multiple files, we need to set them one by one
            await file_input.set_input_files(file_paths[0])
            await asyncio.sleep(2)

            if len(file_paths) > 1:
                # Add remaining files
                for fp in file_paths[1:]:
                    try:
                        add_btn = await page.query_selector(
                            'button:has-text("Add more"), [data-e2e="add-more"]'
                        )
                        if add_btn:
                            await add_btn.click()
                            await asyncio.sleep(1)
                            file_input = await page.query_selector('input[type="file"]')
                            if file_input:
                                await file_input.set_input_files(fp)
                                await asyncio.sleep(1)
                    except Exception as e:
                        logger.warning(f"[Account {account_id}] Failed to add file {fp}: {e}")

            logger.info(f"[Account {account_id}] Uploaded {len(file_paths)} images")
            await asyncio.sleep(3)

            # Wait for upload processing
            await asyncio.sleep(5)

            # Enter caption
            caption_input = await page.query_selector(
                '[data-e2e="caption-input"], textarea, '
                '[contenteditable="true"], [placeholder*="caption"]'
            )
            if caption_input:
                full_caption = caption
                if hashtags:
                    tag_str = " ".join([f"#{t.strip()}" for t in hashtags.split()])
                    full_caption = f"{caption}\n\n{tag_str}" if caption else tag_str

                await caption_input.click()
                await asyncio.sleep(0.5)
                await caption_input.fill("")
                await asyncio.sleep(0.3)

                # Type caption like a human
                for char in full_caption:
                    await caption_input.type(char, delay=random.uniform(10, 50) / 1000)
                logger.info(f"[Account {account_id}] Caption entered ({len(full_caption)} chars)")

            await asyncio.sleep(2)

            # Enter affiliate link if provided
            if affiliate_link:
                try:
                    # Look for affiliate link input
                    affiliate_input = await page.query_selector(
                        'input[placeholder*="link"], input[placeholder*="Link"], '
                        '[data-e2e="affiliate-input"]'
                    )
                    if affiliate_input:
                        await affiliate_input.fill(affiliate_link)
                        logger.info(f"[Account {account_id}] Affiliate link entered")
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"[Account {account_id}] Failed to enter affiliate link: {e}")

            # Post the video
            post_btn = await page.query_selector(
                'button:has-text("Post"), button:has-text("Upload"), '
                '[data-e2e="post-button"], button:has-text("Submit")'
            )

            if post_btn:
                await post_btn.click()
                logger.info(f"[Account {account_id}] Post button clicked, waiting for upload...")

                # Wait for upload to complete (may take a while)
                await asyncio.sleep(10)

                # Check for success indicators
                success_elem = await page.query_selector(
                    '[data-e2e="upload-success"], [class*="success"], '
                    'text=Your video is being posted'
                )
                if success_elem:
                    logger.info(f"[Account {account_id}] Upload appears successful")

                result["success"] = True
                result["posted_at"] = datetime.now().isoformat()
                result["tiktok_post_id"] = await self._extract_post_id(page)
                await self._persist_session(account_id, page)
                logger.info(f"[Account {account_id}] Slideshow posted successfully!")
            else:
                result["error"] = "Could not find Post button"
                logger.error(f"[Account {account_id}] {result['error']}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[Account {account_id}] Upload error: {e}", exc_info=True)

        # Cleanup
        try:
            await self.browser.close_context(account_id)
        except Exception as e:
            logger.warning(f"[Account {account_id}] Cleanup error: {e}")

        return result

    async def get_post_stats(self, account_id: int, post_url: str) -> Optional[Dict]:
        """Get stats for an existing post (views, likes, comments, shares)."""
        try:
            page = await self.browser.get_page(account_id)
            success = await self.browser.navigate_safe(page, post_url)
            if not success:
                return None

            await asyncio.sleep(5)

            # Try to extract stats (selectors may vary)
            stats = {}

            views_elem = await page.query_selector('[data-e2e="video-views"], [class*="view-count"]')
            if views_elem:
                stats["views"] = await views_elem.inner_text()

            likes_elem = await page.query_selector('[data-e2e="like-count"], [class*="like-count"]')
            if likes_elem:
                stats["likes"] = await likes_elem.inner_text()

            comments_elem = await page.query_selector('[data-e2e="comment-count"], [class*="comment-count"]')
            if comments_elem:
                stats["comments"] = await comments_elem.inner_text()

            shares_elem = await page.query_selector('[data-e2e="share-count"], [class*="share-count"]')
            if shares_elem:
                stats["shares"] = await shares_elem.inner_text()

            logger.info(f"[Account {account_id}] Post stats: {stats}")
            return stats

        except Exception as e:
            logger.error(f"[Account {account_id}] Failed to get post stats: {e}")
            return None

    async def _extract_post_id(self, page) -> Optional[str]:
        """Try to read TikTok post id from URL after upload."""
        try:
            url = page.url
            if "/video/" in url:
                return url.split("/video/")[-1].split("?")[0]
        except Exception:
            pass
        return None

    def clear_login_cache(self, account_id: Optional[int] = None):
        """Clear cached login state for an account (or all accounts)."""
        if account_id:
            self._login_cache.pop(account_id, None)
        else:
            self._login_cache.clear()

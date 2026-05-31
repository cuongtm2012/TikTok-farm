# TikTok Farm - Farm Behavior Engine
# Scrolls, likes, comments, follows, and watches videos like a real human

import asyncio
import logging
import random
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Default comment pools
DEFAULT_COMMENTS = [
    "Nice content! 🔥",
    "Great video! 👏",
    "Love this! ❤️",
    "So underrated!",
    "Keep it up! 💪",
    "This is amazing!",
    "Saved for later 📌",
    "Wow, just wow!",
    "Underrated content!",
    "My new favorite creator",
]

DEFAULT_HASHTAGS = [
    "foryou", "fyp", "viral", "trending", "explore",
]


class FarmEngine:
    """Human-like TikTok behavior simulation using Playwright.

    Generates natural activity patterns: scroll, like, comment, follow, watch.
    Each session mimics real user behavior with random timing and actions.
    """

    def __init__(self, browser_manager, account_manager=None):
        self.browser = browser_manager
        self.account_mgr = account_manager
        self._running = False

    async def _random_delay(self, min_s: float = 0.5, max_s: float = 3.0):
        """Sleep for a random duration to simulate human behavior."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _random_scroll_delay(self):
        """Delay between scrolls (human-like pacing)."""
        await self._random_delay(1.0, 4.0)

    async def _random_action_delay(self):
        """Delay between actions (like, comment, etc.)."""
        await self._random_delay(2.0, 6.0)

    async def scroll_feed(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        duration_minutes: int = 10,
        target_url: str = "https://www.tiktok.com/foryou",
    ) -> dict:
        """Scroll the For You feed for a set duration.

        Mimics real behavior: scroll, pause, watch short clips, scroll again.
        """
        logger.info(f"[Account {account_id}] Starting scroll feed for {duration_minutes} min")

        stats = {
            "videos_viewed": 0,
            "scrolls": 0,
            "duration_seconds": duration_minutes * 60,
            "completed": False,
        }

        try:
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, target_url)
            if not success:
                logger.warning(f"[Account {account_id}] Failed to load TikTok feed")
                return stats

            # Wait for feed to load
            await asyncio.sleep(3)

            start_time = asyncio.get_event_loop().time()
            end_time = start_time + (duration_minutes * 60)

            scroll_count = 0
            while asyncio.get_event_loop().time() < end_time:
                # Watch current video for 5-30 seconds
                watch_time = random.uniform(5, 30)
                await asyncio.sleep(watch_time)

                # Randomly pause on a video longer
                if random.random() < 0.2:
                    logger.debug(f"[Account {account_id}] Pausing to watch video longer")
                    await asyncio.sleep(random.uniform(10, 45))

                # Scroll down
                try:
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    scroll_count += 1
                    stats["videos_viewed"] += 1
                    logger.debug(f"[Account {account_id}] Scrolled ({scroll_count})")
                except Exception as e:
                    logger.warning(f"[Account {account_id}] Scroll failed: {e}")

                await self._random_scroll_delay()

                # Occasionally scroll up a bit (like real users)
                if random.random() < 0.15:
                    try:
                        await page.evaluate("window.scrollBy(0, -window.innerHeight * 0.3)")
                        logger.debug(f"[Account {account_id}] Scroll up (look back)")
                    except Exception:
                        pass

            stats["scrolls"] = scroll_count
            stats["completed"] = True
            elapsed = int(asyncio.get_event_loop().time() - start_time)
            stats["duration_seconds"] = elapsed
            logger.info(f"[Account {account_id}] Scroll feed complete: {scroll_count} scrolls, {stats['videos_viewed']} videos")

        except Exception as e:
            logger.error(f"[Account {account_id}] Scroll feed error: {e}", exc_info=True)

        return stats

    async def like_videos(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        count: int = 5,
        topic: str = "",
        target_url: str = "https://www.tiktok.com/foryou",
    ) -> dict:
        """Like a specified number of videos on the feed.

        Optionally filter by topic hashtag search.
        """
        logger.info(f"[Account {account_id}] Liking {count} videos")

        stats = {"liked": 0, "failed": 0, "completed": False}

        try:
            page = await self.browser.get_page(account_id, proxy_url)

            # Navigate to topic search if provided
            if topic:
                target_url = f"https://www.tiktok.com/tag/{topic}"

            success = await self.browser.navigate_safe(page, target_url)
            if not success:
                return stats

            await asyncio.sleep(3)

            liked = 0
            attempts = 0
            max_attempts = count * 3  # Don't try forever

            while liked < count and attempts < max_attempts:
                attempts += 1

                try:
                    # Find like buttons - TikTok uses aria-label based like buttons (2026)
                    like_buttons = await page.query_selector_all(
                        'button[aria-label*="Like video"], '
                        '[data-e2e="like-icon"]'
                    )

                    if not like_buttons:
                        logger.debug(f"[Account {account_id}] No like buttons found, scrolling")
                        await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        await self._random_scroll_delay()
                        continue

                    # Like the first available video
                    btn = like_buttons[0]
                    is_liked = await page.evaluate(
                        "(el) => el.classList.contains('liked') || el.getAttribute('aria-pressed') === 'true' || "
                        "el.getAttribute('data-is-liked') === 'true'",
                        btn,
                    )

                    if not is_liked:
                        await btn.click()
                        liked += 1
                        stats["liked"] = liked
                        logger.debug(f"[Account {account_id}] Liked video ({liked}/{count})")
                        await self._random_action_delay()

                    # Scroll to next video
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await self._random_scroll_delay()

                except Exception as e:
                    logger.warning(f"[Account {account_id}] Like attempt failed: {e}")
                    stats["failed"] += 1
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")

            stats["completed"] = True
            logger.info(f"[Account {account_id}] Liked {liked} videos")

        except Exception as e:
            logger.error(f"[Account {account_id}] Like videos error: {e}", exc_info=True)

        return stats

    async def comment_random(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        count: int = 2,
        comment_pool: Optional[List[str]] = None,
        target_url: str = "https://www.tiktok.com/foryou",
    ) -> dict:
        """Leave comments on random videos.

        Uses a pool of comments to avoid repetitive text.
        """
        pool = comment_pool or DEFAULT_COMMENTS
        logger.info(f"[Account {account_id}] Commenting {count} times")

        stats = {"commented": 0, "failed": 0, "completed": False}

        try:
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, target_url)
            if not success:
                return stats

            await asyncio.sleep(3)

            commented = 0
            attempts = 0
            max_attempts = count * 5

            while commented < count and attempts < max_attempts:
                attempts += 1

                try:
                    # Click on comment section of a video
                    # TikTok comments open in a modal/side panel (2026: aria-label based)
                    comment_triggers = await page.query_selector_all(
                        'button[aria-label*="comments"], '
                        'button[aria-label*="Comments"], '
                        '[data-e2e="comment-icon"]'
                    )

                    if not comment_triggers:
                        logger.debug(f"[Account {account_id}] No comment triggers, scrolling")
                        await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        await self._random_scroll_delay()
                        continue

                    # Click on a comment trigger
                    trigger = comment_triggers[0]
                    await trigger.click()
                    await asyncio.sleep(random.uniform(2, 4))

                    # Find comment input
                    comment_input = await page.query_selector(
                        'textarea, [contenteditable="true"], '
                        '[data-e2e="comment-input"], input[placeholder*="comment"]'
                    )

                    if comment_input:
                        # Pick random comment from pool
                        comment_text = random.choice(pool)

                        # Type slowly like a human
                        await comment_input.click()
                        await asyncio.sleep(0.5)
                        await comment_input.fill("")
                        await asyncio.sleep(0.3)
                        for char in comment_text:
                            await comment_input.type(char, delay=random.uniform(30, 100) / 1000)

                        await asyncio.sleep(random.uniform(1, 3))

                        # Find and click post/send button
                        post_btn = await page.query_selector(
                            '[data-e2e="comment-post"], button:has-text("Post"), '
                            'button:has-text("Send"), [type="submit"]'
                        )
                        if post_btn:
                            await post_btn.click()
                            commented += 1
                            stats["commented"] = commented
                            logger.debug(f"[Account {account_id}] Commented ({commented}/{count})")

                    # Close comment panel
                    close_btn = await page.query_selector(
                        '[data-e2e="close-comment"], [aria-label="Close"], button:has-text("Close")'
                    )
                    if close_btn:
                        await close_btn.click()
                    else:
                        # Press Escape to close
                        await page.keyboard.press("Escape")

                    await self._random_action_delay()

                except Exception as e:
                    logger.warning(f"[Account {account_id}] Comment attempt failed: {e}")
                    stats["failed"] += 1

            stats["completed"] = True
            logger.info(f"[Account {account_id}] Commented {commented} times")

        except Exception as e:
            logger.error(f"[Account {account_id}] Comment error: {e}", exc_info=True)

        return stats

    async def follow_accounts(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        count: int = 3,
        max_follows_per_session: int = 5,
        target_url: str = "https://www.tiktok.com/foryou",
    ) -> dict:
        """Follow a small number of accounts to build natural network.

        Uses a low follow rate to avoid triggering spam detection.
        """
        effective_count = min(count, max_follows_per_session)
        logger.info(f"[Account {account_id}] Following {effective_count} accounts")

        stats = {"followed": 0, "failed": 0, "completed": False}

        try:
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, target_url)
            if not success:
                return stats

            await asyncio.sleep(3)

            followed = 0
            attempts = 0
            max_attempts = effective_count * 5

            while followed < effective_count and attempts < max_attempts:
                attempts += 1

                try:
                    # Find follow buttons on the feed
                    # TikTok 2026: button with "Follow" text, or in profile section
                    follow_buttons = await page.query_selector_all(
                        'button[aria-label*="Follow"], '
                        'button:has-text("Follow"):not(:has-text("Following")), '
                        '[data-e2e="follow-button"]'
                    )

                    if not follow_buttons:
                        logger.debug(f"[Account {account_id}] No follow buttons, scrolling")
                        await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        await self._random_scroll_delay()
                        continue

                    btn = follow_buttons[0]
                    btn_text = await btn.inner_text()

                    # Only click if it says "Follow" (not "Following")
                    if "follow" in btn_text.lower() and "following" not in btn_text.lower():
                        await btn.click()
                        followed += 1
                        stats["followed"] = followed
                        logger.debug(f"[Account {account_id}] Followed ({followed}/{effective_count})")

                        # Random delay between follows
                        await self._random_action_delay()

                    # Scroll to find more accounts
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await self._random_scroll_delay()

                except Exception as e:
                    logger.warning(f"[Account {account_id}] Follow attempt failed: {e}")
                    stats["failed"] += 1

            stats["completed"] = True
            logger.info(f"[Account {account_id}] Followed {followed} accounts")

        except Exception as e:
            logger.error(f"[Account {account_id}] Follow error: {e}", exc_info=True)

        return stats

    async def watch_video_full(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        min_seconds: int = 15,
        max_seconds: int = 60,
        count: int = 5,
        target_url: str = "https://www.tiktok.com/foryou",
    ) -> dict:
        """Watch videos fully (or partially) to simulate genuine consumption."""
        logger.info(f"[Account {account_id}] Watching {count} videos ({min_seconds}-{max_seconds}s each)")

        stats = {"watched": 0, "total_duration": 0, "completed": False}

        try:
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, target_url)
            if not success:
                return stats

            await asyncio.sleep(3)

            watched = 0
            total_time = 0

            for _ in range(count):
                try:
                    # Watch current video
                    watch_duration = random.randint(min_seconds, max_seconds)

                    # Sometimes pause and rewatch parts
                    if random.random() < 0.3:
                        logger.debug(f"[Account {account_id}] Pausing mid-video")
                        await asyncio.sleep(watch_duration * 0.3)
                        # Simulate pause by not scrolling
                        await asyncio.sleep(watch_duration * 0.7)
                    else:
                        await asyncio.sleep(watch_duration)

                    total_time += watch_duration

                    # React occasionally during watch
                    if random.random() < 0.2:
                        logger.debug(f"[Account {account_id}] Tapping screen during video")
                        try:
                            # Simulate tap/interaction on video
                            video_element = await page.query_selector("video")
                            if video_element:
                                box = await video_element.bounding_box()
                                if box:
                                    x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
                                    y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
                                    await page.mouse.click(x, y)
                        except Exception:
                            pass

                    # Scroll to next
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    watched += 1
                    stats["watched"] = watched
                    stats["total_duration"] = total_time
                    logger.debug(f"[Account {account_id}] Watched video {watched} ({watch_duration}s)")

                    await self._random_scroll_delay()

                except Exception as e:
                    logger.warning(f"[Account {account_id}] Watch error: {e}")
                    await self._random_scroll_delay()

            stats["completed"] = True
            logger.info(f"[Account {account_id}] Watched {watched} videos ({total_time}s total)")

        except Exception as e:
            logger.error(f"[Account {account_id}] Watch error: {e}", exc_info=True)

        return stats

    async def run_farm_session(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        duration_minutes: int = 15,
        actions: Optional[dict] = None,
    ) -> dict:
        """Run a complete farm session combining multiple behaviors.

        Args:
            account_id: TikTok account ID
            proxy_url: Proxy URL for the account
            duration_minutes: Total session duration
            actions: Dict specifying which actions to perform.
                Example: {"scroll": True, "like": 3, "comment": 1, "follow": 1, "watch": 3}

        Returns:
            Session stats dict
        """
        if actions is None:
            actions = {
                "scroll": True,
                "like": 3,
                "comment": 1,
                "follow": 1,
                "watch": 3,
            }

        logger.info(f"[Account {account_id}] Starting farm session ({duration_minutes} min, actions: {actions})")
        self._running = True

        session_stats = {
            "account_id": account_id,
            "started_at": datetime.now().isoformat(),
            "duration_minutes": duration_minutes,
            "actions": {},
        }

        try:
            # Phase 1: Scroll feed (always)
            if actions.get("scroll", True):
                scroll_duration = max(3, duration_minutes // 3)
                scroll_stats = await self.scroll_feed(
                    account_id, proxy_url, duration_minutes=scroll_duration
                )
                session_stats["actions"]["scroll"] = scroll_stats

            # Phase 2: Watch videos
            watch_count = actions.get("watch", 3)
            if watch_count > 0:
                watch_stats = await self.watch_video_full(
                    account_id, proxy_url, count=watch_count
                )
                session_stats["actions"]["watch"] = watch_stats

            # Remaining time distribution
            remaining = max(2, duration_minutes - scroll_duration)

            # Phase 3: Like some videos
            like_count = actions.get("like", 3)
            if like_count > 0:
                like_stats = await self.like_videos(
                    account_id, proxy_url, count=like_count
                )
                session_stats["actions"]["like"] = like_stats

            # Phase 4: Follow a few accounts (low rate)
            follow_count = actions.get("follow", 1)
            if follow_count > 0:
                follow_stats = await self.follow_accounts(
                    account_id, proxy_url, count=follow_count
                )
                session_stats["actions"]["follow"] = follow_stats

            # Phase 5: Leave some comments
            comment_count = actions.get("comment", 1)
            if comment_count > 0:
                comment_stats = await self.comment_random(
                    account_id, proxy_url, count=comment_count
                )
                session_stats["actions"]["comment"] = comment_stats

            # Final scroll
            final_scroll = await self.scroll_feed(
                account_id, proxy_url, duration_minutes=2
            )
            session_stats["actions"]["final_scroll"] = final_scroll

            session_stats["completed"] = True
            logger.info(f"[Account {account_id}] Farm session completed successfully")

        except Exception as e:
            logger.error(f"[Account {account_id}] Farm session error: {e}", exc_info=True)
            session_stats["completed"] = False
            session_stats["error"] = str(e)
        finally:
            self._running = False
            # Save cookies to DB before cleanup
            try:
                if self.account_mgr and account_id in self.browser._pages:
                    page = self.browser._pages.get(account_id)
                    if page and not page.is_closed():
                        cookies = await page.context.cookies()
                        if cookies:
                            self.account_mgr.save_cookies(account_id, cookies)
                            if hasattr(self.browser, "write_storage_state_from_cookies"):
                                self.browser.write_storage_state_from_cookies(
                                    account_id, cookies
                                )
                            logger.info(f"[Account {account_id}] Saved {len(cookies)} cookies to DB")
            except Exception as e:
                logger.warning(f"[Account {account_id}] Save cookies error: {e}")
            # Clean up browser context
            try:
                await self.browser.close_context(account_id)
            except Exception as e:
                logger.warning(f"[Account {account_id}] Cleanup error: {e}")

        return session_stats

    @property
    def is_running(self) -> bool:
        return self._running

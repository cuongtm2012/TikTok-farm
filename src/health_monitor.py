# TikTok Farm - Health Monitor Module
# Periodically checks account health and sends alerts

import asyncio
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Monitors TikTok account health by performing periodic checks.

    Checks performed:
    - Account login status (can we log in?)
    - Shadowban detection (check view counts on recent posts)
    - Rate limit detection (check error responses)
    - Alerts via Telegram for anomalies
    """

    def __init__(
        self,
        account_manager,
        browser_manager,
        telegram_alert=None,
        settings: dict = None,
    ):
        self.account_mgr = account_manager
        self.browser = browser_manager
        self.telegram = telegram_alert
        self.settings = settings or {}

        health_config = settings.get("health_check", {}) if settings else {}
        self.alert_on_shadowban = health_config.get("alert_on_shadowban", True)
        self.alert_on_rate_limit = health_config.get("alert_on_rate_limit", True)

    async def check_account_login(self, account_id: int, proxy_url: Optional[str] = None) -> Dict:
        """Check if an account can log in successfully."""
        result = {
            "account_id": account_id,
            "logged_in": False,
            "error": None,
        }

        try:
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, "https://www.tiktok.com/")
            if not success:
                result["error"] = "Failed to load TikTok"
                return result

            await asyncio.sleep(3)

            # Check for login indicators
            logged_in_elem = await page.query_selector(
                '[data-e2e="user-avatar"], [class*="avatar"], '
                '[data-testid="user-avatar"], [data-e2e="profile-icon"]'
            )

            if logged_in_elem:
                result["logged_in"] = True
                logger.info(f"[Account {account_id}] Login check: OK")
            else:
                result["error"] = "Not logged in"
                logger.warning(f"[Account {account_id}] Login check: NOT LOGGED IN")

            # Clean up
            await self.browser.close_context(account_id)

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[Account {account_id}] Login check error: {e}")

        return result

    async def check_shadowban(
        self,
        account_id: int,
        recent_post_url: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ) -> Dict:
        """Check if an account is shadowbanned by analyzing post performance.

        A shadowbanned account typically has:
        - Very low views on recent posts (< 10% of follower count)
        - Posts not appearing in hashtag searches
        """
        result = {
            "account_id": account_id,
            "is_shadowbanned": False,
            "confidence": 0.0,
            "details": {},
        }

        try:
            account = self.account_mgr.get_account(account_id)
            if not account:
                result["error"] = "Account not found"
                return result

            if not recent_post_url:
                # Get the latest post URL from the account's profile
                page = await self.browser.get_page(account_id, proxy_url)
                profile_url = f"https://www.tiktok.com/@{account.username}"
                success = await self.browser.navigate_safe(page, profile_url)
                if not success:
                    result["error"] = "Failed to load profile"
                    await self.browser.close_context(account_id)
                    return result

                await asyncio.sleep(4)

                # Try to find the latest post link
                post_links = await page.query_selector_all(
                    'a[href*="/video/"], [data-e2e="user-post-item"] a'
                )

                if post_links:
                    href = await post_links[0].get_attribute("href")
                    if href:
                        recent_post_url = f"https://www.tiktok.com{href}" if href.startswith("/") else href

                await self.browser.close_context(account_id)

            if not recent_post_url:
                result["message"] = "No recent posts found to check"
                return result

            # Load the post page to check views
            page = await self.browser.get_page(account_id, proxy_url)
            success = await self.browser.navigate_safe(page, recent_post_url)
            if not success:
                result["error"] = "Failed to load post"
                await self.browser.close_context(account_id)
                return result

            await asyncio.sleep(4)

            # Try to extract view count
            views_text = None
            views_elem = await page.query_selector(
                '[data-e2e="video-views"], [class*="view-count"], strong:has-text("views")'
            )
            if views_elem:
                views_text = await views_elem.inner_text()

            await self.browser.close_context(account_id)

            # Analyze
            if views_text:
                # Parse view count (e.g., "1.2M", "5K", "100")
                views_num = self._parse_count(views_text)
                followers = account.followers or 0

                if followers > 0 and views_num < followers * 0.1:
                    result["is_shadowbanned"] = True
                    result["confidence"] = 0.7
                    result["details"] = {
                        "views": views_num,
                        "followers": followers,
                        "ratio": round(views_num / followers, 4),
                        "threshold": 0.1,
                    }
                    logger.warning(f"[Account {account_id}] Possible shadowban: {views_num} views vs {followers} followers")
                elif views_num < 50 and followers > 100:
                    result["is_shadowbanned"] = True
                    result["confidence"] = 0.5
                    result["details"] = {
                        "views": views_num,
                        "followers": followers,
                        "ratio": round(views_num / followers, 4),
                    }
                    logger.warning(f"[Account {account_id}] Very low views: {views_num}")
                else:
                    result["details"] = {
                        "views": views_num,
                        "followers": followers,
                        "ratio": round(views_num / followers, 4) if followers > 0 else 0,
                    }
                    logger.info(f"[Account {account_id}] Shadowban check: OK ({views_num} views)")
            else:
                result["message"] = "Could not extract view count"

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[Account {account_id}] Shadowban check error: {e}")

        return result

    async def check_account_banned(self, account_id: int, proxy_url: Optional[str] = None) -> Dict:
        """Detect banned/suspended account pages."""
        result = {"account_id": account_id, "is_banned": False, "message": ""}
        try:
            page = await self.browser.get_page(account_id, proxy_url)
            await self.browser.navigate_safe(page, "https://www.tiktok.com/")
            await asyncio.sleep(2)
            body = await page.evaluate("() => document.body.innerText.toLowerCase()")
            banned_phrases = (
                "account was banned",
                "account has been banned",
                "permanently banned",
                "suspended",
                "violated our community guidelines",
            )
            for phrase in banned_phrases:
                if phrase in body:
                    result["is_banned"] = True
                    result["message"] = phrase
                    break
            await self.browser.close_context(account_id)
        except Exception as e:
            result["error"] = str(e)
        return result

    async def check_hashtag_visibility(
        self, account_id: int, hashtag: str = "fyp", proxy_url: Optional[str] = None
    ) -> Dict:
        """Supplementary shadowban signal: post visibility in hashtag search (best-effort)."""
        result = {"account_id": account_id, "visible_in_hashtag": None, "hashtag": hashtag}
        try:
            page = await self.browser.get_page(account_id, proxy_url)
            url = f"https://www.tiktok.com/tag/{hashtag.strip('#')}"
            if not await self.browser.navigate_safe(page, url):
                result["error"] = "navigation_failed"
                return result
            await asyncio.sleep(3)
            result["visible_in_hashtag"] = True
            await self.browser.close_context(account_id)
        except Exception as e:
            result["error"] = str(e)
        return result

    async def check_rate_limit(self, account_id: int, proxy_url: Optional[str] = None) -> Dict:
        """Check if the account is being rate-limited by TikTok."""
        result = {
            "account_id": account_id,
            "is_rate_limited": False,
            "error": None,
        }

        try:
            page = await self.browser.get_page(account_id, proxy_url)

            # Try various actions to check for rate limiting
            urls_to_check = [
                "https://www.tiktok.com/foryou",
                "https://www.tiktok.com/upload",
            ]

            for url in urls_to_check:
                success = await self.browser.navigate_safe(page, url)
                await asyncio.sleep(2)

                if success:
                    # Check for rate limit indicators
                    rate_limit_text = await page.query_selector(
                        'text=Too Many Requests, text=rate limit, text=Please slow down, '
                        'text=try again later, text=429, text=Too many attempts'
                    )

                    if rate_limit_text:
                        result["is_rate_limited"] = True
                        result["error"] = f"Rate limit detected on {url}"
                        logger.warning(f"[Account {account_id}] Rate limited on {url}")
                        await self.browser.close_context(account_id)
                        return result

                    # Also check response headers via console
                    rate_limited = await page.evaluate("""
                        () => {
                            const meta = document.querySelector('meta[http-equiv]');
                            if (meta && meta.content && meta.content.includes('rate')) return true;
                            return document.body.innerText.includes('Too Many Requests') ||
                                   document.body.innerText.includes('Please slow down');
                        }
                    """)
                    if rate_limited:
                        result["is_rate_limited"] = True
                        result["error"] = f"Rate limited detected on {url}"
                        logger.warning(f"[Account {account_id}] Rate limited on {url}")
                        await self.browser.close_context(account_id)
                        return result

            logger.info(f"[Account {account_id}] Rate limit check: OK")
            await self.browser.close_context(account_id)

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[Account {account_id}] Rate limit check error: {e}")

        return result

    async def check_account(self, account_id: int) -> Dict:
        """Run all health checks on a single account."""
        logger.info(f"Running full health check on account {account_id}")

        account = self.account_mgr.get_account(account_id)
        if not account:
            return {"account_id": account_id, "error": "Account not found"}

        # Get proxy
        proxy_url = None
        if account.proxy_id:
            try:
                from src.proxy_manager import ProxyManager
                pm = ProxyManager.from_settings(self.settings)
                pm.load_from_csv()
                proxy_obj = pm.get_proxy(account.proxy_id)
                if proxy_obj and proxy_obj.is_alive:
                    proxy_url = proxy_obj.url
            except Exception as e:
                logger.warning(f"Failed to get proxy for account {account_id}: {e}")

        results = {
            "account_id": account_id,
            "username": account.username,
            "status": account.status,
        }

        # Run checks
        login_result = await self.check_account_login(account_id, proxy_url)
        results["login"] = login_result

        shadowban_result = await self.check_shadowban(account_id, proxy_url=proxy_url)
        results["shadowban"] = shadowban_result

        rate_limit_result = await self.check_rate_limit(account_id, proxy_url)
        results["rate_limit"] = rate_limit_result

        # Take actions based on results
        needs_alert = False
        alert_message_parts = []

        if not login_result.get("logged_in"):
            banned = await self.check_account_banned(account_id, proxy_url)
            results["banned"] = banned
            if banned.get("is_banned"):
                self.account_mgr.set_status(account_id, "banned")
                self.account_mgr.add_alert(
                    account_id, "banned", banned.get("message", "Account banned")
                )
                needs_alert = True
                alert_message_parts.append("Account banned")
            else:
                self.account_mgr.add_alert(
                    account_id,
                    "login_fail",
                    "Account login check failed",
                )
                needs_alert = True
                alert_message_parts.append("Login failed")

        if shadowban_result.get("is_shadowbanned") and self.alert_on_shadowban:
            self.account_mgr.add_alert(
                account_id,
                "shadowban",
                f"Shadowban detected: {shadowban_result['details'].get('views', '?')} views "
                f"vs {shadowban_result['details'].get('followers', '?')} followers",
            )
            self.account_mgr.set_status(account_id, "shadowbanned")
            needs_alert = True
            alert_message_parts.append("Shadowban detected")

        if rate_limit_result.get("is_rate_limited") and self.alert_on_rate_limit:
            self.account_mgr.add_alert(
                account_id,
                "rate_limit",
                "Rate limiting detected",
            )
            needs_alert = True
            alert_message_parts.append("Rate limited")

        # Send Telegram alert if needed
        if needs_alert and self.telegram:
            message = "; ".join(alert_message_parts) or "Health check issues detected"
            await self.telegram.send_alert(
                alert_type="info",
                message=message,
                account_username=account.username,
            )

        results["alerted"] = needs_alert
        return results

    async def check_all_accounts(self) -> Dict:
        """Run health checks on all active accounts."""
        logger.info("Running health checks on all accounts")

        accounts = self.account_mgr.get_all_accounts()
        results = {
            "total": len(accounts),
            "checked": 0,
            "issues_found": 0,
            "details": [],
        }

        for account in accounts:
            if account.status in ("banned", "paused"):
                continue

            try:
                check_result = await self.check_account(account.id)
                results["details"].append(check_result)
                results["checked"] += 1

                if check_result.get("alerted"):
                    results["issues_found"] += 1

                # Add a small delay between checks to avoid rate limiting
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Health check failed for account {account.id}: {e}")

        logger.info(f"Health check complete: {results['checked']} checked, {results['issues_found']} issues")
        return results

    @staticmethod
    def _parse_count(text: str) -> int:
        """Parse a TikTok count string like '1.2M' or '5K' into an integer."""
        if not text:
            return 0

        text = text.strip().lower().replace(",", "").replace(" ", "")

        multipliers = {"k": 1000, "m": 1000000, "b": 1000000000}

        for suffix, multiplier in multipliers.items():
            if text.endswith(suffix):
                try:
                    return int(float(text[:-1]) * multiplier)
                except ValueError:
                    return 0

        try:
            return int(float(text))
        except ValueError:
            return 0

    @classmethod
    def from_settings(cls, settings: dict, account_manager, browser_manager, telegram_alert=None) -> "HealthMonitor":
        """Create instance from settings dict."""
        return cls(
            account_manager=account_manager,
            browser_manager=browser_manager,
            telegram_alert=telegram_alert,
            settings=settings,
        )

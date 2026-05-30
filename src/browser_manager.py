# TikTok Farm - Browser Manager Module
# Singleton factory for Camoufox browser instances with Playwright

import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed. Browser features disabled.")


class BrowserManager:
    """Singleton factory managing Camoufox/Playwright browser instances.

    Each account gets its own browser context with unique fingerprint and proxy.
    Supports lifecycle management (create, close, recycle).
    """

    _instance: Optional["BrowserManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        profile_dir: str = "profiles/",
        headless: bool = True,
        navigation_timeout: int = 30000,
    ):
        # Only initialize once
        if hasattr(self, "_initialized") and self._initialized:
            return
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.navigation_timeout = navigation_timeout
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: Dict[int, BrowserContext] = {}  # account_id -> context
        self._pages: Dict[int, Any] = {}  # account_id -> page
        self._lock = asyncio.Lock()
        self._initialized = True
        logger.info(f"BrowserManager initialized (headless={headless}, profiles={profile_dir})")

    async def _ensure_playwright(self):
        """Lazy-init Playwright and browser."""
        if self._playwright is None:
            if not PLAYWRIGHT_AVAILABLE:
                raise RuntimeError("Playwright is not installed. Run: pip install playwright && playwright install chromium")
            self._playwright = await async_playwright().start()
            logger.debug("Playwright started")

        if self._browser is None:
            # Use Chromium (Camoufox is Firefox-based, but Playwright Chromium works
            # for most TikTok automation; for actual Camoufox, use the camoufox package)
            launch_options = {
                "headless": self.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            }

            try:
                self._browser = await self._playwright.chromium.launch(**launch_options)
                logger.info("Browser launched successfully")
            except Exception as e:
                logger.error(f"Failed to launch browser: {e}")
                raise

    async def create_context(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        user_agent: Optional[str] = None,
        viewport: Optional[Dict] = None,
    ) -> BrowserContext:
        """Create a browser context for an account with fingerprint and proxy."""
        async with self._lock:
            # Reuse existing context if available
            if account_id in self._contexts:
                try:
                    ctx = self._contexts[account_id]
                    # Check if context is still usable
                    pages = ctx.pages
                    if pages:
                        logger.debug(f"Reusing existing context for account {account_id}")
                        return ctx
                except Exception:
                    # Context is dead, remove it
                    del self._contexts[account_id]
                    if account_id in self._pages:
                        del self._pages[account_id]

            await self._ensure_playwright()

            context_options = {
                "viewport": viewport or {"width": 390, "height": 844},  # iPhone 14 size for mobile-like
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "permissions": ["geolocation"],
                "geolocation": {"latitude": 40.7128, "longitude": -74.0060},
            }

            if user_agent:
                context_options["user_agent"] = user_agent

            # Fingerprint data directory per account
            profile_path = self.profile_dir / str(account_id)
            profile_path.mkdir(parents=True, exist_ok=True)
            context_options["user_data_dir"] = str(profile_path)

            # Apply proxy if provided
            if proxy_url:
                try:
                    # Parse proxy URL for Playwright format
                    proxy_options = self._parse_proxy_url(proxy_url)
                    context_options["proxy"] = proxy_options
                    logger.debug(f"Using proxy for account {account_id}: {proxy_url[:30]}...")
                except Exception as e:
                    logger.warning(f"Failed to set proxy for account {account_id}: {e}")

            try:
                context = await self._browser.new_context(**context_options)
                context.set_default_timeout(self.navigation_timeout)
                self._contexts[account_id] = context
                logger.info(f"Created browser context for account {account_id}")
                return context
            except Exception as e:
                logger.error(f"Failed to create browser context for account {account_id}: {e}")
                raise

    async def get_page(self, account_id: int, proxy_url: Optional[str] = None) -> Any:
        """Get or create a page for an account."""
        if account_id in self._pages:
            try:
                page = self._pages[account_id]
                if not page.is_closed():
                    return page
            except Exception:
                pass

        context = await self.create_context(account_id, proxy_url)
        page = await context.new_page()
        self._pages[account_id] = page
        logger.debug(f"Created new page for account {account_id}")
        return page

    async def close_context(self, account_id: int):
        """Close the browser context for a specific account."""
        async with self._lock:
            if account_id in self._pages:
                try:
                    await self._pages[account_id].close()
                except Exception as e:
                    logger.warning(f"Error closing page for account {account_id}: {e}")
                del self._pages[account_id]

            if account_id in self._contexts:
                try:
                    await self._contexts[account_id].close()
                    logger.info(f"Closed browser context for account {account_id}")
                except Exception as e:
                    logger.warning(f"Error closing context for account {account_id}: {e}")
                del self._contexts[account_id]

    async def close_all(self):
        """Close all browser contexts and the browser itself."""
        async with self._lock:
            # Close all pages
            for acc_id in list(self._pages.keys()):
                try:
                    await self._pages[acc_id].close()
                except Exception:
                    pass
            self._pages.clear()

            # Close all contexts
            for acc_id in list(self._contexts.keys()):
                try:
                    await self._contexts[acc_id].close()
                except Exception:
                    pass
            self._contexts.clear()

            # Close browser
            if self._browser:
                try:
                    await self._browser.close()
                    logger.info("Browser closed")
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")
                self._browser = None

            # Stop Playwright
            if self._playwright:
                try:
                    await self._playwright.stop()
                    logger.info("Playwright stopped")
                except Exception as e:
                    logger.warning(f"Error stopping Playwright: {e}")
                self._playwright = None

            logger.info("All browser resources cleaned up")

    async def navigate_safe(self, page, url: str, timeout: Optional[int] = None) -> bool:
        """Navigate to a URL with error handling."""
        try:
            timeout = timeout or self.navigation_timeout
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=timeout)
            return True
        except Exception as e:
            logger.warning(f"Navigation failed to {url}: {e}")
            return False

    @staticmethod
    def _parse_proxy_url(proxy_url: str) -> dict:
        """Parse proxy URL into Playwright proxy format."""
        # Format: protocol://user:pass@host:port or protocol://host:port
        result = {"server": proxy_url}

        # Handle authentication in the URL
        if "@" in proxy_url:
            try:
                # Extract auth part
                protocol_part, rest = proxy_url.split("://", 1) if "://" in proxy_url else ("http", proxy_url)
                auth_part, server_part = rest.rsplit("@", 1)
                if ":" in auth_part:
                    username, password = auth_part.split(":", 1)
                    result["username"] = username
                    result["password"] = password
                result["server"] = f"{protocol_part}://{server_part}"
            except Exception:
                pass

        return result

    @classmethod
    def get_instance(cls) -> "BrowserManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def from_settings(cls, settings: dict) -> "BrowserManager":
        """Create or return singleton from settings."""
        cam_config = settings.get("camoufox", {})
        instance = cls.get_instance()
        instance.profile_dir = Path(cam_config.get("profile_dir", "profiles/"))
        instance.headless = cam_config.get("headless", True)
        instance.navigation_timeout = cam_config.get("navigation_timeout", 30000)
        instance.profile_dir.mkdir(parents=True, exist_ok=True)
        return instance

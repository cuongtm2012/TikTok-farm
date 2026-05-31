# TikTok Farm - Browser Manager Module
# Singleton factory: Camoufox (preferred) or Playwright Chromium fallback
# FIXED v2: Auto-reconnect browser on crash + health heartbeat

import json
import os
import logging
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed. Browser features disabled.")

CAMOUFOX_AVAILABLE = False
try:
    from camoufox.async_api import AsyncCamoufox  # type: ignore
    CAMOUFOX_AVAILABLE = True
except ImportError:
    AsyncCamoufox = None  # type: ignore

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""


class BrowserManager:
    """Singleton factory managing browser instances per account (Camoufox or Chromium).
    
    FIXED v2: Auto-reconnect on crash + periodic health heartbeat.
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
        use_camoufox: bool = True,
    ):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self.profile_dir = Path(profile_dir)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.navigation_timeout = navigation_timeout
        self.use_camoufox = use_camoufox and CAMOUFOX_AVAILABLE
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._camoufox = None
        self._contexts: Dict[int, BrowserContext] = {}
        self._pages: Dict[int, Any] = {}
        self._lock = asyncio.Lock()
        self._initialized = True
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self._browser_launched = False
        engine = "Camoufox" if self.use_camoufox else "Chromium"
        logger.info(f"BrowserManager initialized ({engine}, headless={headless})")

    async def _ensure_playwright(self):
        """Launch or reconnect browser if crashed."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        if self.use_camoufox and CAMOUFOX_AVAILABLE:
            if self._camoufox is None or not self._browser_launched:
                await self._close_browser_safe()
                self._camoufox = AsyncCamoufox(headless=self.headless)
                self._browser = await self._camoufox.__aenter__()
                self._browser_launched = True
                logger.info("Camoufox browser launched")
            return

        # Check if browser is still alive
        if self._browser is not None:
            try:
                # Quick health check - try to access browser contexts
                _ = await self._browser.contexts
                return  # Browser is alive
            except Exception as e:
                logger.warning(f"Browser connection lost ({e}), reconnecting...")
                await self._close_browser_safe()

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        launch_options = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-blink-features=AutomationControlled",
                "--lang=en-US",
            ],
        }
        self._browser = await self._playwright.chromium.launch(**launch_options)
        self._browser_launched = True
        logger.info("Chromium browser launched (Camoufox disabled or unavailable)")

    async def _close_browser_safe(self):
        """Safely close browser with error tolerance."""
        try:
            if self.use_camoufox and self._camoufox:
                await self._camoufox.__aexit__(None, None, None)
            elif self._browser:
                await self._browser.close()
        except Exception as e:
            logger.debug(f"Browser close (expected during reconnect): {e}")
        self._browser = None
        self._camoufox = None
        self._browser_launched = False

    async def start_heartbeat(self, interval_seconds: int = 60):
        """Start periodic browser health check to detect crashes early."""
        if self._heartbeat_task is not None:
            return

        async def _heartbeat_loop():
            while not self._shutdown:
                try:
                    await asyncio.sleep(interval_seconds)
                    if self._browser is not None:
                        # Check browser connectivity
                        try:
                            _ = await self._browser.contexts
                        except Exception:
                            logger.warning("Browser heartbeat failed, browser needs reconnect")
                            # Clear stale contexts
                            self._contexts.clear()
                            self._pages.clear()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"Heartbeat error: {e}")

        self._heartbeat_task = asyncio.create_task(_heartbeat_loop())
        logger.info(f"Browser heartbeat started ({interval_seconds}s interval)")

    async def stop_heartbeat(self):
        """Stop the heartbeat loop."""
        self._shutdown = True
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def create_browser(
        self,
        account_id: int,
        headless: Optional[bool] = None,
        proxy_url: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> BrowserContext:
        """Spec alias: create isolated browser context for account."""
        if headless is not None:
            self.headless = headless
        return await self.create_context(account_id, proxy_url, user_agent)

    def _storage_state_path(self, account_id: int) -> Path:
        return self.profile_dir / str(account_id) / "storage_state.json"

    def write_storage_state_from_cookies(self, account_id: int, cookies: list) -> bool:
        """Create storage_state.json from cookie list (no browser required)."""
        if not cookies:
            return False
        try:
            from src.cookie_manager import CookieManager

            path = self._storage_state_path(account_id)
            ok = CookieManager.save_to_storage_state(cookies, str(path))
            if ok:
                logger.info(
                    f"Created storage_state for account {account_id} ({len(cookies)} cookies)"
                )
            return ok
        except Exception as e:
            logger.warning(f"Failed to write storage_state for {account_id}: {e}")
            return False

    def delete_storage_state(self, account_id: int) -> bool:
        try:
            path = self._storage_state_path(account_id)
            if path.exists():
                path.unlink()
                logger.info(f"Deleted storage_state for account {account_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete storage_state: {e}")
            return False

    async def create_context(
        self,
        account_id: int,
        proxy_url: Optional[str] = None,
        user_agent: Optional[str] = None,
        viewport: Optional[Dict] = None,
        extra_http_headers: Optional[Dict[str, str]] = None,
    ) -> BrowserContext:
        async with self._lock:
            if account_id in self._contexts:
                try:
                    ctx = self._contexts[account_id]
                    if ctx.pages:
                        return ctx
                except Exception:
                    del self._contexts[account_id]
                    self._pages.pop(account_id, None)

            await self._ensure_playwright()

            context_options: Dict[str, Any] = {
                "viewport": viewport or {"width": 390, "height": 844},
                "locale": "en-US",
                "timezone_id": "America/New_York",
            }
            if user_agent:
                context_options["user_agent"] = user_agent
            if extra_http_headers:
                context_options["extra_http_headers"] = extra_http_headers

            storage_path = self._storage_state_path(account_id)
            # Ephemeral scan contexts (high account_id) skip persisted cookies
            if account_id >= 900_000_000:
                storage_path = None
            if storage_path:
                storage_path.parent.mkdir(parents=True, exist_ok=True)
                if storage_path.exists():
                    context_options["storage_state"] = str(storage_path)

            if proxy_url:
                try:
                    context_options["proxy"] = self._parse_proxy_url(proxy_url)
                except Exception as e:
                    logger.warning(f"Proxy config failed for account {account_id}: {e}")

            try:
                context = await self._browser.new_context(**context_options)
            except Exception as e:
                # Browser might have crashed between health check and context creation
                logger.warning(f"Failed to create context (reconnecting browser): {e}")
                await self._ensure_playwright()  # Force reconnect
                context = await self._browser.new_context(**context_options)

            context.set_default_timeout(self.navigation_timeout)
            self.apply_stealth(context)
            self._contexts[account_id] = context
            logger.info(f"Browser context created for account {account_id}")
            return context

    def apply_stealth(self, context: "BrowserContext") -> None:
        """Anti-detection init scripts (tiktok-uploader browsers.py pattern)."""
        try:
            context.add_init_script(STEALTH_INIT_SCRIPT)
        except Exception as e:
            logger.debug(f"apply_stealth failed: {e}")

    async def save_storage_state(self, account_id: int):
        """Persist cookies/local storage to profile dir."""
        if account_id not in self._contexts:
            return
        path = self._storage_state_path(account_id)
        try:
            await self._contexts[account_id].storage_state(path=str(path))
            logger.debug(f"Saved storage state for account {account_id}")
        except Exception as e:
            logger.warning(f"Failed to save storage state: {e}")

    async def get_page(self, account_id: int, proxy_url: Optional[str] = None) -> Any:
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
        return page

    async def close_context(self, account_id: int):
        async with self._lock:
            if account_id < 900_000_000:
                await self.save_storage_state(account_id)
            if account_id in self._pages:
                try:
                    await self._pages[account_id].close()
                except Exception:
                    pass
                del self._pages[account_id]

            if account_id in self._contexts:
                try:
                    await self._contexts[account_id].close()
                except Exception:
                    pass
                del self._contexts[account_id]

    async def close_all(self):
        await self.stop_heartbeat()
        async with self._lock:
            self._shutdown = True
            for acc_id in list(self._pages.keys()):
                try:
                    await self._pages[acc_id].close()
                except Exception:
                    pass
            self._pages.clear()

            for acc_id in list(self._contexts.keys()):
                try:
                    await self._contexts[acc_id].close()
                except Exception:
                    pass
            self._contexts.clear()

            await self._close_browser_safe()

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None

    async def navigate_safe(self, page, url: str, timeout: Optional[int] = None) -> bool:
        try:
            timeout = timeout or self.navigation_timeout
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return True
        except Exception as e:
            logger.warning(f"Navigation failed to {url}: {e}")
            return False

    @staticmethod
    def _parse_proxy_url(proxy_url: str) -> dict:
        result = {"server": proxy_url}
        if "@" in proxy_url:
            try:
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
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def from_settings(cls, settings: dict) -> "BrowserManager":
        cam_config = settings.get("camoufox", {})
        instance = cls.get_instance()
        instance.profile_dir = Path(cam_config.get("profile_dir", "profiles/"))
        instance.headless = cam_config.get("headless", True)
        instance.navigation_timeout = cam_config.get("navigation_timeout", 30000)
        instance.use_camoufox = cam_config.get("use_camoufox", False)
        instance.profile_dir.mkdir(parents=True, exist_ok=True)
        if instance.use_camoufox and not CAMOUFOX_AVAILABLE:
            logger.warning("camoufox.use_camoufox=true but package not installed; using Chromium")
            instance.use_camoufox = False
        return instance

# TikTok Farm - Browser Manager Module
# Singleton factory: Camoufox (preferred) or Playwright Chromium fallback

import json
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


class BrowserManager:
    """Singleton factory managing browser instances per account (Camoufox or Chromium)."""

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
        use_camoufox: bool = False,
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
        engine = "Camoufox" if self.use_camoufox else "Chromium"
        logger.info(f"BrowserManager initialized ({engine}, headless={headless})")

    async def _ensure_playwright(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        if self.use_camoufox and CAMOUFOX_AVAILABLE:
            if self._camoufox is None:
                self._camoufox = AsyncCamoufox(headless=self.headless)
                self._browser = await self._camoufox.__aenter__()
                logger.info("Camoufox browser launched")
            return

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        if self._browser is None:
            launch_options = {
                "headless": self.headless,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ],
            }
            self._browser = await self._playwright.chromium.launch(**launch_options)
            logger.info("Chromium browser launched (Camoufox disabled or unavailable)")

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
            path = self._storage_state_path(account_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {"cookies": cookies, "origins": []}
            path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            logger.info(
                f"Created storage_state for account {account_id} ({len(cookies)} cookies)"
            )
            return True
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

            storage_path = self._storage_state_path(account_id)
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            if storage_path.exists():
                context_options["storage_state"] = str(storage_path)

            if proxy_url:
                try:
                    context_options["proxy"] = self._parse_proxy_url(proxy_url)
                except Exception as e:
                    logger.warning(f"Proxy config failed for account {account_id}: {e}")

            context = await self._browser.new_context(**context_options)
            context.set_default_timeout(self.navigation_timeout)
            self._contexts[account_id] = context
            logger.info(f"Browser context created for account {account_id}")
            return context

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
        async with self._lock:
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

            if self._browser:
                try:
                    if self.use_camoufox and self._camoufox:
                        await self._camoufox.__aexit__(None, None, None)
                    else:
                        await self._browser.close()
                except Exception as e:
                    logger.warning(f"Error closing browser: {e}")
                self._browser = None
                self._camoufox = None

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

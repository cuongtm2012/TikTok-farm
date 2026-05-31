# TikTok Farm - Main Application Entry Point
# FastAPI app with APScheduler startup, graceful shutdown
# FIXED v2: Clean signal handling (no os._exit), browser heartbeat, graceful shutdown

import os
import sys
import yaml
import json
import logging
import signal
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Third-party imports
try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

# Local imports
from src.database import Database
from src.proxy_manager import ProxyManager
from src.account_manager import AccountManager
from src.session_service import SessionService
from src.warmup_manager import WarmupManager
from src.browser_manager import BrowserManager
from src.event_bus import FarmEventBus
from src.farm_engine import FarmEngine
from src.content_pipeline import ContentPipeline
from src.post_engine import PostEngine
from src.scheduler import FarmScheduler
from src.health_monitor import HealthMonitor
from src.telegram_alert import TelegramAlert
from src.profile_scanner import ProfileScanner
from src.cookie_manager import CookieManager
from src.affiliate import AffiliatePipeline

# Setup logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-5s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_DIR / "farm.log"), encoding="utf-8"),
    ],
)

logger = logging.getLogger("farm.main")

# ---- Settings ----

def load_settings(path: str = "config/settings.yaml") -> dict:
    """Load settings from YAML file."""
    settings_path = Path(path)
    if not settings_path.exists():
        logger.warning(f"Settings file not found at {path}, using defaults")
        return {
            "app": {"name": "TikTok Farm", "version": "1.0.0", "debug": False, "log_level": "INFO"},
            "database": {"driver": "sqlite", "path": "data/farm.db"},
            "proxies": {"csv_path": "config/proxies.csv", "check_timeout": 5, "max_fail_before_disable": 3},
            "scheduler": {"posts_per_day": 3, "farm_sessions_per_day": 3, "farm_session_minutes": 15,
                          "post_time_slots": [["08:00", "11:00"], ["14:00", "17:00"], ["19:00", "22:00"]]},
            "content": {"images_per_post": 5, "output_dir": "data/posts/"},
            "camoufox": {"headless": True, "profile_dir": "profiles/", "navigation_timeout": 30000},
            "health_check": {"interval_minutes": 60, "alert_on_shadowban": True, "alert_on_rate_limit": True},
            "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
        }

    try:
        with open(settings_path, "r") as f:
            settings = yaml.safe_load(f)
        log_level = settings.get("app", {}).get("log_level", "INFO")
        logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))
        logger.info(f"Settings loaded from {path}")
        return settings
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        sys.exit(1)


# ---- Global State ----

class AppState:
    """Holds all application components."""

    def __init__(self, settings: dict):
        self.settings = settings
        self.shutdown_requested = False
        self.db = Database.from_settings(settings)

        # Initialize components
        self.telegram = TelegramAlert.from_settings(settings)
        self.proxy_manager = ProxyManager.from_settings(settings)
        self.account_manager = AccountManager(db=self.db)
        self.browser_manager = BrowserManager.from_settings(settings)
        self.event_bus = FarmEventBus.get_instance()
        self.farm_engine = FarmEngine(
            self.browser_manager,
            account_manager=self.account_manager,
            event_bus=self.event_bus,
        )
        self.active_farm_tasks: dict = {}
        self.active_post_tasks: dict = {}
        self.content_pipeline = ContentPipeline.from_settings(settings)
        self.cookie_manager = CookieManager()
        self.post_engine = PostEngine(
            self.browser_manager,
            self.account_manager,
            cookie_manager=self.cookie_manager,
        )
        self.session_service = SessionService(self.account_manager, self.proxy_manager)
        self.warmup_manager = WarmupManager(
            self.account_manager, self.proxy_manager, settings
        )
        self.health_monitor = HealthMonitor.from_settings(
            settings, self.account_manager, self.browser_manager, self.telegram
        )
        self.affiliate_pipeline = AffiliatePipeline(
            settings, self.post_engine, self.account_manager
        )
        self.scheduler = FarmScheduler.from_settings(
            settings,
            self.account_manager,
            self.farm_engine,
            self.post_engine,
            self.health_monitor,
            proxy_manager=self.proxy_manager,
            session_service=self.session_service,
            warmup_manager=self.warmup_manager,
            affiliate_pipeline=self.affiliate_pipeline,
        )
        self.profile_scanner = ProfileScanner(self.browser_manager)

        self.proxy_manager.load_from_csv()
        logger.info("All components initialized")

    async def startup(self):
        """Start all services."""
        logger.info("Starting TikTok Farm services...")

        self.proxy_manager.sync_proxies_to_db(self.db)

        if self.warmup_manager:
            tick = self.warmup_manager.run_daily_tick()
            logger.info(f"Warm-up on startup: {tick}")

        # Start browser heartbeat to detect crashes early
        await self.browser_manager.start_heartbeat(interval_seconds=60)

        # Warm up browser for profile scanner
        try:
            await self.browser_manager._ensure_playwright()
            logger.info("Browser ready for profile scanner")
        except Exception as e:
            logger.warning(f"Browser warmup failed (non-fatal): {e}")

        self.scheduler.start()

        # Send startup notification
        if self.telegram.enabled:
            await self.telegram.send_alert(
                "info",
                f"TikTok Farm v{self.settings.get('app', {}).get('version', '1.0.0')} started\n"
                f"Accounts: {len(self.account_manager.get_all_accounts())}\n"
                f"Proxies: {len(self.proxy_manager.get_all_proxies())}",
            )

        logger.info("TikTok Farm startup complete")

    async def shutdown(self):
        """Gracefully shut down all services."""
        if self.shutdown_requested:
            return
        self.shutdown_requested = True
        
        logger.info("Shutting down TikTok Farm...")

        # Stop scheduler first (no new tasks)
        self.scheduler.stop()

        # Cancel any running farm tasks gracefully
        logger.info("Waiting for active farm tasks to complete...")
        await asyncio.sleep(2)

        # Close all browsers
        await self.browser_manager.close_all()

        # Close proxy manager
        await self.proxy_manager.close()

        if self.affiliate_pipeline:
            await self.affiliate_pipeline.close()

        # Close telegram
        await self.telegram.close()

        # Send shutdown notification
        if self.telegram.enabled:
            await self.telegram.send_alert("info", "TikTok Farm shutting down")

        logger.info("TikTok Farm shutdown complete")


# ---- Lifespan ----

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """FastAPI lifespan handler for startup/shutdown."""
    # Startup
    settings = load_settings()
    state = AppState(settings)
    app.state.farm = state

    await state.startup()
    logger.info("Application ready")

    yield

    # Shutdown
    await state.shutdown()


# ---- Create FastAPI App ----

if FASTAPI_AVAILABLE:
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import RedirectResponse

    app = FastAPI(
        title="TikTok Farm",
        description="Automated TikTok account farming and content management system",
        version="1.0.0",
        lifespan=app_lifespan,
    )

    static_dir = Path(__file__).parent.parent / "web" / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def root_redirect():
        return RedirectResponse(url="/api/dashboard")

    # Health endpoint
    @app.get("/health")
    async def health_check():
        return {
            "status": "ok",
            "service": "TikTok Farm",
            "version": "1.0.0",
        }

    # Import and include API routes
    try:
        from web.api import router as api_router
        app.include_router(api_router, prefix="/api")
        logger.info("API routes registered")
    except ImportError as e:
        logger.warning(f"API routes not available: {e}")
    except Exception as e:
        logger.error(f"Failed to register API routes: {e}")

else:
    # Standalone mode (no FastAPI)
    app = None
    logger.warning("FastAPI not installed. Running in headless mode.")


# ---- CLI Entry Point ----

async def run_headless():
    """Run the farm system without the web server."""
    settings = load_settings()
    state = AppState(settings)

    # Setup signal handlers - clean shutdown, no os._exit
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    def _signal_handler():
        if not shutdown_event.is_set():
            logger.warning("Shutdown signal received")
            shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    await state.startup()

    # Wait for shutdown signal
    logger.info("Farm running. Press Ctrl+C to stop.")
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    await state.shutdown()


def main():
    """Main entry point."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--headless":
        asyncio.run(run_headless())
    else:
        if FASTAPI_AVAILABLE and app is not None:
            import uvicorn
            settings = load_settings()
            debug = settings.get("app", {}).get("debug", False)
            logger.info("Starting FastAPI web server...")
            uvicorn.run(
                "src.main:app",
                host="0.0.0.0",
                port=8080,
                reload=False,
                log_level="info",
            )
        else:
            logger.info("FastAPI not available, running headless")
            asyncio.run(run_headless())


if __name__ == "__main__":
    main()

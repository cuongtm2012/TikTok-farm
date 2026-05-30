# TikTok Farm — browser session preparation (proxy check, rotate, credentials)

import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class SessionService:
    """Prepares proxy + credentials before farm/post/health browser tasks."""

    def __init__(self, account_manager, proxy_manager):
        self.account_mgr = account_manager
        self.proxy_mgr = proxy_manager

    async def prepare(self, account_id: int) -> Dict[str, Any]:
        """Validate proxy, rotate if dead, return session kwargs for browser engines."""
        account = self.account_mgr.get_account(account_id)
        if not account:
            return {"ok": False, "error": "account_not_found"}

        proxy_url, proxy_id = await self.proxy_mgr.ensure_proxy_for_account(
            account, self.account_mgr
        )
        if not proxy_url:
            return {"ok": False, "error": "no_alive_proxy"}

        if proxy_id and proxy_id != account.proxy_id:
            account = self.account_mgr.get_account(account_id)

        return {
            "ok": True,
            "account": account,
            "proxy_url": proxy_url,
            "cookie_data": account.cookie_data,
            "username": account.username,
            "password": getattr(account, "password", "") or "",
        }

    @staticmethod
    def save_cookies(account_manager, account_id: int, cookies) -> bool:
        if not cookies:
            return False
        try:
            payload = json.dumps(cookies) if isinstance(cookies, list) else cookies
            account_manager.update_account(account_id, cookie_data=payload)
            logger.info(f"Saved session cookies for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to save cookies for account {account_id}: {e}")
            return False

# TikTok Farm - Telegram Alert Module
# Sends notifications to Telegram for important events

import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramAlert:
    """Wrapper for sending Telegram notifications."""

    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_message(self, message: str) -> bool:
        """Send a text message to the configured Telegram chat."""
        if not self.enabled or not self.bot_token or not self.chat_id:
            logger.debug("Telegram alerts disabled or not configured. Skipping message.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            session = await self._get_session()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    logger.info("Telegram message sent successfully")
                    return True
                else:
                    text = await resp.text()
                    logger.error(f"Telegram API error: {resp.status} - {text}")
                    return False
        except asyncio.TimeoutError:
            logger.error("Telegram request timed out")
            return False
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_alert(self, alert_type: str, message: str, account_username: str = "") -> bool:
        """Send a formatted alert message."""
        prefix = ""
        icon = ""

        if alert_type == "shadowban":
            icon = "🚫"
            prefix = "SHADOWBAN DETECTED"
        elif alert_type == "rate_limit":
            icon = "⚠️"
            prefix = "RATE LIMIT HIT"
        elif alert_type == "banned":
            icon = "🔴"
            prefix = "ACCOUNT BANNED"
        elif alert_type == "login_fail":
            icon = "❌"
            prefix = "LOGIN FAILED"
        elif alert_type == "info":
            icon = "ℹ️"
            prefix = "INFO"
        elif alert_type == "success":
            icon = "✅"
            prefix = "SUCCESS"
        else:
            icon = "🔔"
            prefix = "ALERT"

        account_info = f" [{account_username}]" if account_username else ""
        formatted = f"{icon} <b>{prefix}</b>{account_info}\n{message}"
        return await self.send_message(formatted)

    async def close(self):
        """Clean up the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    @classmethod
    def from_settings(cls, settings: dict) -> "TelegramAlert":
        """Create instance from settings dict."""
        tg = settings.get("telegram", {})
        return cls(
            bot_token=tg.get("bot_token", ""),
            chat_id=tg.get("chat_id", ""),
            enabled=tg.get("enabled", False),
        )


import asyncio  # noqa: E402 needed for async TimeoutError handling

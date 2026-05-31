# TikTok Farm — real-time event bus for WebSocket streaming (farm + post sessions)

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, AsyncGenerator, Any

logger = logging.getLogger(__name__)


class FarmEventBus:
    """Singleton in-memory queue per session_id for live dashboard updates."""

    _instance: Optional["FarmEventBus"] = None

    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = {}

    @classmethod
    def get_instance(cls) -> "FarmEventBus":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create_session(self, session_id: str, maxsize: int = 500) -> None:
        self._queues[session_id] = asyncio.Queue(maxsize=maxsize)
        logger.debug(f"Event session created: {session_id}")

    def emit(
        self,
        session_id: str,
        event_type: str,
        account_id: Optional[int] = None,
        data: Optional[dict] = None,
    ) -> None:
        if not session_id:
            return
        event = {
            "type": event_type,
            "account_id": account_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": data or {},
        }
        q = self._queues.get(session_id)
        if not q:
            return
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(f"Event queue full for {session_id}, dropping event")

    async def subscribe(self, session_id: str) -> AsyncGenerator[dict, Any]:
        q = self._queues.get(session_id)
        if not q:
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield event
                except asyncio.TimeoutError:
                    yield {"type": "farm:ping", "data": {}}
        except asyncio.CancelledError:
            pass
        finally:
            self.cleanup_session(session_id)

    def cleanup_session(self, session_id: str) -> None:
        self._queues.pop(session_id, None)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._queues

# TikTok Farm — rotating farm queue (max N concurrent, rest wait in line)

import asyncio
import logging
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class FarmQueue:
    """FIFO queue: up to max_concurrent farm sessions in parallel, then rotate through pending."""

    def __init__(self, state):
        self.state = state
        self.pending: Deque[int] = deque()
        self.pending_set: Set[int] = set()
        self._worker: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.errors: Dict[int, str] = {}
        self.stats = {"enqueued": 0, "completed": 0, "failed": 0, "skipped": 0}

    @property
    def max_concurrent(self) -> int:
        return getattr(self.state, "max_concurrent_farms", 3)

    @property
    def duration_minutes(self) -> int:
        farm_cfg = self.state.settings.get("farm", {})
        if farm_cfg.get("session_minutes") is not None:
            return int(farm_cfg["session_minutes"])
        sch = self.state.settings.get("scheduler", {})
        return int(sch.get("farm_session_minutes", 15))

    def _proxy_url(self, account) -> Optional[str]:
        if not account or not account.proxy_id:
            return None
        proxy_obj = self.state.proxy_manager.get_proxy(account.proxy_id)
        if proxy_obj and proxy_obj.is_alive:
            return proxy_obj.url
        return None

    def enqueue(self, account_ids: List[int]) -> dict:
        added = 0
        skipped = 0
        for aid in account_ids:
            if aid in self.state.active_farm_tasks or aid in self.pending_set:
                skipped += 1
                continue
            self.pending.append(aid)
            self.pending_set.add(aid)
            added += 1
        self.stats["enqueued"] += added
        self.stats["skipped"] += skipped
        if added:
            self._ensure_worker()
        return {
            "added": added,
            "skipped": skipped,
            "pending": len(self.pending),
        }

    def status(self) -> dict:
        return {
            "pending": list(self.pending),
            "pending_count": len(self.pending),
            "running": list(self.state.active_farm_tasks.keys()),
            "running_count": len(self.state.active_farm_tasks),
            "max_concurrent": self.max_concurrent,
            "duration_minutes": self.duration_minutes,
            "stats": dict(self.stats),
            "errors": {str(k): v for k, v in self.errors.items()},
            "worker_active": self._worker is not None and not self._worker.done(),
        }

    def clear_pending(self) -> int:
        n = len(self.pending)
        self.pending.clear()
        self.pending_set.clear()
        return n

    def _ensure_worker(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def _run_one(self, account_id: int):
        account = self.state.account_manager.get_account(account_id)
        if not account:
            raise ValueError("Account not found")
        if account_id in self.state.active_farm_tasks:
            raise ValueError("Farm already running")

        session_id = f"farm_{account_id}_{int(time.time())}"
        self.state.event_bus.create_session(session_id)
        self.state.active_farm_tasks[account_id] = session_id
        proxy = self._proxy_url(account)
        engine = self.state.make_farm_engine()
        try:
            async with self.state.farm_semaphore:
                await engine.run_farm_session(
                    account_id=account_id,
                    proxy_url=proxy,
                    duration_minutes=self.duration_minutes,
                    session_id=session_id,
                )
        finally:
            self.state.active_farm_tasks.pop(account_id, None)

    async def _run_one_wrapper(self, account_id: int):
        try:
            await self._run_one(account_id)
            self.stats["completed"] += 1
            self.errors.pop(account_id, None)
            logger.info(f"Farm queue completed account {account_id}")
        except Exception as e:
            self.stats["failed"] += 1
            self.errors[account_id] = str(e)
            logger.warning(f"Farm queue failed for account {account_id}: {e}")

    async def _run_worker(self):
        logger.info(
            f"Farm queue worker started (max {self.max_concurrent} parallel, "
            f"{self.duration_minutes} min/session)"
        )
        running_tasks: Dict[int, asyncio.Task] = {}
        try:
            while True:
                max_c = self.max_concurrent
                while len(running_tasks) < max_c:
                    account_id = None
                    async with self._lock:
                        if self.pending:
                            account_id = self.pending.popleft()
                            self.pending_set.discard(account_id)
                    if account_id is None:
                        break
                    if account_id in running_tasks:
                        continue
                    running_tasks[account_id] = asyncio.create_task(
                        self._run_one_wrapper(account_id)
                    )

                if not running_tasks and not self.pending:
                    break

                if running_tasks:
                    done, _ = await asyncio.wait(
                        running_tasks.values(),
                        timeout=2.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        for aid, t in list(running_tasks.items()):
                            if t is task:
                                running_tasks.pop(aid, None)
                                break
                else:
                    await asyncio.sleep(0.5)
        finally:
            if running_tasks:
                await asyncio.gather(*running_tasks.values(), return_exceptions=True)
            logger.info("Farm queue worker finished batch")
            self._worker = None

    async def shutdown(self):
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None

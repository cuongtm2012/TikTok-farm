# TikTok Farm — central account logs (persist + ring buffer + WebSocket)

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"password", "cookie_data", "cookies", "ms_token", "token", "secret"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_details(details: Optional[dict]) -> dict:
    if not details:
        return {}
    out = {}
    for k, v in details.items():
        if k.lower() in SENSITIVE_KEYS:
            continue
        if isinstance(v, dict):
            out[k] = _sanitize_details(v)
        else:
            out[k] = v
    return out


class LogManager:
    """Persist account logs, ring buffer, and WebSocket broadcast per account."""

    RING_SIZE = 500

    def __init__(self, db):
        self.db = db
        self._ring: Deque[dict] = deque(maxlen=self.RING_SIZE)
        self._ws_clients: Dict[int, Set[Any]] = {}
        self._lock = asyncio.Lock()
        self.ensure_table()

    def ensure_table(self):
        try:
            conn = self.db.connect()
            cursor = conn.cursor()
            if self.db.is_sqlite:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        account_id INTEGER,
                        log_type TEXT NOT NULL,
                        level TEXT DEFAULT 'INFO',
                        message TEXT NOT NULL,
                        details TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_logs_account
                    ON account_logs(account_id, created_at)
                    """
                )
            else:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_logs (
                        id SERIAL PRIMARY KEY,
                        account_id INTEGER,
                        log_type TEXT NOT NULL,
                        level TEXT DEFAULT 'INFO',
                        message TEXT NOT NULL,
                        details TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_logs_account
                    ON account_logs(account_id, created_at DESC)
                    """
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to ensure account_logs table: {e}")

    def log(
        self,
        account_id: Optional[int],
        log_type: str,
        level: str,
        message: str,
        details: Optional[dict] = None,
    ) -> dict:
        """Write log entry (sync). Schedules WebSocket broadcast if loop is running."""
        entry = {
            "id": None,
            "account_id": account_id,
            "log_type": log_type,
            "level": level.upper(),
            "message": message,
            "details": _sanitize_details(details),
            "created_at": _utc_now(),
        }
        entry["id"] = self._insert_db(entry)
        self._ring.append(entry)
        self._schedule_broadcast(account_id or 0, entry)
        return entry

    async def alog(
        self,
        account_id: Optional[int],
        log_type: str,
        level: str,
        message: str,
        details: Optional[dict] = None,
    ) -> dict:
        entry = self.log(account_id, log_type, level, message, details)
        if account_id:
            await self._broadcast(account_id, entry)
        return entry

    def _insert_db(self, entry: dict) -> Optional[int]:
        try:
            conn = self.db.connect()
            cursor = conn.cursor()
            details_json = json.dumps(entry.get("details") or {})
            params = (
                entry.get("account_id"),
                entry["log_type"],
                entry["level"],
                entry["message"],
                details_json,
            )
            if self.db.is_postgresql:
                cursor.execute(
                    self.db.sql(
                        "INSERT INTO account_logs "
                        "(account_id, log_type, level, message, details) "
                        "VALUES (?, ?, ?, ?, ?) RETURNING id"
                    ),
                    params,
                )
                log_id = self.db.insert_returning_id(cursor, "account_logs")
            else:
                cursor.execute(
                    self.db.sql(
                        "INSERT INTO account_logs "
                        "(account_id, log_type, level, message, details) "
                        "VALUES (?, ?, ?, ?, ?)"
                    ),
                    params,
                )
                log_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return int(log_id) if log_id else None
        except Exception as e:
            logger.error(f"Failed to insert log: {e}")
            return None

    def get_logs(
        self,
        account_id: Optional[int] = None,
        limit: int = 50,
        log_type: Optional[str] = None,
        level: Optional[str] = None,
    ) -> List[dict]:
        try:
            conn = self.db.connect()
            cursor = conn.cursor()
            query = "SELECT * FROM account_logs WHERE 1=1"
            params: list = []
            if account_id is not None:
                query += " AND account_id = ?"
                params.append(account_id)
            if log_type:
                query += " AND log_type = ?"
                params.append(log_type)
            if level:
                query += " AND level = ?"
                params.append(level)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            cursor.execute(self.db.sql(query), tuple(params))
            rows = cursor.fetchall()
            conn.close()
            return [self._row_to_entry(r) for r in rows]
        except Exception as e:
            logger.error(f"get_logs failed: {e}")
            return []

    def get_recent(self, account_id: Optional[int] = None, limit: int = 20) -> List[dict]:
        if account_id is None:
            return list(self._ring)[-limit:]
        filtered = [e for e in self._ring if e.get("account_id") == account_id]
        return filtered[-limit:]

    def clear_logs(self, account_id: int) -> int:
        try:
            conn = self.db.connect()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql("DELETE FROM account_logs WHERE account_id = ?"),
                (account_id,),
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            self._ring = deque(
                (e for e in self._ring if e.get("account_id") != account_id),
                maxlen=self.RING_SIZE,
            )
            return deleted
        except Exception as e:
            logger.error(f"clear_logs failed: {e}")
            return 0

    def _row_to_entry(self, row) -> dict:
        if isinstance(row, dict):
            data = dict(row)
        else:
            data = dict(row)
        details = data.get("details")
        if isinstance(details, str) and details:
            try:
                data["details"] = json.loads(details)
            except json.JSONDecodeError:
                data["details"] = {}
        elif not details:
            data["details"] = {}
        return data

    def _schedule_broadcast(self, account_id: int, entry: dict):
        if not account_id:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(account_id, entry))
        except RuntimeError:
            pass

    async def register_ws(self, account_id: int, websocket):
        async with self._lock:
            if account_id not in self._ws_clients:
                self._ws_clients[account_id] = set()
            self._ws_clients[account_id].add(websocket)

    async def unregister_ws(self, account_id: int, websocket):
        async with self._lock:
            clients = self._ws_clients.get(account_id)
            if clients:
                clients.discard(websocket)
                if not clients:
                    self._ws_clients.pop(account_id, None)

    async def subscribe(self, account_id: int, websocket):
        """Keep WebSocket alive; push logs via _broadcast."""
        await self.register_ws(account_id, websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            await self.unregister_ws(account_id, websocket)

    async def _broadcast(self, account_id: int, entry: dict):
        async with self._lock:
            clients = set(self._ws_clients.get(account_id, set()))
        if not clients:
            return
        payload = {"type": "log", "data": entry}
        dead = set()
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                bucket = self._ws_clients.get(account_id)
                if bucket:
                    bucket -= dead

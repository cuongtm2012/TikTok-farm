# TikTok Farm - Proxy Manager Module
# Manages proxy list CRUD, health checks, and rotation

import csv
import json
import logging
import aiohttp
import asyncio
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class Proxy:
    """Represents a single proxy entry."""

    def __init__(
        self,
        proxy_id: int = 0,
        ip: str = "",
        port: int = 0,
        protocol: str = "http",
        username: str = "",
        password: str = "",
        status: str = "active",
        last_checked: Optional[str] = None,
        fail_count: int = 0,
    ):
        self.id = proxy_id
        self.ip = ip
        self.port = port
        self.protocol = protocol
        self.username = username
        self.password = password
        self.status = status
        self.last_checked = last_checked
        self.fail_count = fail_count

    @property
    def url(self) -> str:
        """Return proxy URL string for Playwright/requests."""
        auth = ""
        if self.username and self.password:
            auth = f"{self.username}:{self.password}@"
        return f"{self.protocol}://{auth}{self.ip}:{self.port}"

    @property
    def is_alive(self) -> bool:
        return self.status == "active"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ip": self.ip,
            "port": self.port,
            "protocol": self.protocol,
            "username": self.username,
            "password": self.password,
            "status": self.status,
            "last_checked": self.last_checked,
            "fail_count": self.fail_count,
            "url": self.url,
            "endpoint": f"{self.ip}:{self.port}",
        }


class ProxyManager:
    """Manages proxy lifecycle, health checks, and CSV/DB storage."""

    def __init__(self, csv_path: str = "config/proxies.csv", check_timeout: int = 5, max_fail: int = 3):
        self.csv_path = Path(csv_path)
        self.check_timeout = check_timeout
        self.max_fail = max_fail
        self._proxies: List[Proxy] = []
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # ---- CSV I/O ----

    def load_from_csv(self) -> List[Proxy]:
        """Load proxies from CSV file. Returns list of Proxy objects."""
        self._proxies = []
        if not self.csv_path.exists():
            logger.warning(f"Proxy CSV not found at {self.csv_path}. Creating empty.")
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self.csv_path.write_text("ip,port,protocol,username,password,status\n")
            return self._proxies

        try:
            with open(self.csv_path, "r") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    # Skip comment lines or empty rows
                    ip = row.get("ip", "").strip()
                    if not ip or ip.startswith("#"):
                        continue
                    proxy = Proxy(
                        proxy_id=i + 1,
                        ip=ip,
                        port=int(row.get("port", 0)),
                        protocol=row.get("protocol", "http").strip(),
                        username=row.get("username", "").strip(),
                        password=row.get("password", "").strip(),
                        status=row.get("status", "active").strip(),
                    )
                    self._proxies.append(proxy)
            logger.info(f"Loaded {len(self._proxies)} proxies from CSV")
        except Exception as e:
            logger.error(f"Failed to load proxies from CSV: {e}")

        return self._proxies

    def save_to_csv(self):
        """Save current proxy list back to CSV."""
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.csv_path, "w", newline="") as f:
                fieldnames = ["ip", "port", "protocol", "username", "password", "status"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for p in self._proxies:
                    writer.writerow({
                        "ip": p.ip,
                        "port": p.port,
                        "protocol": p.protocol,
                        "username": p.username,
                        "password": p.password,
                        "status": p.status,
                    })
            logger.debug(f"Saved {len(self._proxies)} proxies to CSV")
        except Exception as e:
            logger.error(f"Failed to save proxies to CSV: {e}")

    # ---- CRUD ----

    def _reindex_proxy_ids(self):
        for i, p in enumerate(self._proxies):
            p.id = i + 1

    def import_proxies_bulk(
        self,
        rows: List[Dict],
        merge: bool = True,
    ) -> Dict:
        """Import proxies from parsed CSV rows. merge=True appends; False replaces all."""
        result = {"imported": 0, "skipped": 0, "failed": 0, "errors": []}

        if not merge:
            self._proxies = []

        existing = {(p.ip, p.port) for p in self._proxies}

        for i, raw in enumerate(rows, start=1):
            ip = (raw.get("ip") or "").strip()
            if not ip or ip.startswith("#"):
                result["failed"] += 1
                result["errors"].append({"row": i, "error": "missing ip"})
                continue

            try:
                port = int(raw.get("port") or 0)
            except (TypeError, ValueError):
                result["failed"] += 1
                result["errors"].append({"row": i, "error": "invalid port"})
                continue

            if port <= 0:
                result["failed"] += 1
                result["errors"].append({"row": i, "error": "invalid port"})
                continue

            key = (ip, port)
            if key in existing:
                result["skipped"] += 1
                continue

            proxy = Proxy(
                proxy_id=0,
                ip=ip,
                port=port,
                protocol=(raw.get("protocol") or "http").strip() or "http",
                username=(raw.get("username") or "").strip(),
                password=(raw.get("password") or "").strip(),
                status=(raw.get("status") or "active").strip() or "active",
            )
            self._proxies.append(proxy)
            existing.add(key)
            result["imported"] += 1

        self._reindex_proxy_ids()
        self.save_to_csv()
        logger.info(
            f"Bulk proxy import: {result['imported']} imported, "
            f"{result['skipped']} skipped, total {len(self._proxies)}"
        )
        return result

    def add_proxy(self, proxy: Proxy) -> Proxy:
        """Add a new proxy. Returns the added proxy with assigned ID."""
        key = (proxy.ip, proxy.port)
        for p in self._proxies:
            if (p.ip, p.port) == key:
                return p
        proxy.id = len(self._proxies) + 1
        self._proxies.append(proxy)
        self.save_to_csv()
        logger.info(f"Added proxy {proxy.ip}:{proxy.port}")
        return proxy

    def remove_proxy(self, proxy_id: int) -> bool:
        """Remove a proxy by ID. Returns True if found and removed."""
        for i, p in enumerate(self._proxies):
            if p.id == proxy_id:
                self._proxies.pop(i)
                self._reindex_proxy_ids()
                self.save_to_csv()
                logger.info(f"Removed proxy ID {proxy_id}")
                return True
        logger.warning(f"Proxy ID {proxy_id} not found for removal")
        return False

    def update_proxy(self, proxy_id: int, **kwargs) -> Optional[Proxy]:
        """Update proxy fields. Returns updated Proxy or None."""
        for p in self._proxies:
            if p.id == proxy_id:
                for key, value in kwargs.items():
                    if hasattr(p, key):
                        setattr(p, key, value)
                self.save_to_csv()
                logger.info(f"Updated proxy ID {proxy_id}")
                return p
        logger.warning(f"Proxy ID {proxy_id} not found for update")
        return None

    def get_proxy(self, proxy_id: int) -> Optional[Proxy]:
        """Get a proxy by ID."""
        for p in self._proxies:
            if p.id == proxy_id:
                return p
        return None

    def get_all_proxies(self) -> List[Proxy]:
        """Get all proxies."""
        return self._proxies

    def get_alive_proxies(self) -> List[Proxy]:
        """Get proxies with active status."""
        return [p for p in self._proxies if p.status == "active"]

    def get_random_proxy(self) -> Optional[Proxy]:
        """Get a random alive proxy."""
        import random
        alive = self.get_alive_proxies()
        return random.choice(alive) if alive else None

    def get_proxy_by_db_id(self, proxy_id: int) -> Optional[Proxy]:
        """Match proxy by CSV/DB id (1-based index in loaded list)."""
        for p in self._proxies:
            if p.id == proxy_id:
                return p
        return None

    async def ensure_proxy_for_account(self, account, account_manager) -> tuple:
        """Check proxy before browser use; rotate to another alive proxy if dead.

        Returns:
            (proxy_url or None, proxy_id or None)
        """
        proxy_id = account.proxy_id or 0
        proxy = self.get_proxy_by_db_id(proxy_id) if proxy_id else None

        if proxy:
            alive = await self.check_proxy(proxy)
            self.save_to_csv()
            if alive:
                return proxy.url, proxy.id
            logger.warning(
                f"Proxy {proxy.ip}:{proxy.port} dead for account {account.id}, rotating"
            )
        else:
            logger.warning(f"Account {account.id} has no proxy, assigning one")

        new_id = await self.rotate_proxy_for_account(account.id, account_manager, exclude_id=proxy_id)
        if not new_id:
            return None, None
        new_proxy = self.get_proxy_by_db_id(new_id)
        return (new_proxy.url, new_id) if new_proxy else (None, None)

    async def rotate_proxy_for_account(
        self,
        account_id: int,
        account_manager,
        exclude_id: int = 0,
    ) -> Optional[int]:
        """Assign next alive proxy to account. Returns new proxy_id."""
        candidates = [
            p for p in self.get_alive_proxies() if p.id != exclude_id
        ]
        if not candidates:
            dead = [p for p in self._proxies if p.id != exclude_id and p.status != "dead"]
            for p in dead:
                if await self.check_proxy(p):
                    candidates.append(p)
            self.save_to_csv()

        if not candidates:
            logger.error(f"No alive proxy available for account {account_id}")
            account_manager.add_alert(
                account_id, "proxy_fail", "No alive proxy available after rotation"
            )
            return None

        import random
        chosen = random.choice(candidates)
        account_manager.assign_proxy(account_id, chosen.id)
        logger.info(
            f"Rotated account {account_id} to proxy {chosen.ip}:{chosen.port} (id={chosen.id})"
        )
        return chosen.id

    # ---- Health Checks ----

    async def check_proxy(self, proxy: Proxy) -> bool:
        """Check if a proxy is alive by making a request to a test URL."""
        url = "http://httpbin.org/ip"
        proxy_url = proxy.url

        try:
            session = await self._get_session()
            async with session.get(
                url,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=self.check_timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.debug(f"Proxy {proxy.ip}:{proxy.port} alive. IP: {data.get('origin', 'unknown')}")
                    proxy.status = "active"
                    proxy.fail_count = 0
                    proxy.last_checked = datetime.now().isoformat()
                    return True
                else:
                    logger.warning(f"Proxy {proxy.ip}:{proxy.port} returned status {resp.status}")
                    proxy.fail_count += 1
                    proxy.last_checked = datetime.now().isoformat()
                    if proxy.fail_count >= self.max_fail:
                        proxy.status = "dead"
                        logger.warning(f"Proxy {proxy.ip}:{proxy.port} marked as dead after {proxy.fail_count} failures")
                    return False
        except asyncio.TimeoutError:
            logger.warning(f"Proxy {proxy.ip}:{proxy.port} timed out")
            proxy.fail_count += 1
            proxy.last_checked = datetime.now().isoformat()
            if proxy.fail_count >= self.max_fail:
                proxy.status = "dead"
            return False
        except Exception as e:
            logger.error(f"Proxy {proxy.ip}:{proxy.port} check failed: {e}")
            proxy.fail_count += 1
            proxy.last_checked = datetime.now().isoformat()
            if proxy.fail_count >= self.max_fail:
                proxy.status = "dead"
            return False

    async def check_all_proxies(self) -> Dict[str, int]:
        """Check all proxies concurrently. Returns summary counts."""
        tasks = [self.check_proxy(p) for p in self._proxies if p.status != "dead"]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        alive = sum(1 for r in results if r is True)
        dead = sum(1 for r in results if r is False)
        errors = sum(1 for r in results if isinstance(r, Exception))

        self.save_to_csv()
        logger.info(f"Proxy health check complete: {alive} alive, {dead} dead, {errors} errors")
        return {"alive": alive, "dead": dead, "errors": errors}

    def sync_proxies_to_db(self, db):
        """Sync current proxy list to database (SQLite or PostgreSQL)."""
        try:
            conn = db.connect()
            cursor = conn.cursor()

            if db.is_sqlite:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS proxies (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ip TEXT NOT NULL,
                        port INTEGER NOT NULL,
                        protocol TEXT DEFAULT 'http',
                        username TEXT,
                        password TEXT,
                        status TEXT DEFAULT 'active',
                        last_checked TIMESTAMP,
                        fail_count INTEGER DEFAULT 0
                    )
                """)

            cursor.execute(db.sql("DELETE FROM proxies"))
            insert_sql = db.sql(
                "INSERT INTO proxies (ip, port, protocol, username, password, status, last_checked, fail_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            )
            for p in self._proxies:
                cursor.execute(
                    insert_sql,
                    (
                        p.ip,
                        p.port,
                        p.protocol,
                        p.username,
                        p.password,
                        p.status,
                        p.last_checked,
                        p.fail_count,
                    ),
                )
            conn.commit()
            conn.close()
            logger.info(f"Synced {len(self._proxies)} proxies to DB ({db.driver})")
        except Exception as e:
            logger.error(f"Failed to sync proxies to DB: {e}")

    # ---- Cleanup ----

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    @classmethod
    def from_settings(cls, settings: dict) -> "ProxyManager":
        """Create instance from settings dict."""
        proxy_config = settings.get("proxies", {})
        return cls(
            csv_path=proxy_config.get("csv_path", "config/proxies.csv"),
            check_timeout=proxy_config.get("check_timeout", 5),
            max_fail=proxy_config.get("max_fail_before_disable", 3),
        )

# TikTok Farm - Proxy Manager Module
# Manages proxy list CRUD, health checks, and rotation

import csv
import json
import logging
import aiohttp
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Set, Tuple
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

    def to_dict(self, used_by: Optional[List[str]] = None) -> dict:
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
            "used_by": used_by or [],
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
        """Match proxy by database id."""
        for p in self._proxies:
            if p.id == proxy_id:
                return p
        return None

    def _pick_live_replacement(
        self,
        current: Optional[Proxy],
        taken_live_ids: Set[int],
    ) -> Optional[Proxy]:
        """Prefer active endpoint on same IP; HTTP before SOCKS5; spread across IPs."""
        live = self.get_alive_proxies()
        if not live:
            return None

        def sort_key(p: Proxy) -> Tuple:
            proto = (p.protocol or "http").lower()
            return (0 if proto == "http" else 1, 1 if p.id in taken_live_ids else 0, p.id)

        if current:
            same_ip = [p for p in live if p.ip == current.ip]
            if same_ip:
                return sorted(same_ip, key=sort_key)[0]

        return sorted(live, key=sort_key)[0]

    def rebalance_accounts_to_live_proxies(self, db, account_manager) -> Dict[str, int]:
        """Move accounts off inactive/dead proxies onto live endpoints (same IP when possible)."""
        stats = {"reassigned": 0, "unchanged": 0, "no_live_proxy": 0}
        self.apply_db_ids_from_db(db)
        live = self.get_alive_proxies()
        if not live:
            stats["no_live_proxy"] = 1
            return stats

        live_ids = {p.id for p in live}
        proxy_by_id = {p.id: p for p in self._proxies}
        taken_live_ids: Set[int] = set()

        try:
            conn = db.connect()
            cursor = conn.cursor()
            cursor.execute(db.sql("SELECT id, username, proxy_id FROM accounts ORDER BY id"))
            rows = cursor.fetchall()
            conn.close()
        except Exception as e:
            logger.error(f"rebalance_accounts_to_live_proxies: {e}")
            return stats

        for row in rows:
            r = dict(row) if hasattr(row, "keys") else {
                "id": row[0], "username": row[1], "proxy_id": row[2],
            }
            aid = int(r["id"])
            pid = int(r.get("proxy_id") or 0)
            current = proxy_by_id.get(pid) if pid else None

            if current and pid in live_ids and (current.status or "") == "active":
                taken_live_ids.add(pid)
                stats["unchanged"] += 1
                continue

            replacement = self._pick_live_replacement(current, taken_live_ids)
            if not replacement:
                stats["no_live_proxy"] += 1
                continue

            if replacement.id != pid:
                account_manager.assign_proxy(aid, replacement.id)
                taken_live_ids.add(replacement.id)
                stats["reassigned"] += 1
                logger.info(
                    f"Rebalanced account {aid} ({r.get('username')}) "
                    f"proxy {pid} -> {replacement.id} ({replacement.ip}:{replacement.port} {replacement.protocol})"
                )
            else:
                taken_live_ids.add(replacement.id)
                stats["unchanged"] += 1

        return stats

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

    @staticmethod
    def _endpoint_key(ip: str, port: int) -> tuple:
        return (ip.strip(), int(port))

    async def check_proxy(
        self, proxy: Proxy, test_url: str = "https://www.tiktok.com"
    ) -> bool:
        """Real HTTP check via proxy (SPEC v5 — TikTok reachability)."""
        proxy_url = proxy.url
        try:
            session = await self._get_session()
            async with session.get(
                test_url,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=self.check_timeout),
                allow_redirects=True,
            ) as resp:
                proxy.last_checked = datetime.now().isoformat()
                if resp.status == 200:
                    proxy.status = "active"
                    proxy.fail_count = 0
                    return True
                logger.warning(
                    f"Proxy {proxy.ip}:{proxy.port} returned HTTP {resp.status}"
                )
                proxy.fail_count += 1
                proxy.status = "inactive"
                if proxy.fail_count >= self.max_fail:
                    proxy.status = "dead"
                return False
        except asyncio.TimeoutError:
            logger.warning(f"Proxy {proxy.ip}:{proxy.port} timed out")
            proxy.fail_count += 1
            proxy.last_checked = datetime.now().isoformat()
            proxy.status = "inactive"
            if proxy.fail_count >= self.max_fail:
                proxy.status = "dead"
            return False
        except Exception as e:
            logger.error(f"Proxy {proxy.ip}:{proxy.port} check failed: {e}")
            proxy.fail_count += 1
            proxy.last_checked = datetime.now().isoformat()
            proxy.status = "inactive"
            if proxy.fail_count >= self.max_fail:
                proxy.status = "dead"
            return False

    async def check_all_proxies(self) -> Dict[str, int]:
        """Check all proxies in parallel and persist status to CSV."""
        if not self._proxies:
            self.load_from_csv()
        tasks = [self.check_proxy(p) for p in self._proxies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        alive = sum(1 for r in results if r is True)
        dead = sum(1 for r in results if r is False)
        errors = sum(1 for r in results if isinstance(r, Exception))

        self.save_to_csv()
        logger.info(f"Proxy health check: {alive} alive, {dead} dead, {errors} errors")
        return {"alive": alive, "dead": dead, "errors": errors, "inactive": dead}

    def apply_db_ids_from_db(self, db) -> int:
        """Align in-memory proxy.id (and status) with database rows."""
        db_by_key = self._load_proxies_from_db(db)
        matched = 0
        for p in self._proxies:
            row = db_by_key.get(self._endpoint_key(p.ip, p.port))
            if not row:
                continue
            p.id = int(row["id"])
            if row.get("status"):
                p.status = row["status"]
            if row.get("fail_count") is not None:
                p.fail_count = int(row["fail_count"] or 0)
            matched += 1
        return matched

    def _load_proxies_from_db(self, db) -> Dict[tuple, dict]:
        """Return {(ip,port): row} from DB."""
        out = {}
        try:
            conn = db.connect()
            cursor = conn.cursor()
            cursor.execute(db.sql("SELECT * FROM proxies"))
            for row in cursor.fetchall():
                r = dict(row) if hasattr(row, "keys") else {
                    "id": row[0], "ip": row[1], "port": row[2],
                }
                key = self._endpoint_key(r["ip"], r["port"])
                out[key] = r
        except Exception as e:
            logger.error(f"load proxies from DB: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return out

    def _accounts_by_proxy_id(self, db) -> Dict[int, List[str]]:
        usage: Dict[int, List[str]] = {}
        try:
            conn = db.connect()
            cursor = conn.cursor()
            cursor.execute(
                db.sql(
                    "SELECT id, username, proxy_id FROM accounts WHERE proxy_id IS NOT NULL AND proxy_id > 0"
                )
            )
            for row in cursor.fetchall():
                r = dict(row) if hasattr(row, "keys") else row
                pid = r.get("proxy_id") or r[2]
                if not pid:
                    continue
                usage.setdefault(int(pid), []).append(r.get("username") or f"#{r.get('id')}")
            conn.close()
        except Exception as e:
            logger.error(f"accounts_by_proxy: {e}")
        return usage

    def get_usage_map(self, db) -> Dict[int, List[str]]:
        return self._accounts_by_proxy_id(db)

    def pick_proxy_for_import(
        self, db, reserved: Optional[Dict[int, int]] = None
    ) -> Optional[Proxy]:
        """
        Pick an alive proxy with the fewest accounts (spread load, prefer empty slots).
        reserved: in-batch counts {proxy_id: n} for multi-line seller import.
        """
        alive = self.get_alive_proxies()
        if not alive:
            return None
        usage = self.get_usage_map(db)
        reserved = reserved or {}

        def load(p: Proxy) -> int:
            return len(usage.get(p.id, [])) + int(reserved.get(p.id, 0))

        empty = [p for p in alive if load(p) == 0]
        if empty:
            return sorted(empty, key=lambda p: p.id)[0]
        return min(alive, key=lambda p: (load(p), p.id))

    def assign_proxies_for_import_items(self, db, items: List[dict]) -> None:
        """Set proxy_id on import rows with proxy_id=0 using least-loaded alive proxies."""
        reserved: Dict[int, int] = {}
        for item in items:
            if int(item.get("proxy_id") or 0) != 0:
                continue
            proxy = self.pick_proxy_for_import(db, reserved)
            if not proxy:
                continue
            item["proxy_id"] = proxy.id
            reserved[proxy.id] = reserved.get(proxy.id, 0) + 1

    def sync_proxies_to_db(self, db, account_manager=None) -> Dict[str, int]:
        """Sync proxies.csv → DB; remove orphans; migrate accounts off deprecated proxies."""
        stats = {
            "inserted": 0,
            "updated": 0,
            "deleted": 0,
            "deprecated": 0,
            "accounts_migrated": 0,
        }
        self.load_from_csv()
        csv_keys = {self._endpoint_key(p.ip, p.port): p for p in self._proxies}

        try:
            conn = db.connect()
            cursor = conn.cursor()

            if db.is_sqlite:
                cursor.execute(
                    """
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
                    """
                )

            cursor.execute(db.sql("SELECT id, ip, port, protocol FROM proxies"))
            db_rows = []
            for row in cursor.fetchall():
                db_rows.append(dict(row) if hasattr(row, "keys") else {
                    "id": row[0], "ip": row[1], "port": row[2], "protocol": row[3],
                })

            db_by_key = {self._endpoint_key(r["ip"], r["port"]): r for r in db_rows}
            csv_key_set = set(csv_keys.keys())
            default_replacement = self._proxies[0].id if self._proxies else None

            # Upsert CSV proxies
            for key, p in csv_keys.items():
                if key in db_by_key:
                    rid = db_by_key[key]["id"]
                    cursor.execute(
                        db.sql(
                            "UPDATE proxies SET protocol=?, username=?, password=?, "
                            "status=?, last_checked=?, fail_count=? WHERE id=?"
                        ),
                        (
                            p.protocol, p.username, p.password, p.status or "active",
                            p.last_checked, p.fail_count, rid,
                        ),
                    )
                    p.id = rid
                    stats["updated"] += 1
                else:
                    cursor.execute(
                        db.sql(
                            "INSERT INTO proxies (ip, port, protocol, username, password, "
                            "status, last_checked, fail_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                        ),
                        (
                            p.ip, p.port, p.protocol, p.username, p.password,
                            p.status or "active", p.last_checked, p.fail_count,
                        ),
                    )
                    p.id = db.insert_returning_id(cursor, "proxies")
                    stats["inserted"] += 1

            # Handle DB proxies not in CSV
            for key, row in db_by_key.items():
                if key in csv_key_set:
                    continue
                pid = row["id"]
                cursor.execute(
                    db.sql("SELECT COUNT(*) as c FROM accounts WHERE proxy_id = ?"),
                    (pid,),
                )
                count_row = cursor.fetchone()
                in_use = (count_row["c"] if hasattr(count_row, "keys") else count_row[0]) or 0

                replacement_id = None
                if self._proxies:
                    same_ip = [
                        px for px in self._proxies
                        if px.ip == row["ip"] and self._endpoint_key(px.ip, px.port) in csv_key_set
                    ]
                    replacement_id = same_ip[0].id if same_ip else default_replacement

                if in_use and replacement_id and account_manager:
                    cursor.execute(
                        db.sql("UPDATE accounts SET proxy_id = ? WHERE proxy_id = ?"),
                        (replacement_id, pid),
                    )
                    stats["accounts_migrated"] += in_use
                    cursor.execute(
                        db.sql("UPDATE proxies SET status = 'deprecated' WHERE id = ?"),
                        (pid,),
                    )
                    stats["deprecated"] += 1
                else:
                    cursor.execute(db.sql("DELETE FROM proxies WHERE id = ?"), (pid,))
                    stats["deleted"] += 1

            conn.commit()
            conn.close()
            self.save_to_csv()
            logger.info(f"Proxy sync: {stats}")
        except Exception as e:
            logger.error(f"sync_proxies_to_db failed: {e}")

        if account_manager:
            reb = self.rebalance_accounts_to_live_proxies(db, account_manager)
            stats["accounts_rebalanced"] = reb.get("reassigned", 0)
            stats["rebalance"] = reb

        return stats

    def reload_from_csv_and_sync(self, db, account_manager=None) -> Dict[str, int]:
        """Load CSV then sync to DB (API /api/proxies/sync)."""
        self.load_from_csv()
        return self.sync_proxies_to_db(db, account_manager=account_manager)

    def persist_proxy_status_to_db(self, db):
        """Write current in-memory proxy health fields back to DB."""
        try:
            conn = db.connect()
            cursor = conn.cursor()
            for p in self._proxies:
                if not p.id:
                    continue
                cursor.execute(
                    db.sql(
                        "UPDATE proxies SET status=?, last_checked=?, fail_count=? WHERE id=?"
                    ),
                    (p.status, p.last_checked, p.fail_count, p.id),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"persist_proxy_status_to_db: {e}")

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

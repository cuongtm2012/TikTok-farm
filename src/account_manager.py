# TikTok Farm - Account Manager Module
# CRUD for TikTok accounts with status management (SQLite or PostgreSQL)

import json
import logging
import yaml
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta
import random

from src.database import Database

logger = logging.getLogger(__name__)


class Account:
    """Represents a single TikTok account."""

    def __init__(
        self,
        account_id: int = 0,
        username: str = "",
        proxy_id: int = 0,
        status: str = "pending",
        followers: int = 0,
        following: int = 0,
        total_posts: int = 0,
        total_views: int = 0,
        created_at: Optional[str] = None,
        last_active: Optional[str] = None,
        cookie_data: Optional[str] = None,
        password: str = "",
        notes: str = "",
    ):
        self.id = account_id
        self.username = username
        self.proxy_id = proxy_id
        self.password = password
        self.status = status
        self.followers = followers
        self.following = following
        self.total_posts = total_posts
        self.total_views = total_views
        self.created_at = created_at or datetime.now().isoformat()
        self.last_active = last_active
        self.cookie_data = cookie_data
        self.notes = notes

    @property
    def is_active(self) -> bool:
        return self.status in ("active", "warming")

    @property
    def days_since_creation(self) -> int:
        if not self.created_at:
            return 0
        try:
            created = datetime.fromisoformat(self.created_at)
            return (datetime.now() - created).days
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def cookie_status_from_data(cookie_data: Optional[str]) -> dict:
        """Build cookie_status dict from cookie_data JSON string."""
        has_cookies = False
        cookie_count = 0
        expired = False
        has_sessionid = False

        if cookie_data:
            try:
                cookies = json.loads(cookie_data)
                if isinstance(cookies, list) and len(cookies) > 0:
                    has_cookies = True
                    cookie_count = len(cookies)
                    has_sessionid = any(
                        c.get("name") == "sessionid" for c in cookies if isinstance(c, dict)
                    )
                    expired = not has_sessionid
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "has_cookies": has_cookies,
            "cookie_count": cookie_count,
            "expired": expired,
            "has_sessionid": has_sessionid,
        }

    def to_dict(self, include_cookie_payload: bool = False) -> dict:
        data = {
            "id": self.id,
            "username": self.username,
            "proxy_id": self.proxy_id,
            "status": self.status,
            "followers": self.followers,
            "following": self.following,
            "total_posts": self.total_posts,
            "total_views": self.total_views,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "notes": self.notes,
            "cookie_status": self.cookie_status_from_data(self.cookie_data),
        }
        if include_cookie_payload and self.cookie_data:
            try:
                data["cookies"] = json.loads(self.cookie_data)
            except Exception:
                data["cookies"] = []
        return data


class AccountManager:
    """Manages TikTok accounts with SQLite or PostgreSQL persistence."""

    def __init__(self, db: Optional[Database] = None, db_path: str = "data/farm.db"):
        if db is None:
            db = Database(driver="sqlite", path=db_path)
        self.db = db
        self._init_db()

    def _get_conn(self):
        return self.db.connect()

    def _init_db(self):
        """Initialize database schema (SQLite only; Postgres uses docker/init SQL)."""
        if self.db.is_postgresql:
            if self.db.ping():
                logger.info("PostgreSQL connection OK (schema from docker/init)")
            else:
                raise ConnectionError(
                    "Cannot connect to PostgreSQL. Start DB: docker compose up -d"
                )
            return

        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    proxy_id INTEGER REFERENCES proxies(id),
                    status TEXT DEFAULT 'pending',
                    followers INTEGER DEFAULT 0,
                    following INTEGER DEFAULT 0,
                    total_posts INTEGER DEFAULT 0,
                    total_views INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP,
                    cookie_data TEXT,
                    password TEXT,
                    notes TEXT
                );

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
                );

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER REFERENCES accounts(id),
                    tiktok_post_id TEXT,
                    content_path TEXT,
                    caption TEXT,
                    hashtags TEXT,
                    affiliate_link TEXT,
                    status TEXT DEFAULT 'pending',
                    views INTEGER DEFAULT 0,
                    likes INTEGER DEFAULT 0,
                    comments INTEGER DEFAULT 0,
                    shares INTEGER DEFAULT 0,
                    scheduled_at TIMESTAMP,
                    posted_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS farm_activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER REFERENCES accounts(id),
                    activity_type TEXT,
                    duration_seconds INTEGER,
                    actions_count INTEGER,
                    performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER REFERENCES accounts(id),
                    alert_type TEXT,
                    message TEXT,
                    resolved INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            try:
                cursor.execute("ALTER TABLE accounts ADD COLUMN password TEXT")
                conn.commit()
            except Exception:
                pass

            conn.commit()
            conn.close()
            logger.info("SQLite database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    # ---- CRUD Accounts ----

    def assign_proxy(self, account_id: int, proxy_id: int) -> bool:
        result = self.update_account(account_id, proxy_id=proxy_id)
        return result is not None

    def save_cookies(self, account_id: int, cookies) -> bool:
        try:
            payload = json.dumps(cookies) if isinstance(cookies, list) else str(cookies)
            return self.update_account(account_id, cookie_data=payload) is not None
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")
            return False

    def save_cookies_from_string(self, account_id: int, cookie_input) -> Tuple[bool, List]:
        """
        Parse cookie string or JSON/list and save to DB.
        Returns (success, cookies_list).
        """
        from src.import_utils import parse_cookie_string

        cookies: list = []
        if isinstance(cookie_input, list):
            cookies = cookie_input
        elif isinstance(cookie_input, str) and cookie_input.strip():
            text = cookie_input.strip()
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        cookies = parsed
                except json.JSONDecodeError:
                    cookies = parse_cookie_string(text)
            else:
                cookies = parse_cookie_string(text)

        if not cookies:
            return False, []

        ok = self.save_cookies(account_id, cookies)
        return ok, cookies

    def clear_cookies(self, account_id: int) -> bool:
        return self.update_account(account_id, cookie_data="") is not None

    def import_accounts_bulk(
        self,
        items: List[Dict],
        skip_existing: bool = True,
        require_cookies: bool = False,
        default_status_with_cookies: str = "",
        browser_manager=None,
    ) -> Dict:
        """Import many accounts from parsed CSV/JSON rows."""
        result = {
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "without_cookies": 0,
            "errors": [],
        }
        valid_statuses = {
            "pending", "warming", "active", "banned", "shadowbanned", "paused"
        }

        for i, raw in enumerate(items, start=1):
            username = (raw.get("username") or "").strip()
            if not username:
                result["failed"] += 1
                result["errors"].append({"row": i, "error": "missing username"})
                continue

            if skip_existing and self.get_account_by_username(username):
                result["skipped"] += 1
                continue

            cookie_raw = raw.get("cookie_data") or ""
            has_cookie_payload = bool(cookie_raw and cookie_raw.strip())
            if require_cookies and not has_cookie_payload:
                result["without_cookies"] += 1
                result["failed"] += 1
                result["errors"].append(
                    {"row": i, "username": username, "error": "missing cookies (required)"}
                )
                continue

            try:
                proxy_id = int(raw.get("proxy_id") or 0)
            except (TypeError, ValueError):
                proxy_id = 0

            acc = self.add_account(
                username=username,
                proxy_id=proxy_id,
                notes=raw.get("notes") or "",
                password=raw.get("password") or "",
            )
            if not acc:
                result["skipped"] += 1
                continue

            status = (raw.get("status") or "").strip().lower()
            if not status and has_cookie_payload and default_status_with_cookies:
                status = default_status_with_cookies
            if status and status in valid_statuses:
                self.set_status(acc.id, status)

            if cookie_raw:
                try:
                    if cookie_raw.strip().startswith("["):
                        cookies = json.loads(cookie_raw)
                    else:
                        from src.import_utils import parse_cookie_string
                        cookies = parse_cookie_string(cookie_raw)
                    if cookies:
                        self.save_cookies(acc.id, cookies)
                        result.setdefault("with_cookies", 0)
                        result["with_cookies"] += 1
                        if browser_manager:
                            browser_manager.write_storage_state_from_cookies(
                                acc.id, cookies
                            )
                    elif require_cookies:
                        result["failed"] += 1
                        result["errors"].append(
                            {"row": i, "username": username, "error": "invalid cookies"}
                        )
                        continue
                except Exception as e:
                    result["errors"].append(
                        {"row": i, "username": username, "error": f"cookies: {e}"}
                    )
                    if require_cookies:
                        result["failed"] += 1
                        continue

            result["imported"] += 1

        logger.info(
            f"Bulk account import: {result['imported']} imported, "
            f"{result['skipped']} skipped, {result['failed']} failed"
        )
        return result

    def load_accounts_from_yaml(self, yaml_path: str = "config/accounts.yaml") -> int:
        """Import accounts from YAML into DB (skip existing usernames)."""
        path = Path(yaml_path)
        if not path.exists():
            return 0
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
            items = data.get("accounts") or []
            imported = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                username = (item.get("username") or "").strip()
                if not username:
                    continue
                if self.get_account_by_username(username):
                    continue
                acc = self.add_account(
                    username=username,
                    proxy_id=int(item.get("proxy_id") or 0),
                    notes=item.get("notes") or "",
                    password=item.get("password") or "",
                )
                if acc:
                    status = item.get("status")
                    if status:
                        self.set_status(acc.id, status)
                    imported += 1
            logger.info(f"Imported {imported} accounts from {yaml_path}")
            return imported
        except Exception as e:
            logger.error(f"Failed to load accounts YAML: {e}")
            return 0

    def add_account(
        self,
        username: str,
        proxy_id: int = 0,
        notes: str = "",
        password: str = "",
    ) -> Optional[Account]:
        """Add a new TikTok account. Returns the created Account or None."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if self.db.is_postgresql:
                cursor.execute(
                    self.db.sql(
                        "INSERT INTO accounts (username, proxy_id, status, notes, password) "
                        "VALUES (?, ?, 'pending', ?, ?) RETURNING id"
                    ),
                    (username, proxy_id, notes, password),
                )
                account_id = self.db.insert_returning_id(cursor, "accounts")
            else:
                cursor.execute(
                    self.db.sql(
                        "INSERT INTO accounts (username, proxy_id, status, notes, password) "
                        "VALUES (?, ?, 'pending', ?, ?)"
                    ),
                    (username, proxy_id, notes, password),
                )
                account_id = cursor.lastrowid
            conn.commit()
            conn.close()
            logger.info(f"Added account {username} (ID: {account_id})")
            return self.get_account(account_id)
        except self.db.integrity_error:
            logger.error(f"Account {username} already exists")
            return None
        except Exception as e:
            logger.error(f"Failed to add account {username}: {e}")
            return None

    def get_account(self, account_id: int) -> Optional[Account]:
        """Get an account by ID."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql("SELECT * FROM accounts WHERE id = ?"),
                (account_id,),
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._row_to_account(row)
            return None
        except Exception as e:
            logger.error(f"Failed to get account {account_id}: {e}")
            return None

    def get_account_by_username(self, username: str) -> Optional[Account]:
        """Get an account by username."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql("SELECT * FROM accounts WHERE username = ?"),
                (username,),
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return self._row_to_account(row)
            return None
        except Exception as e:
            logger.error(f"Failed to get account by username {username}: {e}")
            return None

    def get_all_accounts(self, status: Optional[str] = None) -> List[Account]:
        """Get all accounts, optionally filtered by status."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    self.db.sql("SELECT * FROM accounts WHERE status = ? ORDER BY id"),
                    (status,),
                )
            else:
                cursor.execute(self.db.sql("SELECT * FROM accounts ORDER BY id"))
            rows = cursor.fetchall()
            conn.close()
            return [self._row_to_account(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to list accounts: {e}")
            return []

    def apply_tiktok_profile(self, account_id: int, profile: Dict) -> Optional[Account]:
        """Update account stats from normalized TikTok public profile."""
        return self.update_account(
            account_id,
            followers=profile.get("followers", 0),
            following=profile.get("following", 0),
            total_posts=profile.get("video_count", 0),
            last_active=datetime.now().isoformat(),
        )

    def update_account(self, account_id: int, **kwargs) -> Optional[Account]:
        """Update account fields. Returns updated Account or None."""
        allowed_fields = {
            "username", "proxy_id", "status", "followers", "following",
            "total_posts", "total_views", "last_active", "cookie_data", "password", "notes",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return self.get_account(account_id)

        try:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values())
            values.append(account_id)

            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql(f"UPDATE accounts SET {set_clause} WHERE id = ?"),
                values,
            )
            conn.commit()
            conn.close()
            logger.info(f"Updated account {account_id}: {updates}")
            return self.get_account(account_id)
        except Exception as e:
            logger.error(f"Failed to update account {account_id}: {e}")
            return None

    def delete_account(self, account_id: int) -> bool:
        """Delete an account by ID. Returns True if deleted."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql("DELETE FROM accounts WHERE id = ?"),
                (account_id,),
            )
            deleted = cursor.rowcount > 0
            conn.commit()
            conn.close()
            if deleted:
                logger.info(f"Deleted account {account_id}")
            else:
                logger.warning(f"Account {account_id} not found for deletion")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete account {account_id}: {e}")
            return False

    # ---- Status Management ----

    def set_status(self, account_id: int, status: str) -> bool:
        """Set account status. Validates status value."""
        valid_statuses = {"pending", "warming", "active", "banned", "shadowbanned", "paused"}
        if status not in valid_statuses:
            logger.error(f"Invalid status: {status}. Must be one of {valid_statuses}")
            return False
        result = self.update_account(account_id, status=status)
        return result is not None

    def get_accounts_for_warming(self) -> List[Account]:
        """Get accounts that need warming (status='pending' or status='warming')."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            self.db.sql(
                "SELECT * FROM accounts WHERE status IN ('pending', 'warming') ORDER BY created_at"
            )
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_account(r) for r in rows]

    def get_accounts_for_farming(self) -> List[Account]:
        """Get accounts eligible for farm sessions (active or warming)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            self.db.sql(
                "SELECT * FROM accounts WHERE status IN ('active', 'warming') "
                "ORDER BY last_active ASC"
            )
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_account(r) for r in rows]

    def get_accounts_for_posting(self) -> List[Account]:
        """Get accounts eligible for posting (active only)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            self.db.sql("SELECT * FROM accounts WHERE status = 'active' ORDER BY last_active ASC")
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_account(r) for r in rows]

    def mark_last_active(self, account_id: int):
        """Update the last_active timestamp to now."""
        self.update_account(account_id, last_active=datetime.now().isoformat())

    def complete_warming(self, account_id: int):
        """Mark an account as active after warming period (7 days)."""
        account = self.get_account(account_id)
        if account and account.status == "warming":
            if account.days_since_creation >= 7:
                self.set_status(account_id, "active")
                logger.info(f"Account {account.username} warmed up and set to active")
                return True
            logger.info(
                f"Account {account.username} still warming ({account.days_since_creation}/7 days)"
            )
        return False

    # ---- Activity Logging ----

    def log_activity(
        self, account_id: int, activity_type: str, duration_seconds: int, actions_count: int = 0
    ):
        """Log a farm activity."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql(
                    "INSERT INTO farm_activities "
                    "(account_id, activity_type, duration_seconds, actions_count) "
                    "VALUES (?, ?, ?, ?)"
                ),
                (account_id, activity_type, duration_seconds, actions_count),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to log activity: {e}")

    def get_recent_activities(self, account_id: int, limit: int = 20) -> List[Dict]:
        """Get recent activities for an account."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql(
                    "SELECT * FROM farm_activities WHERE account_id = ? "
                    "ORDER BY performed_at DESC LIMIT ?"
                ),
                (account_id, limit),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get activities: {e}")
            return []

    # ---- Alerts ----

    def add_alert(self, account_id: int, alert_type: str, message: str) -> bool:
        """Add an alert for an account."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql(
                    "INSERT INTO alerts (account_id, alert_type, message) VALUES (?, ?, ?)"
                ),
                (account_id, alert_type, message),
            )
            conn.commit()
            conn.close()
            logger.warning(f"Alert [{alert_type}] for account {account_id}: {message}")
            return True
        except Exception as e:
            logger.error(f"Failed to add alert: {e}")
            return False

    def get_alerts(self, resolved: Optional[bool] = None, limit: int = 50) -> List[Dict]:
        """List alerts, optionally filter by resolved status."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if resolved is None:
                cursor.execute(
                    self.db.sql(
                        "SELECT a.*, acc.username FROM alerts a "
                        "LEFT JOIN accounts acc ON a.account_id = acc.id "
                        "ORDER BY a.created_at DESC LIMIT ?"
                    ),
                    (limit,),
                )
            else:
                flag = "TRUE" if resolved else "FALSE"
                if self.db.is_sqlite:
                    flag = "1" if resolved else "0"
                cursor.execute(
                    self.db.sql(
                        f"SELECT a.*, acc.username FROM alerts a "
                        f"LEFT JOIN accounts acc ON a.account_id = acc.id "
                        f"WHERE a.resolved = {flag} ORDER BY a.created_at DESC LIMIT ?"
                    ),
                    (limit,),
                )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get alerts: {e}")
            return []

    def get_unresolved_alerts(self, limit: int = 50) -> List[Dict]:
        """Get unresolved alerts."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            resolved = "FALSE" if self.db.is_postgresql else "0"
            cursor.execute(
                self.db.sql(
                    f"SELECT a.*, acc.username FROM alerts a "
                    f"LEFT JOIN accounts acc ON a.account_id = acc.id "
                    f"WHERE a.resolved = {resolved} ORDER BY a.created_at DESC LIMIT ?"
                ),
                (limit,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get alerts: {e}")
            return []

    def resolve_alert(self, alert_id: int) -> bool:
        """Mark an alert as resolved."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            resolved = "TRUE" if self.db.is_postgresql else "1"
            cursor.execute(
                self.db.sql(f"UPDATE alerts SET resolved = {resolved} WHERE id = ?"),
                (alert_id,),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to resolve alert {alert_id}: {e}")
            return False

    # ---- Posts ----

    def mark_post_posted(
        self,
        post_id: int,
        tiktok_post_id: str = "",
        views: int = 0,
    ) -> bool:
        return self.update_post(
            post_id,
            status="posted",
            tiktok_post_id=tiktok_post_id or None,
            posted_at=datetime.now().isoformat(),
            views=views,
        )

    def add_post(
        self,
        account_id: int,
        content_path: str,
        caption: str = "",
        hashtags: str = "",
        affiliate_link: str = "",
        scheduled_at: Optional[str] = None,
    ) -> Optional[int]:
        """Create a post record. Returns post ID."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            params = (account_id, content_path, caption, hashtags, affiliate_link, scheduled_at)
            if self.db.is_postgresql:
                cursor.execute(
                    self.db.sql(
                        "INSERT INTO posts "
                        "(account_id, content_path, caption, hashtags, affiliate_link, scheduled_at) "
                        "VALUES (?, ?, ?, ?, ?, ?) RETURNING id"
                    ),
                    params,
                )
                post_id = self.db.insert_returning_id(cursor, "posts")
            else:
                cursor.execute(
                    self.db.sql(
                        "INSERT INTO posts "
                        "(account_id, content_path, caption, hashtags, affiliate_link, scheduled_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)"
                    ),
                    params,
                )
                post_id = cursor.lastrowid
            conn.commit()
            conn.close()
            logger.info(f"Created post record {post_id} for account {account_id}")
            return post_id
        except Exception as e:
            logger.error(f"Failed to create post: {e}")
            return None

    def update_post(self, post_id: int, **kwargs) -> bool:
        """Update post fields."""
        allowed = {"tiktok_post_id", "status", "views", "likes", "comments", "shares", "posted_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        try:
            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values())
            values.append(post_id)
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql(f"UPDATE posts SET {set_clause} WHERE id = ?"),
                values,
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to update post {post_id}: {e}")
            return False

    def get_pending_posts(self, limit: int = 10) -> List[Dict]:
        """Get posts scheduled for posting."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                self.db.sql(
                    "SELECT * FROM posts WHERE status = 'pending' "
                    "ORDER BY scheduled_at ASC LIMIT ?"
                ),
                (limit,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get pending posts: {e}")
            return []

    # ---- Stats ----

    def _scalar(self, row, key: str = "count", index: int = 0):
        if row is None:
            return 0
        if isinstance(row, dict):
            if key in row:
                return row[key] or 0
            vals = list(row.values())
            return vals[index] if vals else 0
        return row[index] if row[index] is not None else 0

    def get_performance_stats(self) -> Dict:
        """Aggregate performance stats across all accounts."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute(
                self.db.sql(
                    "SELECT COUNT(*) as total, SUM(total_posts) as posts, "
                    "SUM(total_views) as views FROM accounts"
                )
            )
            row = cursor.fetchone()

            cursor.execute(
                self.db.sql("SELECT COUNT(*) as count FROM posts WHERE status = 'posted'")
            )
            total_posted = self._scalar(cursor.fetchone(), "count")

            cursor.execute(
                self.db.sql("""
                    SELECT COALESCE(SUM(views), 0) as total_views,
                           COALESCE(SUM(likes), 0) as total_likes,
                           COALESCE(SUM(comments), 0) as total_comments,
                           COALESCE(SUM(shares), 0) as total_shares
                    FROM posts WHERE status = 'posted'
                """)
            )
            engagement = cursor.fetchone()

            cursor.execute(
                self.db.sql(
                    "SELECT COUNT(*) as count FROM accounts "
                    "WHERE status IN ('banned', 'shadowbanned')"
                )
            )
            flagged = self._scalar(cursor.fetchone(), "count")

            resolved = "FALSE" if self.db.is_postgresql else "0"
            cursor.execute(
                self.db.sql(f"SELECT COUNT(*) as count FROM alerts WHERE resolved = {resolved}")
            )
            unresolved_alerts = self._scalar(cursor.fetchone(), "count")

            conn.close()

            total_views = self._scalar(engagement, "total_views")
            total_likes = self._scalar(engagement, "total_likes")
            total_comments = self._scalar(engagement, "total_comments")
            total_shares = self._scalar(engagement, "total_shares")

            avg_engagement = 0
            if total_posted > 0 and total_views > 0:
                avg_engagement = (total_likes + total_comments + total_shares) / total_posted

            return {
                "total_accounts": self._scalar(row, "total"),
                "total_posts": self._scalar(row, "posts"),
                "total_views": self._scalar(row, "views"),
                "posts_posted": total_posted,
                "total_likes": total_likes,
                "total_comments": total_comments,
                "total_shares": total_shares,
                "avg_engagement_per_post": round(avg_engagement, 2),
                "flagged_accounts": flagged,
                "unresolved_alerts": unresolved_alerts,
            }
        except Exception as e:
            logger.error(f"Failed to get performance stats: {e}")
            return {}

    # ---- Helpers ----

    @staticmethod
    def _row_to_account(row) -> Account:
        return Account(
            account_id=row["id"],
            username=row["username"],
            proxy_id=row["proxy_id"] or 0,
            status=row["status"],
            followers=row["followers"] or 0,
            following=row["following"] or 0,
            total_posts=row["total_posts"] or 0,
            total_views=row["total_views"] or 0,
            created_at=str(row["created_at"]) if row["created_at"] else None,
            last_active=str(row["last_active"]) if row["last_active"] else None,
            cookie_data=row["cookie_data"],
            password=row["password"] if "password" in row.keys() else "",
            notes=row["notes"] or "",
        )

    @classmethod
    def from_settings(cls, settings: dict) -> "AccountManager":
        """Create instance from settings dict."""
        return cls(db=Database.from_settings(settings))

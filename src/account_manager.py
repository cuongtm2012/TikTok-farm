# TikTok Farm - Account Manager Module
# CRUD for TikTok accounts with status management and SQLite persistence

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import random

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
        notes: str = "",
    ):
        self.id = account_id
        self.username = username
        self.proxy_id = proxy_id
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

    def to_dict(self) -> dict:
        return {
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
        }


class AccountManager:
    """Manages TikTok accounts with SQLite persistence."""

    def __init__(self, db_path: str = "data/farm.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a new SQLite connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """Initialize database schema."""
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

            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    # ---- CRUD Accounts ----

    def add_account(
        self,
        username: str,
        proxy_id: int = 0,
        notes: str = "",
    ) -> Optional[Account]:
        """Add a new TikTok account. Returns the created Account or None."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO accounts (username, proxy_id, status, notes) VALUES (?, ?, 'pending', ?)",
                (username, proxy_id, notes),
            )
            conn.commit()
            account_id = cursor.lastrowid
            conn.close()
            logger.info(f"Added account {username} (ID: {account_id})")
            return self.get_account(account_id)
        except sqlite3.IntegrityError:
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
            cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
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
            cursor.execute("SELECT * FROM accounts WHERE username = ?", (username,))
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
                cursor.execute("SELECT * FROM accounts WHERE status = ? ORDER BY id", (status,))
            else:
                cursor.execute("SELECT * FROM accounts ORDER BY id")
            rows = cursor.fetchall()
            conn.close()
            return [self._row_to_account(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to list accounts: {e}")
            return []

    def update_account(self, account_id: int, **kwargs) -> Optional[Account]:
        """Update account fields. Returns updated Account or None."""
        allowed_fields = {
            "username", "proxy_id", "status", "followers", "following",
            "total_posts", "total_views", "last_active", "cookie_data", "notes",
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
            cursor.execute(f"UPDATE accounts SET {set_clause} WHERE id = ?", values)
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
            cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
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
        cursor.execute("SELECT * FROM accounts WHERE status IN ('pending', 'warming') ORDER BY created_at")
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_account(r) for r in rows]

    def get_accounts_for_farming(self) -> List[Account]:
        """Get accounts eligible for farm sessions (active or warming)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE status IN ('active', 'warming') ORDER BY last_active ASC")
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_account(r) for r in rows]

    def get_accounts_for_posting(self) -> List[Account]:
        """Get accounts eligible for posting (active only)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE status = 'active' ORDER BY last_active ASC")
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
            else:
                logger.info(f"Account {account.username} still warming ({account.days_since_creation}/7 days)")
        return False

    # ---- Activity Logging ----

    def log_activity(self, account_id: int, activity_type: str, duration_seconds: int, actions_count: int = 0):
        """Log a farm activity."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO farm_activities (account_id, activity_type, duration_seconds, actions_count) "
                "VALUES (?, ?, ?, ?)",
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
                "SELECT * FROM farm_activities WHERE account_id = ? ORDER BY performed_at DESC LIMIT ?",
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
                "INSERT INTO alerts (account_id, alert_type, message) VALUES (?, ?, ?)",
                (account_id, alert_type, message),
            )
            conn.commit()
            conn.close()
            logger.warning(f"Alert [{alert_type}] for account {account_id}: {message}")
            return True
        except Exception as e:
            logger.error(f"Failed to add alert: {e}")
            return False

    def get_unresolved_alerts(self, limit: int = 50) -> List[Dict]:
        """Get unresolved alerts."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT a.*, acc.username FROM alerts a "
                "LEFT JOIN accounts acc ON a.account_id = acc.id "
                "WHERE a.resolved = 0 ORDER BY a.created_at DESC LIMIT ?",
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
            cursor.execute("UPDATE alerts SET resolved = 1 WHERE id = ?", (alert_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to resolve alert {alert_id}: {e}")
            return False

    # ---- Posts ----

    def add_post(self, account_id: int, content_path: str, caption: str = "",
                 hashtags: str = "", affiliate_link: str = "", scheduled_at: Optional[str] = None) -> Optional[int]:
        """Create a post record. Returns post ID."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO posts (account_id, content_path, caption, hashtags, affiliate_link, scheduled_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (account_id, content_path, caption, hashtags, affiliate_link, scheduled_at),
            )
            conn.commit()
            post_id = cursor.lastrowid
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
            cursor.execute(f"UPDATE posts SET {set_clause} WHERE id = ?", values)
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
                "SELECT * FROM posts WHERE status = 'pending' ORDER BY scheduled_at ASC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Failed to get pending posts: {e}")
            return []

    # ---- Stats ----

    def get_performance_stats(self) -> Dict:
        """Aggregate performance stats across all accounts."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) as total, SUM(total_posts) as posts, SUM(total_views) as views FROM accounts")
            row = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) FROM posts WHERE status = 'posted'")
            total_posted = cursor.fetchone()[0]

            cursor.execute("""
                SELECT COALESCE(SUM(views), 0) as total_views,
                       COALESCE(SUM(likes), 0) as total_likes,
                       COALESCE(SUM(comments), 0) as total_comments,
                       COALESCE(SUM(shares), 0) as total_shares
                FROM posts WHERE status = 'posted'
            """)
            engagement = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) FROM accounts WHERE status IN ('banned', 'shadowbanned')")
            flagged = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM alerts WHERE resolved = 0")
            unresolved_alerts = cursor.fetchone()[0]

            conn.close()

            total_views = engagement["total_views"] or 0
            total_likes = engagement["total_likes"] or 0
            total_comments = engagement["total_comments"] or 0
            total_shares = engagement["total_shares"] or 0
            total_posts = total_posted or 0

            avg_engagement = 0
            if total_posts > 0 and total_views > 0:
                avg_engagement = (total_likes + total_comments + total_shares) / total_posts

            return {
                "total_accounts": row["total"] or 0,
                "total_posts": row["posts"] or 0,
                "total_views": row["views"] or 0,
                "posts_posted": total_posts,
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
    def _row_to_account(row: sqlite3.Row) -> Account:
        return Account(
            account_id=row["id"],
            username=row["username"],
            proxy_id=row["proxy_id"],
            status=row["status"],
            followers=row["followers"],
            following=row["following"],
            total_posts=row["total_posts"],
            total_views=row["total_views"],
            created_at=row["created_at"],
            last_active=row["last_active"],
            cookie_data=row["cookie_data"],
            notes=row["notes"],
        )

    @classmethod
    def from_settings(cls, settings: dict) -> "AccountManager":
        """Create instance from settings dict."""
        db_config = settings.get("database", {})
        return cls(db_path=db_config.get("path", "data/farm.db"))

# TikTok Farm — database connection layer (SQLite pilot / PostgreSQL scale)

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2 import IntegrityError as PgIntegrityError
    from psycopg2.extras import RealDictCursor

    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    PgIntegrityError = Exception  # type: ignore[misc, assignment]


class Database:
    """Unified DB access: sqlite (file) or postgresql (Docker / remote)."""

    def __init__(
        self,
        driver: str = "sqlite",
        path: str = "data/farm.db",
        host: str = "localhost",
        port: int = 5432,
        name: str = "tiktok_farm",
        user: str = "tiktok_farm",
        password: str = "",
        url: Optional[str] = None,
    ):
        self.driver = driver.lower().strip()
        self.path = Path(path)
        self.host = host
        self.port = int(port)
        self.name = name
        self.user = user
        self.password = password
        self.url = url or os.getenv("DATABASE_URL", "")

        if self.driver == "postgresql" and not PSYCOPG2_AVAILABLE:
            raise RuntimeError(
                "PostgreSQL driver selected but psycopg2 is not installed. "
                "Run: pip install psycopg2-binary"
            )

    @property
    def is_postgresql(self) -> bool:
        return self.driver == "postgresql"

    @property
    def is_sqlite(self) -> bool:
        return self.driver == "sqlite"

    @property
    def integrity_error(self) -> type:
        if self.is_postgresql:
            return PgIntegrityError
        return sqlite3.IntegrityError

    def connect(self):
        if self.is_postgresql:
            return self._connect_postgresql()
        return self._connect_sqlite()

    def _connect_postgresql(self):
        if self.url:
            conn = psycopg2.connect(self.url, cursor_factory=RealDictCursor)
        else:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                dbname=self.name,
                user=self.user,
                password=self.password,
                cursor_factory=RealDictCursor,
            )
        conn.autocommit = False
        return conn

    def _connect_sqlite(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def sql(self, query: str) -> str:
        if self.is_postgresql:
            return query.replace("?", "%s")
        return query

    def insert_returning_id(self, cursor, table: str) -> int:
        if self.is_postgresql:
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError(f"INSERT into {table} did not return id")
            return int(row["id"] if isinstance(row, dict) else row[0])
        return int(cursor.lastrowid)

    @classmethod
    def from_settings(cls, settings: dict) -> "Database":
        db = settings.get("database", {})
        driver = os.getenv("DATABASE_DRIVER", db.get("driver", "sqlite"))

        if driver == "postgresql":
            url = os.getenv("DATABASE_URL", db.get("url", ""))
            return cls(
                driver="postgresql",
                host=os.getenv("POSTGRES_HOST", db.get("host", "localhost")),
                port=int(os.getenv("POSTGRES_PORT", db.get("port", 5432))),
                name=os.getenv("POSTGRES_DB", db.get("name", "tiktok_farm")),
                user=os.getenv("POSTGRES_USER", db.get("user", "tiktok_farm")),
                password=os.getenv(
                    "POSTGRES_PASSWORD", db.get("password", "tiktok_farm_secret")
                ),
                url=url or None,
            )

        return cls(
            driver="sqlite",
            path=db.get("path", "data/farm.db"),
        )

    def ping(self) -> bool:
        try:
            conn = self.connect()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Database ping failed: {e}")
            return False

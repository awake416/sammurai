"""Gmail message database interface.

Mirrors WhatsAppDB pattern for consistent multi-source digest integration.
Uses historyId for incremental sync (Gmail's version vector).
"""

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class EmailDB:
    """Gmail message database.

    Thread-safe SQLite wrapper. Schema matches wacli.db structure for
    digest compatibility. Uses historyId for incremental sync.

    Attributes:
        db_path: Path to email.db (default: ~/.emailsync/email.db)
    """

    def __init__(self, db_path: str = "~/.emailsync/email.db"):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path).expanduser()
        self._local = threading.local()
        self._connections = []
        self._lock = threading.Lock()

        # Create parent directory if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection.

        Returns:
            SQLite connection with Row factory
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.execute("SELECT 1")
                return conn
            except (sqlite3.Error, sqlite3.ProgrammingError):
                self._local.conn = None

        conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, timeout=30
        )
        conn.row_factory = sqlite3.Row
        self._local.conn = conn

        with self._lock:
            self._connections.append(conn)

        return conn

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        conn = self._get_connection()

        # Messages table (matches wacli.db structure)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_jid TEXT NOT NULL,
                chat_name TEXT,
                msg_id TEXT NOT NULL,
                sender_jid TEXT,
                sender_name TEXT,
                ts INTEGER NOT NULL,
                from_me INTEGER NOT NULL,
                text TEXT,
                display_text TEXT,
                media_type TEXT,
                media_caption TEXT,
                filename TEXT,
                mime_type TEXT,
                direct_path TEXT,
                media_key BLOB,
                file_sha256 BLOB,
                file_enc_sha256 BLOB,
                file_length INTEGER,
                local_path TEXT,
                downloaded_at INTEGER,
                UNIQUE(chat_jid, msg_id)
            )
        """)

        # Indexes for common queries
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_jid)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages(chat_jid, ts)"
        )

        # Sync state table (historyId tracking)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)

        conn.commit()
        logger.info(f"Initialized email database: {self.db_path}")

    def get_messages(
        self, days: Optional[int] = None, limit: Optional[int] = None
    ) -> list[dict]:
        """Fetch messages from last N days.

        Args:
            days: Number of days to look back (None = all messages)
            limit: Maximum number of messages to return

        Returns:
            List of message dicts with keys matching Message model
        """
        conn = self._get_connection()

        query = "SELECT * FROM messages"
        params = []

        if days is not None:
            import time

            cutoff = int(time.time()) - (days * 86400)
            query += " WHERE ts >= ?"
            params.append(cutoff)

        query += " ORDER BY ts DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        messages = []
        for row in rows:
            msg = dict(row)
            # Normalize for Message model
            msg["message"] = msg.get("text") or ""
            msg["timestamp"] = str(msg.get("ts", 0))  # Convert to str for LLM client
            msg["ts"] = msg.get("ts", 0)  # Keep int for sorting
            messages.append(msg)

        return messages

    def insert_message(self, msg: dict) -> None:
        """Insert or ignore message.

        Args:
            msg: Message dict with chat_jid, msg_id, sender_jid, ts, etc.

        Note:
            Uses INSERT OR IGNORE to skip duplicates (UNIQUE constraint on chat_jid, msg_id)
        """
        conn = self._get_connection()

        conn.execute(
            """
            INSERT OR IGNORE INTO messages (
                chat_jid, chat_name, msg_id, sender_jid, sender_name,
                ts, from_me, text, display_text, media_type, media_caption,
                filename, mime_type, direct_path, media_key, file_sha256,
                file_enc_sha256, file_length, local_path, downloaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                msg.get("chat_jid"),
                msg.get("chat_name"),
                msg.get("msg_id"),
                msg.get("sender_jid"),
                msg.get("sender_name"),
                msg.get("ts"),
                msg.get("from_me", 0),
                msg.get("text"),
                msg.get("display_text"),
                msg.get("media_type"),
                msg.get("media_caption"),
                msg.get("filename"),
                msg.get("mime_type"),
                msg.get("direct_path"),
                msg.get("media_key"),
                msg.get("file_sha256"),
                msg.get("file_enc_sha256"),
                msg.get("file_length"),
                msg.get("local_path"),
                msg.get("downloaded_at"),
            ),
        )

    def get_last_history_id(self) -> Optional[str]:
        """Get last synced Gmail historyId.

        Returns:
            historyId string or None if never synced
        """
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT value FROM sync_state WHERE key = 'last_history_id'"
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def update_history_id(self, history_id: str) -> None:
        """Update last synced historyId.

        Args:
            history_id: Gmail historyId string
        """
        import time

        conn = self._get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO sync_state (key, value, updated_at)
            VALUES ('last_history_id', ?, ?)
        """,
            (history_id, int(time.time())),
        )

    def close(self) -> None:
        """Close all database connections."""
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Error closing connection: {e}")
            self._connections.clear()

        if hasattr(self._local, "conn"):
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

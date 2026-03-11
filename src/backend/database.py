import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Configure logger
logger = logging.getLogger(__name__)


class WhatsAppDB:
    def __init__(self, db_path: str = "~/.wacli/wacli.db"):
        self.db_path = Path(db_path).expanduser()
        self._local = threading.local()
        self._connections = []
        self._lock = threading.Lock()

    def _get_connection(self):
        """Get a thread-local SQLite connection."""
        # Law of the Early Exit: return existing connection if available and valid
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                # Verify connection is still alive and usable
                conn.execute("SELECT 1")
                return conn
            except (sqlite3.Error, sqlite3.ProgrammingError):
                logger.debug(
                    f"Thread {threading.get_ident()} connection stale, recreating"
                )
                self._local.conn = None

        logger.debug(
            f"Opening new database connection for thread {threading.get_ident()}"
        )
        try:
            # Parse Don't Validate: Ensure path is string for sqlite3.connect
            # Use check_same_thread=False to allow cross-thread closing,
            # though we still aim for one connection per thread for safety.
            conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=30
            )
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
            with self._lock:
                self._connections.append(conn)
            return conn
        except sqlite3.Error as e:
            # Fail Fast, Fail Loud
            logger.error(
                f"CRITICAL: Failed to connect to database at {self.db_path}: {e}"
            )
            raise

    def close(self):
        """Close all database connections."""
        logger.debug("Closing all database connections")
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except sqlite3.Error as e:
                    logger.error(f"Error closing connection: {e}")
            self._connections.clear()

        # Clear thread-local reference for the current thread
        if hasattr(self._local, "conn"):
            self._local.conn = None

    def get_groups(self, days_active: Optional[int] = None) -> list[dict]:
        """Get all groups with their JIDs.

        Args:
            days_active: If provided, filter to groups with activity in the last N days.

        Returns:
            List of dicts with 'jid' and 'name' keys for each group.
            If days_active is provided, includes 'last_activity' timestamp.
        """
        logger.debug("Fetching all groups from database")

        # Law of the Early Exit: Validate input first
        if days_active is not None and days_active < 0:
            raise ValueError("days_active must be a non-negative integer")

        params = []
        join_type = "LEFT JOIN"
        on_clause_extra = ""

        if days_active is not None:
            # If filtering by activity, use INNER JOIN to exclude inactive groups
            join_type = "INNER JOIN"
            # SQLite strftime with epoch timestamps: convert days ago to Unix timestamp
            # m.ts is stored as epoch (seconds), so we compare using strftime('%s', ...)
            on_clause_extra = "AND m.ts >= strftime('%s', 'now', ?)"
            params.append(f"-{days_active} days")

        query = f"""
            WITH BestNames AS (
                SELECT 
                    chat_jid, 
                    chat_name,
                    COUNT(*) as name_count
                FROM messages
                WHERE chat_jid LIKE '%@g.us'
                  AND chat_name != chat_jid
                  AND chat_name != COALESCE(sender_name, '')
                  AND chat_name != 'me'
                  AND chat_name IS NOT NULL
                GROUP BY chat_jid, chat_name COLLATE NOCASE
            ),
            TopNames AS (
                SELECT 
                    chat_jid, 
                    chat_name,
                    ROW_NUMBER() OVER (PARTITION BY chat_jid ORDER BY name_count DESC) as rn
                FROM BestNames
            )
            SELECT 
                c.jid, 
                COALESCE(tn.chat_name, c.name, c.jid) as name, 
                MAX(m.ts) as last_activity
            FROM chats c
            LEFT JOIN TopNames tn ON c.jid = tn.chat_jid AND tn.rn = 1
            {join_type} messages m ON c.jid = m.chat_jid
                AND m.text IS NOT NULL 
                AND m.text != ''
                {on_clause_extra}
            WHERE c.jid LIKE '%@g.us'
            GROUP BY c.jid
            ORDER BY last_activity DESC
        """

        logger.debug(f"Executing query: {query} with params: {params}")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        logger.debug(f"Query returned {len(rows)} rows")

        return [
            {
                "jid": row["jid"],
                "name": row["name"],
                "last_activity": str(row["last_activity"])
                if row["last_activity"]
                else None,
            }
            for row in rows
        ]

    def get_group_jid(self, group_input: str) -> Optional[str]:
        """Get group JID by name or direct JID input.

        Args:
            group_input: Either a group name (will be searched) or a group JID (returned directly).

        Returns:
            The group JID if found, None otherwise.
        """
        # Early exit: if input is already a group JID, return it directly
        if "@g.us" in group_input:
            logger.debug(f"Input appears to be a direct JID: {group_input}")
            return group_input

        # Otherwise search by name in both chats and messages tables
        logger.debug(f"Searching for group by name: {group_input}")
        query = """
                SELECT jid FROM (
                    SELECT chat_jid as jid, chat_name as name, 1 as priority FROM messages 
                    WHERE chat_jid LIKE '%@g.us'
                      AND chat_name != chat_jid
                      AND chat_name != COALESCE(sender_name, '')
                      AND chat_name != 'me'
                    UNION ALL
                    SELECT jid, name, 2 as priority FROM chats WHERE jid LIKE '%@g.us'
                )
                WHERE name LIKE ?
                ORDER BY priority ASC
                LIMIT 1
            """
        params = (f"%{group_input}%",)
        logger.debug(f"Executing query: {query} with params: {params}")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()

        if row is None:
            logger.warning(f"No group found matching: {group_input}")
            return None

        logger.debug("Found matching group JID")
        return row["jid"]

    def _resolve_group_name(self, jid: str) -> str:
        """Resolve the best possible name for a group JID.

        Args:
            jid: The group JID to resolve.

        Returns:
            The best name found, or the JID if no name is found.
        """
        # Law of the Early Exit: if not a group JID, return as is
        if "@g.us" not in jid:
            return jid

        # Try to get the most frequent valid name from messages table
        # This is often more accurate than the chats table for groups
        query_messages = """
            SELECT chat_name
            FROM messages
            WHERE chat_jid = ? 
              AND chat_name != chat_jid 
              AND chat_name != COALESCE(sender_name, '') 
              AND chat_name != 'me' 
              AND chat_name IS NOT NULL 
            GROUP BY chat_name COLLATE NOCASE
            ORDER BY COUNT(*) DESC 
            LIMIT 1
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query_messages, (jid,))
        row = cursor.fetchone()
        if row:
            return row["chat_name"]

        # Fallback to chats table
        query_chats = "SELECT name FROM chats WHERE jid = ?"
        cursor.execute(query_chats, (jid,))
        row = cursor.fetchone()
        if row and row["name"]:
            return row["name"]

        return jid

    def get_messages_by_group(
        self, group_jid: str, limit: int = 100, days: Optional[int] = None
    ) -> list[dict]:
        """Get messages for a specific group.

        Args:
            group_jid: The group JID to fetch messages from.
            limit: Maximum number of messages to return.
            days: If provided, filter to messages from the last N days.

        Returns:
            List of dicts with msg_id, text, sender_name, ts, chat_name, chat_jid.
        """
        logger.debug(f"Fetching messages for group: {group_jid}")

        # Law of the Early Exit: Validate input first
        if days is not None and days < 0:
            raise ValueError("days must be a non-negative integer")

        base_query = """
            SELECT 
                msg_id,
                text,
                sender_name,
                ts,
                chat_name,
                chat_jid,
                media_type,
                media_caption,
                filename,
                local_path
            FROM messages
        """
        where_clause = "WHERE chat_jid = ?"
        params: list[Any] = [group_jid]

        if days is not None:
            where_clause += " AND ts >= strftime('%s', 'now', ?)"
            params.append(f"-{days} days")
            order_limit = "ORDER BY ts DESC"
        else:
            order_limit = "ORDER BY ts DESC LIMIT ?"
            params.append(limit)

        query = f"{base_query} {where_clause} {order_limit}"

        logger.debug(f"Executing query: {query} with params: {params}")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        logger.debug(f"Query returned {len(rows)} rows")

        # Resolve group name once for all messages in this group
        group_name = self._resolve_group_name(group_jid)
        messages = self._rows_to_message_dicts(rows)
        for msg in messages:
            msg["group_name"] = group_name

        return messages

    def get_recent_messages(
        self, limit: int = 100, days: Optional[int] = None
    ) -> list[dict]:
        """Get recent messages from all groups.

        Args:
            limit: Maximum number of messages to return.
            days: If provided, filter to messages from the last N days.

        Returns:
            List of dicts with msg_id, text, sender_name, ts, chat_name, chat_jid.
        """
        logger.debug(f"Fetching recent messages (limit: {limit}, days: {days})")

        # Law of the Early Exit: Validate input first
        if days is not None and days < 0:
            raise ValueError("days must be a non-negative integer")

        base_query = """
            SELECT 
                msg_id,
                text,
                sender_name,
                ts,
                chat_name,
                chat_jid,
                media_type,
                media_caption,
                filename,
                local_path
            FROM messages
        """
        where_clause = "WHERE chat_jid LIKE '%@g.us'"
        params: list[Any] = []

        if days is not None:
            where_clause += " AND ts >= strftime('%s', 'now', ?)"
            params.append(f"-{days} days")
            order_limit = "ORDER BY ts DESC"
        else:
            order_limit = "ORDER BY ts DESC LIMIT ?"
            params.append(limit)

        query = f"{base_query} {where_clause} {order_limit}"

        logger.debug(f"Executing query: {query} with params: {params}")
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        logger.debug(f"Query returned {len(rows)} rows")

        messages = self._rows_to_message_dicts(rows)

        # Resolve group names for each unique JID to ensure accuracy
        name_cache = {}
        for msg in messages:
            jid = msg["group_jid"]
            if jid not in name_cache:
                name_cache[jid] = self._resolve_group_name(jid)
            msg["group_name"] = name_cache[jid]

        return messages

    def _rows_to_message_dicts(self, rows: Sequence[Mapping[str, Any]]) -> list[dict]:
        """Convert database rows to message dictionaries.

        Args:
            rows: List of sqlite3.Row objects or dictionaries

        Returns:
            List of dicts with standardized message format
        """
        messages = []
        for row in rows:
            text = row["text"] or ""
            media_type = row["media_type"]
            media_caption = row["media_caption"]
            filename = row["filename"]
            local_path = row["local_path"]

            attachment_parts = []
            if media_type:
                attachment_parts.append(f"[Attachment: {media_type}]")
                if filename:
                    attachment_parts.append(filename)
                if media_caption:
                    attachment_parts.append(f"- {media_caption}")

            attachment_info = " ".join(attachment_parts)
            final_text = text
            if attachment_info:
                if final_text:
                    final_text = f"{final_text}\n{attachment_info}"
                else:
                    final_text = attachment_info

            messages.append(
                {
                    "id": row["msg_id"],
                    "message": final_text,
                    "sender": row["sender_name"],
                    "timestamp": str(row["ts"]),
                    "group_name": row["chat_name"],
                    "group_jid": row["chat_jid"],
                    "local_path": local_path,
                    "media_type": media_type,
                    "filename": filename,
                }
            )
        return messages

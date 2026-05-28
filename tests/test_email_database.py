"""Tests for email_database.py"""

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from src.backend.email_database import EmailDB


@pytest.fixture
def temp_db():
    """Temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_email.db"
        yield str(db_path)


@pytest.fixture
def email_db(temp_db):
    """EmailDB instance with temp database."""
    db = EmailDB(db_path=temp_db)
    yield db
    db.close()


def test_schema_init_fresh_db(temp_db):
    """Schema initialization creates tables on fresh database."""
    db = EmailDB(db_path=temp_db)
    conn = db._get_connection()

    # Check messages table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
    )
    assert cursor.fetchone() is not None

    # Check sync_state table exists
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'"
    )
    assert cursor.fetchone() is not None

    # Check indexes exist
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_messages_ts'"
    )
    assert cursor.fetchone() is not None

    db.close()


def test_schema_init_existing_db(temp_db):
    """Schema initialization is idempotent on existing database."""
    db1 = EmailDB(db_path=temp_db)
    db1.insert_message(
        {
            "chat_jid": "thread1",
            "msg_id": "msg1",
            "sender_jid": "test@example.com",
            "ts": int(time.time()),
            "from_me": 0,
            "text": "test",
        }
    )
    db1._get_connection().commit()
    db1.close()

    # Re-open same database
    db2 = EmailDB(db_path=temp_db)
    messages = db2.get_messages()
    assert len(messages) == 1
    assert messages[0]["text"] == "test"
    db2.close()


def test_insert_message_new(email_db):
    """Insert new message succeeds."""
    msg = {
        "chat_jid": "thread1",
        "chat_name": "Test Subject",
        "msg_id": "msg1",
        "sender_jid": "sender@example.com",
        "sender_name": "Test Sender",
        "ts": int(time.time()),
        "from_me": 0,
        "text": "Test message",
    }

    email_db.insert_message(msg)
    email_db._get_connection().commit()

    messages = email_db.get_messages()
    assert len(messages) == 1
    assert messages[0]["text"] == "Test message"
    assert messages[0]["sender_name"] == "Test Sender"


def test_insert_message_duplicate(email_db):
    """Duplicate message is ignored (UNIQUE constraint)."""
    msg = {
        "chat_jid": "thread1",
        "msg_id": "msg1",
        "sender_jid": "test@example.com",
        "ts": int(time.time()),
        "from_me": 0,
        "text": "original",
    }

    email_db.insert_message(msg)
    email_db._get_connection().commit()

    # Try to insert duplicate with different text
    msg["text"] = "modified"
    email_db.insert_message(msg)
    email_db._get_connection().commit()

    messages = email_db.get_messages()
    assert len(messages) == 1
    assert messages[0]["text"] == "original"  # First insert wins


def test_insert_message_transaction_rollback(email_db):
    """Transaction rollback discards uncommitted inserts."""
    conn = email_db._get_connection()

    conn.execute("BEGIN")
    email_db.insert_message(
        {
            "chat_jid": "thread1",
            "msg_id": "msg1",
            "sender_jid": "test@example.com",
            "ts": int(time.time()),
            "from_me": 0,
            "text": "test",
        }
    )
    conn.rollback()

    messages = email_db.get_messages()
    assert len(messages) == 0


def test_get_messages_last_n_days(email_db):
    """get_messages filters by days parameter."""
    now = int(time.time())
    yesterday = now - 86400
    last_week = now - (7 * 86400)

    email_db.insert_message(
        {
            "chat_jid": "thread1",
            "msg_id": "msg1",
            "sender_jid": "test@example.com",
            "ts": now,
            "from_me": 0,
            "text": "today",
        }
    )
    email_db.insert_message(
        {
            "chat_jid": "thread2",
            "msg_id": "msg2",
            "sender_jid": "test@example.com",
            "ts": yesterday,
            "from_me": 0,
            "text": "yesterday",
        }
    )
    email_db.insert_message(
        {
            "chat_jid": "thread3",
            "msg_id": "msg3",
            "sender_jid": "test@example.com",
            "ts": last_week,
            "from_me": 0,
            "text": "last week",
        }
    )
    email_db._get_connection().commit()

    messages_3d = email_db.get_messages(days=3)
    assert len(messages_3d) == 2
    assert any(m["text"] == "today" for m in messages_3d)
    assert any(m["text"] == "yesterday" for m in messages_3d)

    messages_all = email_db.get_messages()
    assert len(messages_all) == 3


def test_get_messages_empty_result(email_db):
    """get_messages returns empty list when no messages exist."""
    messages = email_db.get_messages()
    assert messages == []


def test_get_messages_normalizes_to_message_model(email_db):
    """get_messages adds 'message' and 'timestamp' keys for Message model."""
    email_db.insert_message(
        {
            "chat_jid": "thread1",
            "msg_id": "msg1",
            "sender_jid": "test@example.com",
            "ts": 1234567890,
            "from_me": 0,
            "text": "test message",
        }
    )
    email_db._get_connection().commit()

    messages = email_db.get_messages()
    assert len(messages) == 1
    assert messages[0]["message"] == "test message"
    assert messages[0]["timestamp"] == 1234567890


def test_history_id_first_sync(email_db):
    """get_last_history_id returns None on first sync."""
    history_id = email_db.get_last_history_id()
    assert history_id is None


def test_history_id_subsequent_sync(email_db):
    """update_history_id stores and retrieves historyId."""
    email_db.update_history_id("12345")
    email_db._get_connection().commit()

    history_id = email_db.get_last_history_id()
    assert history_id == "12345"

    # Update again
    email_db.update_history_id("67890")
    email_db._get_connection().commit()

    history_id = email_db.get_last_history_id()
    assert history_id == "67890"


def test_close_connections(email_db):
    """close() shuts down all connections."""
    # Force connection creation
    email_db._get_connection()

    email_db.close()

    # Verify connections list is cleared
    assert len(email_db._connections) == 0

    # Verify thread-local connection is cleared
    assert getattr(email_db._local, "conn", None) is None

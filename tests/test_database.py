from unittest.mock import patch, MagicMock
from src.backend.database import WhatsAppDB


@patch("sqlite3.connect")
def test_get_groups_with_activity(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    mock_cursor.fetchall.return_value = [
        {"jid": "1@g.us", "name": "Group 1", "last_activity": "2026-03-07 10:00:00"}
    ]

    db = WhatsAppDB("dummy.db")
    groups = db.get_groups(days_active=30)

    assert len(groups) == 1
    assert "last_activity" in groups[0]

    # Verify query contains date filtering
    call_args = mock_cursor.execute.call_args[0][0]
    assert "ts >=" in call_args or "timestamp >=" in call_args


def test_rows_to_message_dicts_with_media():
    db = WhatsAppDB("dummy.db")
    rows = [
        {
            "msg_id": "1",
            "text": "Hello",
            "sender_name": "User1",
            "ts": 123456789,
            "chat_name": "Group1",
            "chat_jid": "1@g.us",
            "media_type": "image",
            "media_caption": "A nice picture",
            "filename": "pic.jpg",
            "local_path": "/path/to/pic.jpg",
        },
        {
            "msg_id": "2",
            "text": None,
            "sender_name": "User2",
            "ts": 123456790,
            "chat_name": "Group1",
            "chat_jid": "1@g.us",
            "media_type": "document",
            "media_caption": None,
            "filename": "doc.pdf",
            "local_path": "/path/to/doc.pdf",
        },
        {
            "msg_id": "3",
            "text": "Just text",
            "sender_name": "User3",
            "ts": 123456791,
            "chat_name": "Group1",
            "chat_jid": "1@g.us",
            "media_type": None,
            "media_caption": None,
            "filename": None,
            "local_path": None,
        },
    ]

    messages = db._rows_to_message_dicts(rows)

    assert len(messages) == 3
    assert (
        messages[0]["message"] == "Hello\n[Attachment: image] pic.jpg - A nice picture"
    )
    assert messages[1]["message"] == "[Attachment: document] doc.pdf"
    assert messages[0]["local_path"] == "/path/to/pic.jpg"
    assert messages[0]["media_type"] == "image"
    assert messages[0]["filename"] == "pic.jpg"
    assert messages[1]["local_path"] == "/path/to/doc.pdf"
    assert messages[1]["media_type"] == "document"
    assert messages[1]["filename"] == "doc.pdf"
    assert messages[2]["local_path"] is None
    assert messages[2]["media_type"] is None
    assert messages[2]["filename"] is None


@patch("sqlite3.connect")
def test_resolve_group_name(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    db = WhatsAppDB("dummy.db")
    jid = "12345@g.us"

    # Test 1: Name found in messages
    mock_cursor.fetchone.side_effect = [
        {"chat_name": "Message Name"},  # First call for messages
    ]
    assert db._resolve_group_name(jid) == "Message Name"

    # Test 2: Name not in messages, found in chats
    mock_cursor.fetchone.side_effect = [
        None,  # Not in messages
        {"name": "Chat Name"},  # Found in chats
    ]
    assert db._resolve_group_name(jid) == "Chat Name"

    # Test 3: Name not found anywhere
    mock_cursor.fetchone.side_effect = [
        None,  # Not in messages
        None,  # Not in chats
    ]
    assert db._resolve_group_name(jid) == jid

    # Test 4: Not a group JID
    assert db._resolve_group_name("user@s.whatsapp.net") == "user@s.whatsapp.net"


@patch("sqlite3.connect")
def test_get_messages_by_group_with_days(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_connect.return_value = mock_conn

    mock_cursor.fetchall.return_value = []

    db = WhatsAppDB("dummy.db")
    db.get_messages_by_group("1@g.us", days=7)

    # Verify query contains date filtering
    # We check call_args_list because _resolve_group_name also calls execute
    found_query = False
    for call in mock_cursor.execute.call_args_list:
        query_str = call[0][0]
        if (
            "SELECT" in query_str
            and "FROM messages" in query_str
            and "ts >=" in query_str
        ):
            found_query = True
            assert "ts >= strftime('%s', 'now', ?)" in query_str
            assert "-7 days" in call[0][1]
            break
    assert found_query, "Main query with date filtering not found"

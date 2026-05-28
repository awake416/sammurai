"""Tests for emailsync_daemon.py"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.backend.emailsync_daemon import load_config, main


@pytest.fixture
def temp_config():
    """Temporary config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.yaml"
        config_path.write_text(
            """
email:
  enabled: true
  database:
    path: /tmp/test_email.db
  sync:
    poll_interval: 1
    max_results_per_sync: 10
    labels_to_sync:
      - INBOX
    skip_labels:
      - SPAM
"""
        )
        yield config_path


def test_load_config_missing_file():
    """load_config returns empty dict when config.yaml missing."""
    with patch(
        "src.backend.emailsync_daemon.Path.exists", return_value=False
    ):
        config = load_config()
        assert config == {}


def test_main_config_disabled():
    """main exits early if email.enabled is false."""
    config = {"email": {"enabled": False}}

    with patch("src.backend.emailsync_daemon.load_config", return_value=config):
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0


def test_main_successful_sync():
    """main loop syncs messages and updates historyId."""
    config = {
        "email": {
            "enabled": True,
            "database": {"path": "/tmp/test_email.db"},
            "sync": {
                "poll_interval": 0.1,
                "max_results_per_sync": 10,
                "labels_to_sync": ["INBOX"],
                "skip_labels": ["SPAM"],
            },
        }
    }

    mock_gmail = MagicMock()
    mock_email_db = MagicMock()
    mock_conn = MagicMock()

    # Mock first sync returns messages
    mock_email_db.get_last_history_id.return_value = None
    mock_gmail.fetch_messages.return_value = (
        [
            {
                "chat_jid": "thread1",
                "msg_id": "msg1",
                "sender_jid": "test@example.com",
                "ts": 123456,
                "from_me": 0,
                "text": "test",
            }
        ],
        "12345",
    )
    mock_email_db._get_connection.return_value = mock_conn

    with patch("src.backend.emailsync_daemon.load_config", return_value=config):
        with patch(
            "src.backend.emailsync_daemon.GmailClient", return_value=mock_gmail
        ):
            with patch(
                "src.backend.emailsync_daemon.EmailDB", return_value=mock_email_db
            ):
                with patch("src.backend.emailsync_daemon.time.sleep") as mock_sleep:
                    # Stop after first iteration
                    mock_sleep.side_effect = KeyboardInterrupt

                    with pytest.raises(KeyboardInterrupt):
                        main()

    # Verify transaction
    mock_conn.execute.assert_any_call("BEGIN")
    mock_email_db.insert_message.assert_called_once()
    mock_email_db.update_history_id.assert_called_with("12345")
    mock_conn.commit.assert_called_once()


def test_main_oauth_token_expired():
    """main handles OAuth token expiry."""
    config = {
        "email": {
            "enabled": True,
            "database": {"path": "/tmp/test_email.db"},
            "sync": {
                "poll_interval": 0.1,
                "max_results_per_sync": 10,
                "labels_to_sync": ["INBOX"],
                "skip_labels": ["SPAM"],
            },
        }
    }

    mock_gmail = MagicMock()
    mock_email_db = MagicMock()

    from google.auth.exceptions import RefreshError

    mock_gmail.fetch_messages.side_effect = RefreshError("Token expired")

    with patch("src.backend.emailsync_daemon.load_config", return_value=config):
        with patch(
            "src.backend.emailsync_daemon.GmailClient", return_value=mock_gmail
        ):
            with patch(
                "src.backend.emailsync_daemon.EmailDB", return_value=mock_email_db
            ):
                with patch("src.backend.emailsync_daemon.time.sleep") as mock_sleep:
                    # Stop after first error
                    mock_sleep.side_effect = KeyboardInterrupt

                    with pytest.raises(KeyboardInterrupt):
                        main()

    # Verify error was logged but daemon continued
    mock_gmail.fetch_messages.assert_called_once()


def test_main_gmail_api_down():
    """main handles Gmail API errors and continues."""
    config = {
        "email": {
            "enabled": True,
            "database": {"path": "/tmp/test_email.db"},
            "sync": {
                "poll_interval": 0.1,
                "max_results_per_sync": 10,
                "labels_to_sync": ["INBOX"],
                "skip_labels": ["SPAM"],
            },
        }
    }

    mock_gmail = MagicMock()
    mock_email_db = MagicMock()

    from googleapiclient.errors import HttpError

    error_resp = Mock(status=503)
    mock_gmail.fetch_messages.side_effect = HttpError(
        error_resp, b"Service unavailable"
    )

    with patch("src.backend.emailsync_daemon.load_config", return_value=config):
        with patch(
            "src.backend.emailsync_daemon.GmailClient", return_value=mock_gmail
        ):
            with patch(
                "src.backend.emailsync_daemon.EmailDB", return_value=mock_email_db
            ):
                with patch("src.backend.emailsync_daemon.time.sleep") as mock_sleep:
                    mock_sleep.side_effect = KeyboardInterrupt

                    with pytest.raises(KeyboardInterrupt):
                        main()

    # Daemon should continue after error
    mock_gmail.fetch_messages.assert_called_once()


def test_main_transaction_rollback_on_error():
    """main rolls back transaction on insert error."""
    config = {
        "email": {
            "enabled": True,
            "database": {"path": "/tmp/test_email.db"},
            "sync": {
                "poll_interval": 0.1,
                "max_results_per_sync": 10,
                "labels_to_sync": ["INBOX"],
                "skip_labels": ["SPAM"],
            },
        }
    }

    mock_gmail = MagicMock()
    mock_email_db = MagicMock()
    mock_conn = MagicMock()

    mock_email_db.get_last_history_id.return_value = None
    mock_gmail.fetch_messages.return_value = (
        [{"chat_jid": "thread1", "msg_id": "msg1"}],
        "12345",
    )
    mock_email_db._get_connection.return_value = mock_conn

    # Simulate insert error
    mock_email_db.insert_message.side_effect = Exception("DB write error")

    with patch("src.backend.emailsync_daemon.load_config", return_value=config):
        with patch(
            "src.backend.emailsync_daemon.GmailClient", return_value=mock_gmail
        ):
            with patch(
                "src.backend.emailsync_daemon.EmailDB", return_value=mock_email_db
            ):
                with patch("src.backend.emailsync_daemon.time.sleep") as mock_sleep:
                    mock_sleep.side_effect = KeyboardInterrupt

                    with pytest.raises(KeyboardInterrupt):
                        main()

    # Verify rollback called
    mock_conn.rollback.assert_called_once()
    mock_conn.commit.assert_not_called()


def test_main_no_new_messages():
    """main updates historyId even when no new messages."""
    config = {
        "email": {
            "enabled": True,
            "database": {"path": "/tmp/test_email.db"},
            "sync": {
                "poll_interval": 0.1,
                "max_results_per_sync": 10,
                "labels_to_sync": ["INBOX"],
                "skip_labels": ["SPAM"],
            },
        }
    }

    mock_gmail = MagicMock()
    mock_email_db = MagicMock()
    mock_conn = MagicMock()

    mock_email_db.get_last_history_id.return_value = "12345"
    # No new messages, but historyId changed
    mock_gmail.fetch_messages.return_value = ([], "67890")
    mock_email_db._get_connection.return_value = mock_conn

    with patch("src.backend.emailsync_daemon.load_config", return_value=config):
        with patch(
            "src.backend.emailsync_daemon.GmailClient", return_value=mock_gmail
        ):
            with patch(
                "src.backend.emailsync_daemon.EmailDB", return_value=mock_email_db
            ):
                with patch("src.backend.emailsync_daemon.time.sleep") as mock_sleep:
                    mock_sleep.side_effect = KeyboardInterrupt

                    with pytest.raises(KeyboardInterrupt):
                        main()

    # Verify historyId updated
    mock_email_db.update_history_id.assert_called_with("67890")
    mock_conn.commit.assert_called_once()

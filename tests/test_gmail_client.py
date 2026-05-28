"""Tests for gmail_client.py"""

import base64
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from googleapiclient.errors import HttpError

from src.backend.gmail_client import GmailClient


@pytest.fixture
def temp_paths():
    """Temporary paths for credentials and token."""
    with tempfile.TemporaryDirectory() as tmpdir:
        creds_path = Path(tmpdir) / "credentials.json"
        token_path = Path(tmpdir) / "token.json"
        yield creds_path, token_path


@pytest.fixture
def gmail_client(temp_paths):
    """GmailClient instance with temp paths."""
    creds_path, token_path = temp_paths
    return GmailClient(
        credentials_path=str(creds_path), token_path=str(token_path)
    )


@pytest.fixture
def mock_service():
    """Mock Gmail API service."""
    service = MagicMock()
    return service


def test_authenticate_success(gmail_client, temp_paths, mock_service):
    """OAuth flow succeeds and saves token."""
    creds_path, token_path = temp_paths

    # Create fake credentials file
    creds_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "test_client_id",
                    "client_secret": "test_secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        )
    )

    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds.to_json.return_value = '{"token": "test_token"}'

    with patch(
        "src.backend.gmail_client.InstalledAppFlow.from_client_secrets_file"
    ) as mock_flow_cls:
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_cls.return_value = mock_flow

        with patch("src.backend.gmail_client.build", return_value=mock_service):
            gmail_client.authenticate()

    assert token_path.exists()
    assert gmail_client.service is not None


def test_authenticate_token_refresh(gmail_client, temp_paths, mock_service):
    """Existing token refreshes automatically."""
    creds_path, token_path = temp_paths

    # Create existing token
    token_path.write_text(
        json.dumps(
            {
                "token": "old_token",
                "refresh_token": "refresh_token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "client_id": "test_client_id",
                "client_secret": "test_secret",
            }
        )
    )

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_token"

    with patch(
        "src.backend.gmail_client.Credentials.from_authorized_user_file",
        return_value=mock_creds,
    ):
        with patch("src.backend.gmail_client.Request") as mock_request:
            mock_creds.refresh = MagicMock()
            mock_creds.valid = True

            with patch("src.backend.gmail_client.build", return_value=mock_service):
                gmail_client.authenticate()

    assert gmail_client.service is not None


def test_authenticate_missing_credentials(gmail_client):
    """Raises FileNotFoundError if credentials.json missing."""
    with pytest.raises(FileNotFoundError, match="Credentials not found"):
        gmail_client.authenticate()


def test_fetch_messages_full_sync(gmail_client, mock_service):
    """Full sync fetches all messages and returns historyId."""
    gmail_client.service = mock_service

    # Mock profile for historyId
    mock_service.users().getProfile().execute.return_value = {
        "historyId": "12345"
    }

    # Mock message list
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "msg1"}, {"id": "msg2"}]
    }

    # Mock message details
    def mock_get_message(userId, id, format):
        if id == "msg1":
            return Mock(
                execute=lambda: {
                    "id": "msg1",
                    "threadId": "thread1",
                    "internalDate": "1234567890000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Alice <alice@example.com>"},
                            {"name": "Subject", "value": "Test Subject"},
                        ],
                        "body": {"data": base64.urlsafe_b64encode(b"Hello").decode()},
                    },
                }
            )
        elif id == "msg2":
            return Mock(
                execute=lambda: {
                    "id": "msg2",
                    "threadId": "thread2",
                    "internalDate": "1234567891000",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "bob@example.com"},
                            {"name": "Subject", "value": "Test 2"},
                        ],
                        "body": {"data": base64.urlsafe_b64encode(b"World").decode()},
                    },
                }
            )

    mock_service.users().messages().get.side_effect = mock_get_message

    messages, history_id = gmail_client.fetch_messages(
        since_history_id=None, max_results=100
    )

    assert len(messages) == 2
    assert messages[0]["msg_id"] == "msg1"
    assert messages[0]["sender_name"] == "Alice"
    assert messages[0]["sender_jid"] == "alice@example.com"
    assert messages[0]["chat_name"] == "Test Subject"
    assert messages[0]["text"] == "Hello"
    assert history_id == "12345"


def test_fetch_messages_incremental_sync(gmail_client, mock_service):
    """Incremental sync uses history API with historyId."""
    gmail_client.service = mock_service

    # Mock history list
    mock_service.users().history().list().execute.return_value = {
        "historyId": "67890",
        "history": [
            {
                "messagesAdded": [
                    {"message": {"id": "msg3", "labelIds": ["INBOX"]}}
                ]
            }
        ],
    }

    # Mock message details
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg3",
        "threadId": "thread3",
        "internalDate": "1234567892000",
        "payload": {
            "headers": [
                {"name": "From", "value": "Charlie <charlie@example.com>"},
                {"name": "Subject", "value": "New Message"},
            ],
            "body": {"data": base64.urlsafe_b64encode(b"Incremental").decode()},
        },
    }

    messages, history_id = gmail_client.fetch_messages(
        since_history_id="12345", labels=["INBOX"]
    )

    assert len(messages) == 1
    assert messages[0]["msg_id"] == "msg3"
    assert messages[0]["text"] == "Incremental"
    assert history_id == "67890"


def test_fetch_messages_history_expired(gmail_client, mock_service):
    """Falls back to full sync if historyId expired (404)."""
    gmail_client.service = mock_service

    # Mock 404 error from history API
    error_resp = Mock(status=404)
    mock_service.users().history().list().execute.side_effect = HttpError(
        error_resp, b"historyId too old"
    )

    # Mock full sync path
    mock_service.users().getProfile().execute.return_value = {
        "historyId": "99999"
    }
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "msg4"}]
    }
    mock_service.users().messages().get().execute.return_value = {
        "id": "msg4",
        "threadId": "thread4",
        "internalDate": "1234567893000",
        "payload": {
            "headers": [
                {"name": "From", "value": "test@example.com"},
                {"name": "Subject", "value": "Fallback"},
            ],
            "body": {"data": base64.urlsafe_b64encode(b"Fallback text").decode()},
        },
    }

    messages, history_id = gmail_client.fetch_messages(since_history_id="old_id")

    assert len(messages) == 1
    assert messages[0]["msg_id"] == "msg4"
    assert history_id == "99999"


def test_fetch_messages_rate_limit(gmail_client, mock_service):
    """Rate limit error (429) raises HttpError."""
    gmail_client.service = mock_service

    error_resp = Mock(status=429)
    mock_service.users().messages().list().execute.side_effect = HttpError(
        error_resp, b"Rate limit exceeded"
    )

    with pytest.raises(HttpError):
        gmail_client.fetch_messages()


def test_fetch_messages_timeout(gmail_client, mock_service):
    """Timeout error raises HttpError."""
    gmail_client.service = mock_service

    error_resp = Mock(status=504)
    mock_service.users().messages().list().execute.side_effect = HttpError(
        error_resp, b"Gateway timeout"
    )

    with pytest.raises(HttpError):
        gmail_client.fetch_messages()


def test_download_attachment_success(gmail_client, mock_service, tmp_path):
    """Attachment download succeeds."""
    gmail_client.service = mock_service

    attachment_data = b"PDF content here"
    encoded = base64.urlsafe_b64encode(attachment_data).decode()

    mock_service.users().messages().attachments().get().execute.return_value = {
        "data": encoded
    }

    save_path = tmp_path / "attachments" / "test.pdf"
    success = gmail_client.download_attachment("msg1", "att1", str(save_path))

    assert success
    assert save_path.exists()
    assert save_path.read_bytes() == attachment_data


def test_download_attachment_404(gmail_client, mock_service, tmp_path):
    """Attachment not found returns False."""
    gmail_client.service = mock_service

    error_resp = Mock(status=404)
    mock_service.users().messages().attachments().get().execute.side_effect = (
        HttpError(error_resp, b"Attachment not found")
    )

    save_path = tmp_path / "test.pdf"
    success = gmail_client.download_attachment("msg1", "bad_att", str(save_path))

    assert not success
    assert not save_path.exists()


def test_download_attachment_disk_full(gmail_client, mock_service, tmp_path):
    """Disk full error returns False."""
    gmail_client.service = mock_service

    encoded = base64.urlsafe_b64encode(b"data").decode()
    mock_service.users().messages().attachments().get().execute.return_value = {
        "data": encoded
    }

    # Mock OSError on write
    save_path = tmp_path / "readonly" / "test.pdf"
    save_path.parent.mkdir()
    save_path.parent.chmod(0o444)  # Read-only directory

    success = gmail_client.download_attachment("msg1", "att1", str(save_path))

    assert not success


def test_parse_message_multipart(gmail_client):
    """Multipart message extracts text/plain part."""
    msg = {
        "id": "msg5",
        "threadId": "thread5",
        "internalDate": "1234567894000",
        "payload": {
            "headers": [
                {"name": "From", "value": "test@example.com"},
                {"name": "Subject", "value": "Multipart"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Plain text").decode()},
                },
                {"mimeType": "text/html", "body": {"data": "ignored"}},
            ],
        },
    }

    parsed = gmail_client._parse_message(msg)

    assert parsed["text"] == "Plain text"
    assert parsed["msg_id"] == "msg5"


def test_parse_message_no_subject(gmail_client):
    """Message without subject uses (No Subject)."""
    msg = {
        "id": "msg6",
        "threadId": "thread6",
        "internalDate": "1234567895000",
        "payload": {
            "headers": [{"name": "From", "value": "test@example.com"}],
            "body": {"data": base64.urlsafe_b64encode(b"No subject").decode()},
        },
    }

    parsed = gmail_client._parse_message(msg)

    assert parsed["chat_name"] == "(No Subject)"

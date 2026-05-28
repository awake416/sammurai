"""Tests for email integration in digest_runner.py"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.backend.digest_runner import run_daily_digest


@pytest.fixture
def temp_wiki_path():
    """Temporary wiki directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def config_with_email(temp_wiki_path):
    """Config with email enabled."""
    return {
        "wiki": {"path": str(temp_wiki_path)},
        "cron": {"days": 1, "groups": ["test-group"]},
        "llm": {"model": "claude-sonnet-4.6"},
        "database": {"path": "/tmp/wacli.db"},
        "parallel": {"workers": 1, "batch_workers": 1},
        "email": {
            "enabled": True,
            "database": {"path": "/tmp/email.db"},
            "sync": {
                "poll_interval": 300,
                "max_results_per_sync": 100,
                "labels_to_sync": ["INBOX"],
                "skip_labels": ["SPAM"],
            },
        },
    }


@pytest.fixture
def config_without_email(temp_wiki_path):
    """Config with email disabled."""
    return {
        "wiki": {"path": str(temp_wiki_path)},
        "cron": {"days": 1, "groups": ["test-group"]},
        "llm": {"model": "claude-sonnet-4.6"},
        "database": {"path": "/tmp/wacli.db"},
        "parallel": {"workers": 1, "batch_workers": 1},
        "email": {"enabled": False},
    }


def test_email_db_exists_merge_messages(config_with_email, temp_wiki_path):
    """Email DB exists: merge email messages with WhatsApp messages."""
    mock_whatsapp_db = MagicMock()
    mock_email_db = MagicMock()
    mock_llm_client = MagicMock()
    mock_topic_extractor = MagicMock()
    mock_document_parser = MagicMock()

    # Mock email messages (2 domains: example.com, test.com)
    mock_email_db.get_messages.return_value = [
        {
            "sender_jid": "alice@example.com",
            "text": "Email 1",
            "ts": 123456,
            "chat_jid": "thread1",
            "msg_id": "msg1",
            "from_me": 0,
        },
        {
            "sender_jid": "bob@test.com",
            "text": "Email 2",
            "ts": 123457,
            "chat_jid": "thread2",
            "msg_id": "msg2",
            "from_me": 0,
        },
        {
            "sender_jid": "alice@example.com",
            "text": "Email 3",
            "ts": 123458,
            "chat_jid": "thread3",
            "msg_id": "msg3",
            "from_me": 0,
        },
    ]

    with patch("src.backend.digest_runner.WhatsAppDB", return_value=mock_whatsapp_db):
        with patch("src.backend.digest_runner.EmailDB", return_value=mock_email_db):
            with patch("src.backend.digest_runner.LLMClient", return_value=mock_llm_client):
                with patch(
                    "src.backend.digest_runner.TopicExtractor",
                    return_value=mock_topic_extractor,
                ):
                    with patch(
                        "src.backend.digest_runner.DocumentParser",
                        return_value=mock_document_parser,
                    ):
                        with patch(
                            "src.backend.digest_runner.process_groups_parallel",
                            return_value="WhatsApp digest",
                        ) as mock_process:
                            with patch(
                                "src.backend.digest_runner.extract_from_group",
                                return_value="Email digest",
                            ) as mock_extract:
                                with patch("src.backend.digest_runner.WikiCompiler"):
                                    with patch("src.backend.digest_runner.CogneeStore"):
                                        # Create fake email.db
                                        email_db_path = Path("/tmp/email.db")
                                        email_db_path.touch()

                                        try:
                                            run_daily_digest(config_with_email)

                                            # Verify email DB was queried
                                            mock_email_db.get_messages.assert_called_once_with(
                                                days=1
                                            )
                                            mock_email_db.close.assert_called_once()

                                            # Verify email groups were processed (2 domains)
                                            assert mock_extract.call_count == 2

                                            # Verify calls for both domains
                                            calls = mock_extract.call_args_list
                                            jids = [call.kwargs["group"] for call in calls]
                                            assert "email:example.com" in jids

                                        finally:
                                            if email_db_path.exists():
                                                email_db_path.unlink()


def test_email_db_missing_skip_email(config_with_email, temp_wiki_path):
    """Email DB missing: skip email with warning."""
    mock_whatsapp_db = MagicMock()
    mock_llm_client = MagicMock()
    mock_topic_extractor = MagicMock()
    mock_document_parser = MagicMock()

    with patch("src.backend.digest_runner.WhatsAppDB", return_value=mock_whatsapp_db):
        with patch("src.backend.digest_runner.LLMClient", return_value=mock_llm_client):
            with patch(
                "src.backend.digest_runner.TopicExtractor",
                return_value=mock_topic_extractor,
            ):
                with patch(
                    "src.backend.digest_runner.DocumentParser",
                    return_value=mock_document_parser,
                ):
                    with patch(
                        "src.backend.digest_runner.process_groups_parallel",
                        return_value="WhatsApp digest",
                    ) as mock_process:
                        with patch(
                            "src.backend.digest_runner.extract_from_group"
                        ) as mock_extract:
                            with patch("src.backend.digest_runner.WikiCompiler"):
                                with patch("src.backend.digest_runner.CogneeStore"):
                                    # Ensure email.db does NOT exist
                                    email_db_path = Path("/tmp/email.db")
                                    if email_db_path.exists():
                                        email_db_path.unlink()

                                    run_daily_digest(config_with_email)

                                    # Verify email extraction was NOT called
                                    mock_extract.assert_not_called()

                                    # Verify WhatsApp processing still happened
                                    mock_process.assert_called_once()


def test_email_disabled_skip_email(config_without_email, temp_wiki_path):
    """Email disabled in config: skip email entirely."""
    mock_whatsapp_db = MagicMock()
    mock_llm_client = MagicMock()
    mock_topic_extractor = MagicMock()
    mock_document_parser = MagicMock()

    with patch("src.backend.digest_runner.WhatsAppDB", return_value=mock_whatsapp_db):
        with patch("src.backend.digest_runner.LLMClient", return_value=mock_llm_client):
            with patch(
                "src.backend.digest_runner.TopicExtractor",
                return_value=mock_topic_extractor,
            ):
                with patch(
                    "src.backend.digest_runner.DocumentParser",
                    return_value=mock_document_parser,
                ):
                    with patch(
                        "src.backend.digest_runner.process_groups_parallel",
                        return_value="WhatsApp digest",
                    ) as mock_process:
                        with patch(
                            "src.backend.digest_runner.extract_from_group"
                        ) as mock_extract:
                            with patch("src.backend.digest_runner.WikiCompiler"):
                                with patch("src.backend.digest_runner.CogneeStore"):
                                    run_daily_digest(config_without_email)

                                    # Verify email extraction was NOT called
                                    mock_extract.assert_not_called()

                                    # Verify WhatsApp processing happened
                                    mock_process.assert_called_once()


def test_extract_from_group_whatsapp_compatible(config_with_email):
    """extract_from_group with db=None and messages works (MessageSource compat)."""
    from src.backend.cli import extract_from_group

    messages = [
        {
            "sender_jid": "alice@example.com",
            "sender_name": "Alice",
            "text": "Test email",
            "ts": 123456,
            "chat_jid": "thread1",
            "msg_id": "msg1",
            "from_me": 0,
        }
    ]

    with patch("src.backend.cli.LLMClient") as mock_llm_cls:
        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm
        mock_llm.extract_batch.return_value = ([], [])

        result = extract_from_group(
            db=None,
            group="email:example.com",
            group_name="Email: example.com",
            messages=messages,
            config=config_with_email,
            use_llm=False,  # Skip LLM for speed
        )

        # Should not crash, returns some output
        assert isinstance(result, str)


def test_email_grouping_by_domain(config_with_email, temp_wiki_path):
    """Email messages grouped by sender domain."""
    mock_whatsapp_db = MagicMock()
    mock_email_db = MagicMock()
    mock_llm_client = MagicMock()
    mock_topic_extractor = MagicMock()
    mock_document_parser = MagicMock()

    # Mock email messages from 3 domains
    mock_email_db.get_messages.return_value = [
        {"sender_jid": "alice@foo.com", "text": "Foo 1", "ts": 1},
        {"sender_jid": "bob@bar.com", "text": "Bar 1", "ts": 2},
        {"sender_jid": "charlie@foo.com", "text": "Foo 2", "ts": 3},
        {"sender_jid": "dave@baz.com", "text": "Baz 1", "ts": 4},
        {"sender_jid": "eve@bar.com", "text": "Bar 2", "ts": 5},
    ]

    with patch("src.backend.digest_runner.WhatsAppDB", return_value=mock_whatsapp_db):
        with patch("src.backend.digest_runner.EmailDB", return_value=mock_email_db):
            with patch("src.backend.digest_runner.LLMClient", return_value=mock_llm_client):
                with patch(
                    "src.backend.digest_runner.TopicExtractor",
                    return_value=mock_topic_extractor,
                ):
                    with patch(
                        "src.backend.digest_runner.DocumentParser",
                        return_value=mock_document_parser,
                    ):
                        with patch(
                            "src.backend.digest_runner.process_groups_parallel",
                            return_value="WhatsApp",
                        ):
                            with patch(
                                "src.backend.digest_runner.extract_from_group",
                                return_value="Email",
                            ) as mock_extract:
                                with patch("src.backend.digest_runner.WikiCompiler"):
                                    with patch("src.backend.digest_runner.CogneeStore"):
                                        email_db_path = Path("/tmp/email.db")
                                        email_db_path.touch()

                                        try:
                                            run_daily_digest(config_with_email)

                                            # Should create 3 email groups (foo.com, bar.com, baz.com)
                                            assert mock_extract.call_count == 3

                                            # Verify group names
                                            calls = mock_extract.call_args_list
                                            names = [call.kwargs["group_name"] for call in calls]
                                            assert "Email: foo.com" in names
                                            assert "Email: bar.com" in names
                                            assert "Email: baz.com" in names

                                            # Verify message counts per domain
                                            foo_call = next(
                                                c
                                                for c in calls
                                                if c.kwargs["group_name"] == "Email: foo.com"
                                            )
                                            bar_call = next(
                                                c
                                                for c in calls
                                                if c.kwargs["group_name"] == "Email: bar.com"
                                            )
                                            baz_call = next(
                                                c
                                                for c in calls
                                                if c.kwargs["group_name"] == "Email: baz.com"
                                            )

                                            assert len(foo_call.kwargs["messages"]) == 2
                                            assert len(bar_call.kwargs["messages"]) == 2
                                            assert len(baz_call.kwargs["messages"]) == 1

                                        finally:
                                            if email_db_path.exists():
                                                email_db_path.unlink()

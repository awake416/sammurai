# tests/test_cli.py
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import pytest
from unittest.mock import patch, MagicMock
from src.backend.cli import list_groups, main, get_date_range, display_action_items
from src.backend.models import ActionableItem, Priority, TaskCategory


def test_get_date_range():
    # Test with multiple messages
    messages = [
        {"timestamp": "1709251200"},  # 2024-03-01
        {"timestamp": "1710028800"},  # 2024-03-10
    ]
    assert get_date_range(messages) == "2024-03-01 to 2024-03-10"

    # Test with single message
    messages = [{"timestamp": "1709251200"}]
    assert get_date_range(messages) == "2024-03-01"

    # Test with same day
    messages = [
        {"timestamp": "1709251200"},
        {"timestamp": "1709254800"},  # Same day, different time
    ]
    assert get_date_range(messages) == "2024-03-01"

    # Test with milliseconds
    messages = [
        {"timestamp": "1709251200000"},
        {"timestamp": "1710028800000"},
    ]
    assert get_date_range(messages) == "2024-03-01 to 2024-03-10"

    # Test with empty list
    assert get_date_range([]) == ""

    # Test with invalid timestamps
    messages = [{"timestamp": "invalid"}]
    assert get_date_range(messages) == ""


@patch("src.backend.cli.WhatsAppDB")
def test_list_groups_with_activity(mock_db_class):
    mock_db = mock_db_class.return_value
    mock_db.get_groups.return_value = [
        {"jid": "1@g.us", "name": "Group 1", "last_activity": "2026-03-07 10:00:00"}
    ]

    # Should not raise exception
    list_groups(mock_db, days_active=30)
    mock_db.get_groups.assert_called_with(days_active=30)


@patch("src.backend.cli.validate_db_path", return_value=Path("/tmp/test.db"))
@patch("src.backend.cli.WhatsAppDB")
@patch("src.backend.cli.load_config")
@patch("argparse.ArgumentParser.parse_args")
def test_digest_without_llm_exits(
    mock_parse_args, mock_load_config, mock_db_class, mock_validate_db
):
    # Mock config to have LLM disabled
    mock_load_config.return_value = {"parser": {"use_llm": False}, "llm": {}}

    # Mock arguments: --digest but NO --use-llm
    mock_parse_args.return_value = argparse.Namespace(
        group_name="Test Group",
        list=False,
        days_active=None,
        all=False,
        groups=None,
        parallel=None,
        parallel_batches=None,
        limit=100,
        days=None,
        batch_size=50,
        db_path="~/.wacli/wacli.db",
        use_llm=False,
        no_llm=False,
        digest=True,
        topics_only=False,
        full=False,
        debug=False,
    )

    # Should exit with code 1
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 1


@patch("src.backend.cli.validate_db_path", return_value=Path("/tmp/test.db"))
@patch("src.backend.cli.WhatsAppDB")
@patch("src.backend.cli.load_config")
@patch("src.backend.cli.LLMClient")
@patch("src.backend.cli.TopicExtractor")
@patch("argparse.ArgumentParser.parse_args")
def test_topics_only_happy_path(
    mock_parse_args,
    mock_topic_extractor_class,
    mock_llm_client_class,
    mock_load_config,
    mock_db_class,
    mock_validate_db,
):
    # Mock config
    mock_load_config.return_value = {
        "parser": {"use_llm": True},
        "llm": {"model": "test-model"},
    }

    # Mock arguments: --topics-only and --use-llm
    mock_parse_args.return_value = argparse.Namespace(
        group_name="Test Group",
        list=False,
        days_active=None,
        all=False,
        groups=None,
        parallel=None,
        parallel_batches=None,
        limit=100,
        days=None,
        batch_size=50,
        db_path="~/.wacli/wacli.db",
        use_llm=True,
        no_llm=False,
        digest=False,
        topics_only=True,
        full=False,
        debug=False,
    )

    # Setup mocks
    mock_db = mock_db_class.return_value
    mock_db.get_group_jid.return_value = "123@g.us"
    mock_db._resolve_group_name.return_value = "Test Group"
    mock_db.get_messages_by_group.return_value = [
        {
            "id": "msg1",
            "message": "msg1",
            "sender": "user1",
            "timestamp": "1234567890",
            "group_name": "Test Group",
            "group_jid": "123@g.us",
        }
    ]

    mock_llm_client = mock_llm_client_class.return_value
    mock_llm_client.extract_batch.return_value = []

    mock_topic_extractor = mock_topic_extractor_class.return_value
    mock_topic_extractor.extract_topics.return_value = []
    mock_topic_extractor.tag_items_with_topics.return_value = []
    mock_topic_extractor.generate_digest.return_value = "MOCK DIGEST"

    # Mock display_action_items to verify it's NOT called
    with patch("src.backend.cli.display_action_items") as mock_display:
        main()

        # Verify generate_digest was called
        mock_topic_extractor.generate_digest.assert_called()

        # Verify display_action_items was NOT called (because of topics_only early return)
        mock_display.assert_not_called()


@patch("src.backend.cli.validate_db_path", return_value=Path("/tmp/test.db"))
@patch("src.backend.cli.WhatsAppDB")
@patch("src.backend.cli.load_config")
@patch("argparse.ArgumentParser.parse_args")
def test_days_option_passed_to_db(
    mock_parse_args, mock_load_config, mock_db_class, mock_validate_db
):
    # Mock config
    mock_load_config.return_value = {"parser": {"use_llm": False}, "llm": {}}

    # Mock arguments: --days 7
    mock_parse_args.return_value = argparse.Namespace(
        group_name="Test Group",
        list=False,
        days_active=None,
        all=False,
        groups=None,
        parallel=None,
        parallel_batches=None,
        limit=100,
        days=7,
        batch_size=50,
        db_path="~/.wacli/wacli.db",
        use_llm=False,
        no_llm=False,
        digest=False,
        topics_only=False,
        full=False,
        debug=False,
    )

    mock_db = mock_db_class.return_value
    mock_db.get_group_jid.return_value = "123@g.us"
    mock_db.get_messages_by_group.return_value = []

    main()

    # Verify get_messages_by_group was called with days=7
    mock_db.get_messages_by_group.assert_called()
    args, kwargs = mock_db.get_messages_by_group.call_args
    # It might be passed as positional or keyword argument
    passed_days = kwargs.get("days")
    if passed_days is None and len(args) >= 3:
        passed_days = args[2]
    assert passed_days == 7


def test_display_action_items_with_tags():
    items = [
        ActionableItem(
            task="Task 1",
            priority=Priority.HIGH,
            category=TaskCategory.WORK,
            topic_tags=["Project A"],
            sender="Alice",
            timestamp="1709251200",
        ),
        ActionableItem(
            task="Task 2",
            priority=Priority.LOW,
            category=TaskCategory.OTHER,
            topic_tags=["Project B", "Urgent"],
            sender="Bob",
            timestamp="1709254800",
        ),
    ]

    output = display_action_items(items, "for testing")

    # Check title
    assert "Found 2 tasks for testing:" in output

    # Check sorting (High should be before Low)
    lines = output.split("\n")
    # Header is line 2 (index 2), items start at line 3 (index 3)
    # Line 0: Title
    # Line 1: ===
    # Line 2: Header
    # Line 3: Task 1 (High)
    # Line 4: Task 2 (Low)
    assert "[Tags: Project A] Task 1" in lines[3]
    assert "[Tags: Project B, Urgent] Task 2" in lines[4]
    assert "High" in lines[3]
    assert "Low" in lines[4]
    # Compact format doesn't have Category
    assert "Work" not in lines[3]
    assert "Other" not in lines[4]

    # Test full format
    output_full = display_action_items(items, "for testing", full=True)
    lines_full = output_full.split("\n")
    assert "Work" in lines_full[3]
    assert "Other" in lines_full[4]

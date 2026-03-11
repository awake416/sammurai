import pytest
from unittest.mock import MagicMock, patch
from src.backend.topic_extractor import TopicExtractor
from src.backend.models import (
    Message,
    TopicItem,
    ActionableItem,
    Priority,
    TaskCategory,
)
from src.backend.llm_client import LLMClient


@pytest.fixture
def mock_llm_client():
    client = MagicMock(spec=LLMClient)
    return client


@pytest.fixture
def topic_extractor(mock_llm_client):
    return TopicExtractor(mock_llm_client)


def test_extract_topics(topic_extractor, mock_llm_client):
    messages = [
        Message(
            id="1",
            message="The parking lot is full again",
            sender="Alice",
            timestamp="123",
        ),
        Message(
            id="2", message="Someone parked in my spot", sender="Bob", timestamp="124"
        ),
    ]

    mock_llm_client.extract_topics.return_value = {
        "topics": [
            {
                "topic": "Parking",
                "summary": "Discussion about parking lot being full.",
                "message_count": 2,
                "sample_messages": [
                    "The parking lot is full again",
                    "Someone parked in my spot",
                ],
            }
        ]
    }

    topics = topic_extractor.extract_topics(messages)

    assert len(topics) == 1
    assert topics[0].topic == "Parking"
    assert topics[0].message_count == 2
    mock_llm_client.extract_topics.assert_called_once()


@patch("src.backend.topic_extractor.httpx.get")
def test_summarize_document(mock_get, topic_extractor, mock_llm_client):
    mock_response = MagicMock()
    mock_response.text = "<html><body><h1>Meeting Minutes</h1><p>The meeting was about the new park. Deadline is 2026-04-01.</p></body></html>"
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    mock_llm_client.summarize_document.return_value = {
        "title": "Meeting Minutes",
        "summary": "The meeting discussed the new park project.",
        "key_dates": ["2026-04-01"],
    }

    summary = topic_extractor.summarize_document("https://example.com/doc")

    assert summary.title == "Meeting Minutes"
    assert "park project" in summary.summary
    assert "2026-04-01" in summary.key_dates
    assert str(summary.resource_url) == "https://example.com/doc"


@patch("src.backend.topic_extractor.httpx.get")
def test_summarize_document_fetch_error(mock_get, topic_extractor):
    mock_get.side_effect = Exception("Connection error")

    summary = topic_extractor.summarize_document("https://example.com/bad")

    assert summary.title == "Error"
    assert "Connection error" in summary.summary
    assert str(summary.resource_url) == "https://example.com/bad"


def test_generate_digest(topic_extractor):

    action_items = [
        ActionableItem(
            task="Fix the leak", priority=Priority.HIGH, category=TaskCategory.COMMUNITY
        ),
        ActionableItem(
            task="Pay the bill",
            priority=Priority.MEDIUM,
            category=TaskCategory.BILLS,
            deadline="2026-03-15",
        ),
    ]
    topics = [
        TopicItem(
            topic="Maintenance",
            summary="Issues with leaks and lobby maintenance.",
            message_count=5,
            sample_messages=["Leak in lobby"],
        ),
    ]

    digest = topic_extractor.generate_digest(action_items, topics)

    assert "# 🏘️ Community digest" in digest
    assert "Trending Topics" in digest
    assert "Maintenance" in digest
    assert "## 📋 Tasks" in digest
    assert "### General" in digest
    assert "- [High] Fix the leak" in digest
    assert "- [Medium] Pay the bill - Due: 2026-03-15" in digest


def test_generate_digest_sorting(topic_extractor):
    topics = [
        TopicItem(
            topic="Low Count",
            summary="Summary 1",
            message_count=2,
        ),
        TopicItem(
            topic="High Count",
            summary="Summary 2",
            message_count=10,
        ),
        TopicItem(
            topic="Medium Count",
            summary="Summary 3",
            message_count=5,
        ),
    ]

    digest = topic_extractor.generate_digest([], topics)

    # Check order in digest
    lines = [line for line in digest.split("\n") if line.startswith("### ")]
    assert "High Count" in lines[0]
    assert "Medium Count" in lines[1]
    assert "Low Count" in lines[2]


def test_generate_digest_with_tasks(topic_extractor):
    action_items = [
        ActionableItem(
            task="Fix the leak",
            priority=Priority.HIGH,
            category=TaskCategory.COMMUNITY,
            topic_tags=["Maintenance"],
        ),
        ActionableItem(
            task="Pay the bill",
            priority=Priority.MEDIUM,
            category=TaskCategory.BILLS,
            deadline="2026-03-15",
            topic_tags=["Finance"],
        ),
    ]
    topics = [
        TopicItem(
            topic="Maintenance",
            summary="Issues with leaks.",
            message_count=5,
        ),
        TopicItem(
            topic="Finance",
            summary="Money matters.",
            message_count=2,
        ),
    ]

    digest = topic_extractor.generate_digest(action_items, topics)

    assert "### Maintenance (5 messages)" in digest
    assert "Issues with leaks." in digest
    assert "## 📋 Tasks" in digest
    assert "### Maintenance" in digest
    assert "- [High] Fix the leak" in digest

    assert "### Finance (2 messages)" in digest
    assert "Money matters." in digest
    assert "### Finance" in digest
    assert "- [Medium] Pay the bill - Due: 2026-03-15" in digest


def test_generate_digest_with_group_and_date(topic_extractor):
    action_items = [
        ActionableItem(
            task="Fix the leak", priority=Priority.HIGH, category=TaskCategory.COMMUNITY
        ),
    ]
    topics = [
        TopicItem(
            topic="Maintenance",
            summary="Issues with leaks and lobby maintenance.",
            message_count=5,
            sample_messages=["Leak in lobby"],
        ),
    ]

    digest = topic_extractor.generate_digest(
        action_items,
        topics,
        group_name="Test Group",
        date_range="2026-03-01 to 2026-03-10",
    )

    assert "# 🏘️ Community digest : Test Group [2026-03-01 to 2026-03-10]" in digest
    assert "Maintenance" in digest
    assert "## 📋 Tasks" in digest
    assert "- [High] Fix the leak" in digest


def test_tag_items_with_topics(topic_extractor, mock_llm_client):
    action_items = [
        ActionableItem(
            task="Fix the leak", priority=Priority.HIGH, category=TaskCategory.COMMUNITY
        ),
    ]
    topics = [
        TopicItem(
            topic="Maintenance",
            summary="Issues with leaks and lobby maintenance.",
            message_count=5,
            sample_messages=["Leak in lobby"],
        ),
    ]

    mock_llm_client.tag_items_with_topics.return_value = {
        "tagged_items": [{"item_index": 0, "topics": ["Maintenance"]}]
    }

    tagged_items = topic_extractor.tag_items_with_topics(action_items, topics)

    assert len(tagged_items) == 1
    assert "Maintenance" in tagged_items[0].topic_tags
    mock_llm_client.tag_items_with_topics.assert_called_once()


def test_generate_digest_with_aggregated_tasks(topic_extractor):
    action_items = [
        ActionableItem(
            task="Coordinate with municipal authorities for road repair",
            priority=Priority.HIGH,
            category=TaskCategory.COMMUNITY,
            topic_tags=["Road Work"],
        ),
        ActionableItem(
            task="Coordinate with municipal for road repair",
            priority=Priority.HIGH,
            category=TaskCategory.COMMUNITY,
            topic_tags=["Road Work"],
        ),
        ActionableItem(
            task="Coordinate with authorities for road repair",
            priority=Priority.HIGH,
            category=TaskCategory.COMMUNITY,
            topic_tags=["Road Work"],
        ),
        ActionableItem(
            task="Install speed breakers near the park entrance",
            priority=Priority.MEDIUM,
            category=TaskCategory.COMMUNITY,
            topic_tags=["Road Work"],
        ),
    ]

    digest = topic_extractor.generate_digest(action_items, [])

    assert "### Road Work" in digest
    assert (
        "- [High] Coordinate with municipal authorities for road repair (3 similar tasks)"
        in digest
    )
    assert "- [Medium] Install speed breakers near the park entrance" in digest

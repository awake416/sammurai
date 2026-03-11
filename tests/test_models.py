import pytest
from pydantic import ValidationError
from src.backend.models import (
    Resource,
    ResourceType,
    ActionableItem,
    Priority,
    TopicItem,
    DocumentSummary,
    TopicSummary,
)
from src.backend.cli import format_item_tsv


def test_actionable_item_with_resources():
    resource = Resource(
        type=ResourceType.URL, value="https://example.com", description="Project spec"
    )

    item = ActionableItem(task="Review spec", resources=[resource])

    assert len(item.resources) == 1
    assert item.resources[0].type == ResourceType.URL


def test_resource_type_other():
    resource = Resource(type=ResourceType.OTHER, value="Some other resource")
    assert resource.type == ResourceType.OTHER
    assert resource.value == "Some other resource"


def test_resource_type_image():
    resource = Resource(
        type=ResourceType.IMAGE,
        value="https://example.com/image.png",
        description="A screenshot",
    )
    assert resource.type == ResourceType.IMAGE
    assert resource.value == "https://example.com/image.png"
    assert resource.description == "A screenshot"


def test_resource_type_invalid_value():
    with pytest.raises(ValidationError) as excinfo:
        Resource(type="photo", value="image.png")
    assert "photo" in str(excinfo.value)


def test_cli_format_with_resources():
    resource = Resource(type=ResourceType.URL, value="https://example.com")
    item = ActionableItem(
        task="Test task",
        priority=Priority.HIGH,
        assignee="Alice",
        sender="Bob",
        resources=[resource],
        message_ref=1,
    )

    # Default is compact
    tsv = format_item_tsv(item)
    parts = tsv.split("\t")

    # REF, PRIORITY, TASK, DEADLINE
    assert parts[0] == "#1"
    assert parts[1] == "High"
    assert parts[2] == "Test task"
    assert parts[3] == "-"

    # Full format
    tsv_full = format_item_tsv(item, compact=False)
    parts_full = tsv_full.split("\t")

    # REF, PRIORITY, CATEGORY, TASK, CONTEXT, ASSIGNEE, DEADLINE, SENDER, RESOURCES, DATE
    assert parts_full[0] == "#1"
    assert parts_full[1] == "High"
    assert parts_full[2] == "Other"
    assert parts_full[3] == "Test task"
    assert parts_full[4] == "-"
    assert parts_full[5] == "Alice"
    assert parts_full[7] == "Bob"
    assert parts_full[8] == "https://example.com"


def test_topic_summary_models():
    topic = TopicItem(
        topic="Parking",
        summary="Discussion about parking lot being full.",
        message_count=5,
        sample_messages=["Where to park?"],
    )
    doc = DocumentSummary(
        resource_url="https://example.com/rules.pdf",
        title="Parking Rules",
        summary="Rules for parking",
    )

    summary = TopicSummary(
        group_name="Community",
        topics=[topic],
        document_summaries=[doc],
        date_range="Last 7 days",
    )

    assert summary.group_name == "Community"
    assert len(summary.topics) == 1
    assert summary.topics[0].topic == "Parking"
    assert len(summary.document_summaries) == 1
    assert summary.document_summaries[0].title == "Parking Rules"


def test_actionable_item_topic_tags():
    item = ActionableItem(
        task="Fix parking sign", topic_tags=["Parking", "Maintenance"]
    )
    assert "Parking" in item.topic_tags
    assert "Maintenance" in item.topic_tags


def test_topic_summary_default_values():
    summary = TopicSummary(group_name="Test Group", date_range="Today")
    assert summary.topics == []
    assert summary.document_summaries == []


def test_topic_item_default_values():
    topic = TopicItem(topic="Test Topic", summary="Test summary", message_count=1)
    assert topic.sample_messages == []


def test_actionable_item_default_values():
    item = ActionableItem(task="Test Task")
    assert item.topic_tags == []


def test_document_summary_url_validation():
    # Valid URL
    DocumentSummary(
        resource_url="https://example.com/doc.pdf",
        title="Doc",
        summary="Summary",
    )

    # Invalid URL
    with pytest.raises(ValidationError):
        DocumentSummary(
            resource_url="not-a-url",
            title="Doc",
            summary="Summary",
        )


def test_topic_item_message_count_constraint():
    # Valid count
    TopicItem(topic="Topic", summary="Summary", message_count=1)

    # Invalid count (less than 1)
    with pytest.raises(ValidationError):
        TopicItem(topic="Topic", summary="Summary", message_count=0)


def test_actionable_item_topic_tags_validation():
    # Valid tags
    ActionableItem(task="Task", topic_tags=["Tag1", "Tag2"])

    # Invalid tag (empty string)
    with pytest.raises(ValidationError) as excinfo:
        ActionableItem(task="Task", topic_tags=[""])
    assert "topic_tags[0]: tag cannot be empty or whitespace only, got ''" in str(
        excinfo.value
    )

    # Invalid tag (whitespace only)
    with pytest.raises(ValidationError) as excinfo:
        ActionableItem(task="Task", topic_tags=["   "])
    assert "topic_tags[0]: tag cannot be empty or whitespace only, got '   '" in str(
        excinfo.value
    )


def test_topic_item_requires_summary():
    # Missing summary should raise ValidationError
    with pytest.raises(ValidationError):
        TopicItem(topic="Topic", message_count=1)


def test_topic_summary_date_range_constraint():
    # Valid date range
    TopicSummary(group_name="Group", date_range="7 days")

    # Invalid date range (empty string)
    with pytest.raises(ValidationError):
        TopicSummary(group_name="Group", date_range="")

    # Invalid date range (whitespace only)
    with pytest.raises(ValidationError):
        TopicSummary(group_name="Group", date_range="   ")

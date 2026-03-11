from datetime import timezone
from unittest.mock import patch, MagicMock
from src.backend.llm_client import LLMClient


@patch("src.backend.llm_client.litellm.completion")
def test_extract_resources(mock_completion):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="""
        {
            "is_action_item": true,
            "task": "Review doc",
            "assignee": "John",
            "deadline": null,
            "priority": "Medium",
            "confidence": 0.9,
            "resources": [
                {"type": "url", "value": "https://example.com", "description": "Spec"}
            ]
        }
        """
            )
        )
    ]
    mock_completion.return_value = mock_response

    client = LLMClient(base_url="https://test", api_key="test")
    result = client.extract_action_item("John please review https://example.com")

    assert result is not None
    assert "resources" in result
    assert len(result["resources"]) == 1
    assert result["resources"][0]["type"] == "url"


@patch("src.backend.llm_client.litellm.completion")
def test_extract_batch_true_batching(mock_completion):
    """Test that extract_batch sends all messages in one LLM call."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="""
        {
            "action_items": [
                {
                    "is_action_item": true,
                    "task": "Review doc",
                    "assignee": "John",
                    "deadline": null,
                    "priority": "Medium",
                    "confidence": 0.9,
                    "resources": [],
                    "original_message_index": 0
                }
            ]
        }
        """
            )
        )
    ]
    mock_completion.return_value = mock_response

    client = LLMClient(base_url="https://test", api_key="test")
    messages = [
        {
            "message": "John please review the doc",
            "sender": "Alice",
            "timestamp": "2026-03-07T10:00:00Z",
        },
        {"message": "Thanks!", "sender": "John", "timestamp": "2026-03-07T10:05:00Z"},
    ]

    result = client.extract_batch(messages)

    # Should have only called the LLM ONCE (not once per message)
    assert mock_completion.call_count == 1, (
        "Should batch all messages in a single LLM call"
    )

    assert len(result) == 1
    assert result[0]["task"] == "Review doc"
    assert result[0]["assignee"] == "John"
    assert result[0]["original_message"] == "John please review the doc"


def test_llm_client_model_passing():
    """Test that LLMClient trusts the model name and passes it directly."""
    # Case 1: model has no prefix -> should NOT prepend 'openai/' anymore
    client = LLMClient(base_url="https://test", api_key="test", model="glm-5")
    assert client.model == "glm-5"

    # Case 2: model already has prefix -> should remain unchanged
    client = LLMClient(
        base_url="https://test", api_key="test", model="anthropic/claude-3"
    )
    assert client.model == "anthropic/claude-3"

    # Case 3: model with multiple slashes
    client = LLMClient(
        base_url="https://test", api_key="test", model="custom/provider/model"
    )
    assert client.model == "custom/provider/model"


@patch("src.backend.llm_client.litellm.completion")
def test_llm_client_rejects_jokes(mock_completion):
    """Test that the client rejects jokes based on is_action_item flag or low confidence."""
    client = LLMClient(base_url="https://test", api_key="test")

    # Case 1: LLM correctly identifies it as NOT an action item
    mock_response_1 = MagicMock()
    mock_response_1.choices = [
        MagicMock(
            message=MagicMock(content='{"is_action_item": false, "confidence": 0.9}')
        )
    ]
    mock_completion.return_value = mock_response_1
    result_1 = client.extract_action_item("Update LinkedIn profile to include CEO")
    assert result_1["is_action_item"] is False

    # Case 2: LLM thinks it's an action item but confidence is below threshold (0.75)
    # We use extract_batch here because it's where the filtering logic resides
    mock_response_2 = MagicMock()
    mock_response_2.choices = [
        MagicMock(
            message=MagicMock(
                content="""
                {
                    "action_items": [
                        {
                            "is_action_item": true,
                            "task": "Update LinkedIn",
                            "confidence": 0.6,
                            "original_message_index": 0
                        }
                    ]
                }
                """
            )
        )
    ]
    mock_completion.return_value = mock_response_2
    messages = [{"message": "Update LinkedIn profile to include CEO"}]
    result_2 = client.extract_batch(messages)

    # Should be empty because confidence 0.6 < 0.75
    assert len(result_2) == 0


@patch("src.backend.llm_client.litellm.completion")
def test_extract_topics(mock_completion):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"topics": [{"topic": "Parking", "summary": "Parking issues", "message_count": 2, "sample_messages": ["msg1"]}]}'
            )
        )
    ]
    mock_completion.return_value = mock_response

    client = LLMClient(base_url="https://test", api_key="test")
    messages = [{"message": "msg1", "sender": "Alice"}]
    result = client.extract_topics(messages)

    assert result is not None
    assert "topics" in result
    assert result["topics"][0]["topic"] == "Parking"
    assert result["topics"][0]["summary"] == "Parking issues"


@patch("src.backend.llm_client.litellm.completion")
def test_summarize_document(mock_completion):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"title": "Doc Title", "summary": "Summary text", "key_dates": ["2026-01-01"]}'
            )
        )
    ]
    mock_completion.return_value = mock_response

    client = LLMClient(base_url="https://test", api_key="test")
    result = client.summarize_document("Some content", url="https://example.com")

    assert result is not None
    assert result["title"] == "Doc Title"
    assert result["summary"] == "Summary text"


@patch("src.backend.llm_client.litellm.completion")
def test_tag_items_with_topics(mock_completion):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"tagged_items": [{"item_index": 0, "topics": ["Parking"]}]}'
            )
        )
    ]
    mock_completion.return_value = mock_response

    client = LLMClient(base_url="https://test", api_key="test")
    items = [{"task": "Fix parking"}]
    topic_names = ["Parking", "Security"]
    result = client.tag_items_with_topics(items, topic_names)

    assert result is not None
    assert "tagged_items" in result
    assert result["tagged_items"][0]["topics"] == ["Parking"]


@patch("src.backend.llm_client.litellm.completion")
def test_extract_action_item_injects_current_date(mock_completion):
    """Test that the current date is injected into the system prompt."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(content='{"is_action_item": false, "confidence": 1.0}')
        )
    ]
    mock_completion.return_value = mock_response

    with patch("src.backend.llm_client.datetime") as mock_datetime:
        # Mock datetime.now(timezone.utc) to return a fixed date
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-03-10"
        mock_datetime.now.return_value = mock_now

        client = LLMClient(base_url="https://test", api_key="test")
        client.extract_action_item("test message")

        # Verify timezone.utc was passed to datetime.now()
        mock_datetime.now.assert_called_with(timezone.utc)

        # Check the system prompt passed to litellm
        args, kwargs = mock_completion.call_args
        system_prompt = kwargs["messages"][0]["content"]
        assert "The current date is 2026-03-10" in system_prompt


@patch("src.backend.llm_client.litellm.completion")
def test_extract_batch_injects_current_date(mock_completion):
    """Test that the current date is injected into the batch system prompt."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content='{"action_items": []}'))
    ]
    mock_completion.return_value = mock_response

    with patch("src.backend.llm_client.datetime") as mock_datetime:
        # Mock datetime.now(timezone.utc) to return a fixed date
        mock_now = MagicMock()
        mock_now.strftime.return_value = "2026-03-10"
        mock_datetime.now.return_value = mock_now

        client = LLMClient(base_url="https://test", api_key="test")
        client.extract_batch([{"message": "test message"}])

        # Verify timezone.utc was passed to datetime.now()
        mock_datetime.now.assert_called_with(timezone.utc)

        # Check the system prompt passed to litellm
        args, kwargs = mock_completion.call_args
        system_prompt = kwargs["messages"][0]["content"]
        assert "The current date is 2026-03-10" in system_prompt

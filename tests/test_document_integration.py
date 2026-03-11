import pytest
from unittest.mock import MagicMock, patch
import os
from src.backend.topic_extractor import TopicExtractor
from src.backend.document_parser import DocumentParser
from src.backend.models import Message, DocumentSummary
from src.backend.llm_client import LLMClient


@pytest.fixture
def mock_llm_client():
    return MagicMock(spec=LLMClient)


@pytest.fixture
def mock_document_parser():
    return MagicMock(spec=DocumentParser)


@pytest.fixture
def topic_extractor(mock_llm_client, mock_document_parser):
    return TopicExtractor(mock_llm_client, document_parser=mock_document_parser)


def test_extract_topics_no_enrichment(
    topic_extractor, mock_llm_client, mock_document_parser
):
    messages = [
        Message(
            id="1",
            message="Please see attached document",
            sender="Alice",
            timestamp="123",
            local_path="test.pdf",
            media_type="document",
            filename="test.pdf",
        )
    ]

    mock_llm_client.extract_topics.return_value = {"topics": []}

    topic_extractor.extract_topics(messages)

    # Verify that the message passed to LLM is NOT enriched by TopicExtractor
    args, _ = mock_llm_client.extract_topics.call_args
    msg_dicts = args[0]
    assert msg_dicts[0]["message"] == "Please see attached document"
    mock_document_parser.get_document_content.assert_not_called()


@patch("os.path.exists")
def test_summarize_document_local_file(
    mock_exists, topic_extractor, mock_llm_client, mock_document_parser
):
    mock_exists.return_value = True
    mock_document_parser.extract_text.return_value = "Local document content."
    mock_llm_client.summarize_document.return_value = {
        "title": "Local Doc",
        "summary": "Summary of local doc",
        "key_dates": [],
    }

    summary = topic_extractor.summarize_document(
        url="https://example.com/doc", file_path="local.pdf"
    )

    assert summary.title == "Local Doc"
    assert summary.summary == "Summary of local doc"
    mock_document_parser.extract_text.assert_called_once_with("local.pdf")
    mock_llm_client.summarize_document.assert_called_once_with(
        "Local document content.", "https://example.com/doc"
    )

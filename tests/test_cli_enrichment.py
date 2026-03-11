# tests/test_cli_enrichment.py
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch
from src.backend.cli import enrich_messages_with_docs
from src.backend.document_parser import DocumentParser


def test_enrich_messages_with_docs():
    # Setup mock document parser
    mock_parser = MagicMock(spec=DocumentParser)
    mock_parser.extract_text.return_value = "Extracted text content"

    # Test messages
    messages = [
        {
            "id": "msg1",
            "message": "Check this doc",
            "media_type": "document",
            "local_path": "/path/to/doc.pdf",
        },
        {
            "id": "msg2",
            "message": "Regular message",
            "media_type": "text",
            "local_path": None,
        },
        {
            "id": "msg3",
            "message": "Another doc",
            "media_type": "document",
            "local_path": "/path/to/other.txt",  # Not a PDF
        },
    ]

    enriched = enrich_messages_with_docs(messages, mock_parser)

    # Verify msg1 was enriched
    assert (
        "[Extracted Document Content]: Extracted text content" in enriched[0]["message"]
    )
    mock_parser.extract_text.assert_called_once_with("/path/to/doc.pdf")

    # Verify msg2 was NOT enriched
    assert enriched[1]["message"] == "Regular message"

    # Verify msg3 was NOT enriched
    assert enriched[2]["message"] == "Another doc"


def test_enrich_messages_with_docs_error_handling():
    # Setup mock document parser that raises an error
    mock_parser = MagicMock(spec=DocumentParser)
    mock_parser.extract_text.side_effect = Exception("Extraction failed")

    messages = [
        {
            "id": "msg1",
            "message": "Check this doc",
            "media_type": "document",
            "local_path": "/path/to/doc.pdf",
        }
    ]

    # Should not raise exception, just log warning and return original message
    enriched = enrich_messages_with_docs(messages, mock_parser)
    assert enriched[0]["message"] == "Check this doc"

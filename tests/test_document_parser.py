import os
import pytest
from unittest.mock import MagicMock, patch
from src.backend.document_parser import DocumentParser
from src.backend.models import Message


def test_extract_text_file_not_found():
    parser = DocumentParser()
    with pytest.raises(FileNotFoundError):
        parser.extract_text("non_existent.pdf")


def test_extract_text_not_a_pdf():
    parser = DocumentParser()
    # Create a dummy file
    with open("test.txt", "w") as f:
        f.write("test")

    try:
        with pytest.raises(ValueError, match="File is not a PDF"):
            parser.extract_text("test.txt")
    finally:
        if os.path.exists("test.txt"):
            os.remove("test.txt")


@patch("pdfplumber.open")
@patch("os.path.exists")
def test_extract_text_success(mock_exists, mock_pdf_open):
    mock_exists.return_value = True

    # Mock pdfplumber
    mock_pdf = MagicMock()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Extracted text"
    mock_pdf.pages = [mock_page]
    mock_pdf_open.return_value.__enter__.return_value = mock_pdf

    parser = DocumentParser()
    text = parser.extract_text("test.pdf")

    assert text == "Extracted text"
    mock_pdf_open.assert_called_once_with("test.pdf")


def test_get_document_content_no_path():
    parser = DocumentParser()
    message = Message(id="1", message="test", timestamp="2023-01-01")
    assert parser.get_document_content(message) is None


@patch("src.backend.document_parser.DocumentParser.extract_text")
@patch("os.path.exists")
def test_get_document_content_success(mock_exists, mock_extract):
    mock_exists.return_value = True
    mock_extract.return_value = "Extracted content"

    parser = DocumentParser()
    message = Message(
        id="1",
        message="test",
        timestamp="2023-01-01",
        local_path="test.pdf",
        media_type="document",
        filename="test.pdf",
    )

    content = parser.get_document_content(message)
    assert content == "Extracted content"
    mock_extract.assert_called_once_with("test.pdf")


@patch("os.path.exists")
def test_get_document_content_not_pdf(mock_exists):
    mock_exists.return_value = True
    parser = DocumentParser()
    message = Message(
        id="1",
        message="test",
        timestamp="2023-01-01",
        local_path="test.jpg",
        media_type="image",
        filename="test.jpg",
    )

    assert parser.get_document_content(message) is None

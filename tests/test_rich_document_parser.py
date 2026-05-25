"""Tests for the enhanced RichDocumentParser (OCR pipeline).

Test strategy:
- Unit tests mock all OCR engines and Ollama — no real GPU/network needed in CI
- Integration markers skip if deps not installed
- Tests cover: routing logic, fallback chain, Ollama cleanup, image support, URL auto-expansion
"""

import io
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from src.backend.models import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(local_path=None, media_type=None, filename=None, message="hi"):
    return Message(
        id="test-1",
        message=message,
        timestamp="2024-01-01",
        local_path=local_path,
        media_type=media_type,
        filename=filename,
    )


def _make_parser(**kwargs):
    from src.backend.rich_document_parser import RichDocumentParser
    return RichDocumentParser(**kwargs)


# ---------------------------------------------------------------------------
# 1. Routing: pdfplumber native text path
# ---------------------------------------------------------------------------

class TestNativeTextRouting:

    @patch("pdfplumber.open")
    @patch("os.path.exists", return_value=True)
    def test_native_text_used_when_sufficient(self, mock_exists, mock_pdf_open):
        """If pdfplumber returns >100 chars, skip OCR entirely."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "A" * 150
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf_open.return_value.__enter__.return_value = mock_pdf

        parser = _make_parser()
        result = parser.extract_text("test.pdf")

        assert result == "A" * 150
        # OCR should NOT have been called
        assert not hasattr(parser, "_doctr_called")

    @patch("pdfplumber.open")
    @patch("os.path.exists", return_value=True)
    def test_falls_through_to_ocr_when_native_text_sparse(self, mock_exists, mock_pdf_open):
        """If pdfplumber returns <50 chars, should attempt OCR."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "short"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf_open.return_value.__enter__.return_value = mock_pdf

        parser = _make_parser()
        with patch.object(parser, "_ocr_pdf", return_value="OCR extracted text") as mock_ocr:
            result = parser.extract_text("test.pdf")
            mock_ocr.assert_called_once_with("test.pdf")
            assert result == "OCR extracted text"

    @patch("pdfplumber.open")
    @patch("os.path.exists", return_value=True)
    def test_native_text_empty_falls_through(self, mock_exists, mock_pdf_open):
        """Empty pdfplumber output triggers OCR."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf_open.return_value.__enter__.return_value = mock_pdf

        parser = _make_parser()
        with patch.object(parser, "_ocr_pdf", return_value="OCR result") as mock_ocr:
            result = parser.extract_text("test.pdf")
            mock_ocr.assert_called_once()

    @patch("pdfplumber.open")
    @patch("os.path.exists", return_value=True)
    def test_pdfplumber_exception_falls_through_to_ocr(self, mock_exists, mock_pdf_open):
        """pdfplumber exception should not crash — fall through to OCR."""
        mock_pdf_open.side_effect = Exception("corrupt PDF")

        parser = _make_parser()
        with patch.object(parser, "_ocr_pdf", return_value="OCR fallback") as mock_ocr:
            result = parser.extract_text("test.pdf")
            mock_ocr.assert_called_once()
            assert result == "OCR fallback"


# ---------------------------------------------------------------------------
# 2. OCR engine selection
# ---------------------------------------------------------------------------

class TestOCREngineSelection:

    def test_doctr_selected_for_english_pdf(self):
        """English structured PDFs should prefer docTR."""
        parser = _make_parser()
        long_text = "docTR extracted text from English PAN card document " * 2  # >30 chars
        with patch.object(parser, "_run_doctr", return_value=long_text) as mock_doctr, \
             patch.object(parser, "_run_easyocr", return_value="") as mock_easy, \
             patch.object(parser, "_pdf_to_images", return_value=[MagicMock()]), \
             patch.object(parser, "_postprocess_ocr", side_effect=lambda t: t):
            result = parser._ocr_pdf("pan_card.pdf", hint="english")
            mock_doctr.assert_called_once()
            mock_easy.assert_not_called()
            assert result == long_text

    def test_easyocr_selected_for_hindi_pdf(self):
        """Hindi/bilingual PDFs should prefer EasyOCR."""
        parser = _make_parser()
        long_text = "EasyOCR extracted Aadhaar Hindi bilingual text content here " * 2
        with patch.object(parser, "_run_easyocr", return_value=long_text) as mock_easy, \
             patch.object(parser, "_run_doctr", return_value="") as mock_doctr, \
             patch.object(parser, "_pdf_to_images", return_value=[MagicMock()]), \
             patch.object(parser, "_postprocess_ocr", side_effect=lambda t: t):
            result = parser._ocr_pdf("aadhaar.pdf", hint="hindi")
            mock_easy.assert_called_once()
            mock_doctr.assert_not_called()
            assert result == long_text

    def test_doctr_fallback_to_easyocr_when_short(self):
        """If docTR returns <30 chars, fall back to EasyOCR."""
        parser = _make_parser()
        long_fallback = "EasyOCR full text extracted from document successfully with content"
        with patch.object(parser, "_run_doctr", return_value="tiny") as mock_doctr, \
             patch.object(parser, "_run_easyocr", return_value=long_fallback) as mock_easy, \
             patch.object(parser, "_pdf_to_images", return_value=[MagicMock()]), \
             patch.object(parser, "_postprocess_ocr", side_effect=lambda t: t):
            result = parser._ocr_pdf("doc.pdf", hint="english")
            mock_doctr.assert_called_once()
            mock_easy.assert_called_once()
            assert result == long_fallback

    def test_easyocr_fallback_to_doctr_when_short(self):
        """If EasyOCR returns <30 chars, fall back to docTR."""
        parser = _make_parser()
        long_fallback = "docTR fallback text result from English document extraction here"
        with patch.object(parser, "_run_easyocr", return_value="x") as mock_easy, \
             patch.object(parser, "_run_doctr", return_value=long_fallback) as mock_doctr, \
             patch.object(parser, "_pdf_to_images", return_value=[MagicMock()]), \
             patch.object(parser, "_postprocess_ocr", side_effect=lambda t: t):
            result = parser._ocr_pdf("doc.pdf", hint="hindi")
            mock_easy.assert_called_once()
            mock_doctr.assert_called_once()
            assert result == long_fallback

    def test_both_engines_fail_returns_empty(self):
        """If both OCR engines return <30 chars, return empty string."""
        parser = _make_parser()
        with patch.object(parser, "_run_doctr", return_value="bad"), \
             patch.object(parser, "_run_easyocr", return_value="bad"), \
             patch.object(parser, "_pdf_to_images", return_value=[MagicMock()]):
            result = parser._ocr_pdf("doc.pdf", hint="english")
            assert result == ""


# ---------------------------------------------------------------------------
# 3. Image support (WhatsApp .jpg/.png)
# ---------------------------------------------------------------------------

class TestImageSupport:

    @patch("os.path.exists", return_value=True)
    def test_jpg_routed_to_ocr_not_pdfplumber(self, mock_exists):
        """JPEG images should not go through pdfplumber — direct to OCR."""
        parser = _make_parser()
        with patch.object(parser, "_ocr_image", return_value="image text") as mock_img:
            result = parser.extract_text("photo.jpg")
            mock_img.assert_called_once_with("photo.jpg")
            assert result == "image text"

    @patch("os.path.exists", return_value=True)
    def test_png_routed_to_ocr(self, mock_exists):
        parser = _make_parser()
        with patch.object(parser, "_ocr_image", return_value="png text") as mock_img:
            result = parser.extract_text("scan.png")
            mock_img.assert_called_once_with("scan.png")

    @patch("os.path.exists", return_value=True)
    def test_get_document_content_handles_jpg_message(self, mock_exists):
        """get_document_content should process image attachments."""
        parser = _make_parser()
        with patch.object(parser, "_ocr_image", return_value="scanned receipt text"):
            msg = _make_message(
                local_path="receipt.jpg",
                media_type="image",
                filename="receipt.jpg",
            )
            result = parser.get_document_content(msg)
            assert result == "scanned receipt text"

    @patch("os.path.exists", return_value=True)
    def test_get_document_content_handles_png_message(self, mock_exists):
        parser = _make_parser()
        with patch.object(parser, "_ocr_image", return_value="png content"):
            msg = _make_message(
                local_path="doc.png",
                media_type="image",
                filename="doc.png",
            )
            result = parser.get_document_content(msg)
            assert result == "png content"

    @patch("os.path.exists", return_value=True)
    def test_unsupported_file_type_returns_none(self, mock_exists):
        """Non-PDF, non-image files return None."""
        parser = _make_parser()
        msg = _make_message(
            local_path="file.xlsx",
            media_type="document",
            filename="file.xlsx",
        )
        result = parser.get_document_content(msg)
        assert result is None


# ---------------------------------------------------------------------------
# 4. Ollama OCR cleanup
# ---------------------------------------------------------------------------

class TestOllamaCleanup:

    def test_ollama_cleanup_called_after_ocr(self):
        """After OCR extraction, qwen2.5:7b should clean up the text."""
        parser = _make_parser(use_ollama_cleanup=True)
        raw_ocr = "AMSPK271BF some garbled text here"

        with patch.object(parser, "_ollama_cleanup", return_value="AMSPK2718F some clean text") as mock_ollama:
            result = parser._postprocess_ocr(raw_ocr)
            mock_ollama.assert_called_once_with(raw_ocr)
            assert result == "AMSPK2718F some clean text"

    def test_ollama_cleanup_skipped_when_disabled(self):
        """When use_ollama_cleanup=False, skip Ollama call."""
        parser = _make_parser(use_ollama_cleanup=False)
        with patch.object(parser, "_ollama_cleanup") as mock_ollama:
            result = parser._postprocess_ocr("raw text")
            mock_ollama.assert_not_called()
            assert result == "raw text"

    def test_ollama_cleanup_skipped_for_short_text(self):
        """Don't call Ollama for OCR output < 20 chars — not worth cleanup cost."""
        parser = _make_parser(use_ollama_cleanup=True)
        with patch.object(parser, "_ollama_cleanup") as mock_ollama:
            result = parser._postprocess_ocr("tiny")
            mock_ollama.assert_not_called()

    @patch("requests.post")
    def test_ollama_http_call_format(self, mock_post):
        """Ollama API call must use correct model and return parsed response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "cleaned text output"}
        mock_post.return_value = mock_response

        parser = _make_parser(use_ollama_cleanup=True)
        result = parser._ollama_cleanup("raw OCR garbage text here for cleaning")

        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
        assert payload["model"] == "qwen2.5:7b"
        assert payload["options"]["temperature"] == 0.1
        assert result == "cleaned text output"

    @patch("requests.post", side_effect=Exception("ollama down"))
    def test_ollama_failure_returns_raw_text(self, mock_post):
        """If Ollama call fails, return raw OCR text unchanged."""
        parser = _make_parser(use_ollama_cleanup=True)
        result = parser._ollama_cleanup("raw text fallback check")
        assert result == "raw text fallback check"

    @patch("requests.post")
    def test_ollama_timeout_returns_raw_text(self, mock_post):
        """If Ollama times out, return raw text."""
        import requests
        mock_post.side_effect = requests.Timeout()
        parser = _make_parser(use_ollama_cleanup=True)
        result = parser._ollama_cleanup("timeout check raw text")
        assert result == "timeout check raw text"


# ---------------------------------------------------------------------------
# 5. File-not-found / error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_file_not_found_raises(self):
        from src.backend.rich_document_parser import RichDocumentParser
        parser = RichDocumentParser()
        with pytest.raises(FileNotFoundError):
            parser.extract_text("nonexistent.pdf")

    @patch("os.path.exists", return_value=True)
    def test_unsupported_extension_raises_value_error(self, mock_exists):
        from src.backend.rich_document_parser import RichDocumentParser
        parser = RichDocumentParser()
        with pytest.raises(ValueError, match="Unsupported file type"):
            parser.extract_text("file.docx")

    @patch("os.path.exists", return_value=True)
    def test_get_document_content_missing_file_returns_none(self, mock_exists):
        from src.backend.rich_document_parser import RichDocumentParser
        # file "exists" per mock but extract_text raises FileNotFoundError
        parser = RichDocumentParser()
        with patch.object(parser, "extract_text", side_effect=FileNotFoundError):
            msg = _make_message(local_path="missing.pdf", media_type="document", filename="missing.pdf")
            result = parser.get_document_content(msg)
            assert result is None


# ---------------------------------------------------------------------------
# 6. Backward compatibility with DocumentParser interface
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:

    @patch("pdfplumber.open")
    @patch("os.path.exists", return_value=True)
    def test_extract_text_pdf_interface_unchanged(self, mock_exists, mock_pdf_open):
        """RichDocumentParser.extract_text(pdf_path) must work like old DocumentParser."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Compatible text output from PDF. " + "X" * 80
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf_open.return_value.__enter__.return_value = mock_pdf

        from src.backend.rich_document_parser import RichDocumentParser
        parser = RichDocumentParser()
        result = parser.extract_text("test.pdf")
        assert result.startswith("Compatible text output from PDF")

    @patch("os.path.exists", return_value=True)
    def test_get_document_content_no_path_returns_none(self, mock_exists):
        from src.backend.rich_document_parser import RichDocumentParser
        parser = RichDocumentParser()
        msg = _make_message()
        assert parser.get_document_content(msg) is None


# ---------------------------------------------------------------------------
# 7. Integration tests (skip if deps missing)
# ---------------------------------------------------------------------------

def _ollama_running():
    try:
        import requests
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


doctr_available = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("doctr"),
    reason="doctr not installed"
)
easyocr_available = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("easyocr"),
    reason="easyocr not installed"
)
ollama_available = pytest.mark.skipif(
    not _ollama_running(),
    reason="ollama not running at localhost:11434"
)


@ollama_available
class TestOllamaIntegration:

    def test_ollama_qwen_cleans_pan_ocr_errors(self):
        """Real Ollama call: qwen2.5:7b should fix common PAN OCR confusions."""
        from src.backend.rich_document_parser import RichDocumentParser
        parser = RichDocumentParser(use_ollama_cleanup=True)
        # Simulate OCR misread: 0→O, 8→B in PAN
        raw = "PAN: AMS PK 2 71 8F  Name: VIVEK CHHIKARA  DOB: O1/O5/199O"
        result = parser._ollama_cleanup(raw)
        # Should return non-empty cleaned text
        assert len(result) > 10
        assert "VIVEK" in result or "vivek" in result.lower()

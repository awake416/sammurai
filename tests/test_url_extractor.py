"""Tests for URLExtractor — smart URL filtering + content extraction."""

import pytest
from unittest.mock import MagicMock, patch


def _make_extractor(**kwargs):
    from src.backend.url_extractor import URLExtractor
    return URLExtractor(**kwargs)


# ---------------------------------------------------------------------------
# 1. URL detection in message text
# ---------------------------------------------------------------------------

class TestURLDetection:

    def test_finds_https_url(self):
        ext = _make_extractor()
        urls = ext.extract_urls("Check this out https://timesofindia.com/article/123")
        assert "https://timesofindia.com/article/123" in urls

    def test_finds_multiple_urls(self):
        ext = _make_extractor()
        urls = ext.extract_urls("See https://example.com and https://another.com/path")
        assert len(urls) == 2

    def test_ignores_http_urls(self):
        ext = _make_extractor()
        urls = ext.extract_urls("insecure http://example.com/page")
        assert urls == []

    def test_empty_message_returns_empty(self):
        ext = _make_extractor()
        assert ext.extract_urls("") == []

    def test_no_urls_returns_empty(self):
        ext = _make_extractor()
        assert ext.extract_urls("football class is on Saturday at 10 AM") == []


# ---------------------------------------------------------------------------
# 2. Domain blocklist
# ---------------------------------------------------------------------------

class TestDomainBlocklist:

    @pytest.mark.parametrize("url", [
        "https://wa.me/919999999999",
        "https://chat.whatsapp.com/abc123",
        "https://instagram.com/p/xyz",
        "https://www.instagram.com/reel/abc",
        "https://facebook.com/share/123",
        "https://twitter.com/user/status/1",
        "https://x.com/user/status/1",
        "https://youtube.com/watch?v=abc",
        "https://youtu.be/abc123",
        "https://maps.google.com/maps?q=...",
        "https://t.me/somechannel",
        "https://bit.ly/3xyz",
        "https://tinyurl.com/abc",
    ])
    def test_blocked_domains_filtered(self, url):
        ext = _make_extractor()
        assert ext.is_blocked(url) is True

    @pytest.mark.parametrize("url", [
        "https://timesofindia.com/article/property",
        "https://housing.com/news/registration-charges",
        "https://github.com/org/repo",
        "https://docs.google.com/document/d/abc/edit",
        "https://medium.com/@author/title",
        "https://linkedin.com/pulse/article",
    ])
    def test_useful_domains_not_blocked(self, url):
        ext = _make_extractor()
        assert ext.is_blocked(url) is False


# ---------------------------------------------------------------------------
# 3. Content quality gate
# ---------------------------------------------------------------------------

class TestContentQualityGate:

    @patch("httpx.get")
    def test_short_content_rejected(self, mock_get):
        """Paywall / login page — less than 200 chars extracted."""
        from bs4 import BeautifulSoup
        mock_resp = MagicMock()
        mock_resp.text = "<html><body><p>Please login to continue.</p></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        ext = _make_extractor()
        with patch.object(ext, '_is_safe_url', return_value="1.2.3.4"):
            result = ext.fetch_content("https://example.com/article")
        assert result is None

    @patch("httpx.get")
    def test_substantial_content_returned(self, mock_get):
        """Real article — enough content to be useful."""
        long_content = "Property registration charges in India vary by state. " * 20
        mock_resp = MagicMock()
        mock_resp.text = f"<html><body><p>{long_content}</p></body></html>"
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        ext = _make_extractor()
        with patch.object(ext, '_is_safe_url', return_value="1.2.3.4"):
            result = ext.fetch_content("https://housing.com/news/registration")
        assert result is not None
        assert len(result) >= 200

    @patch("httpx.get", side_effect=Exception("connection error"))
    def test_fetch_error_returns_none(self, mock_get):
        ext = _make_extractor()
        with patch.object(ext, '_is_safe_url', return_value="1.2.3.4"):
            result = ext.fetch_content("https://example.com/article")
        assert result is None

    def test_blocked_domain_skips_fetch(self):
        ext = _make_extractor()
        with patch("httpx.get") as mock_get:
            result = ext.fetch_content("https://youtube.com/watch?v=abc")
            mock_get.assert_not_called()
        assert result is None


# ---------------------------------------------------------------------------
# 4. Relevance check via Ollama
# ---------------------------------------------------------------------------

class TestRelevanceCheck:

    def test_relevant_content_approved(self):
        ollama = MagicMock()
        ollama.is_available.return_value = True
        ollama.generate_json.return_value = {"relevant": True}
        ext = _make_extractor(ollama_client=ollama)
        content = "Property registration charges in Bangalore 2026 stamp duty details"
        assert ext.is_relevant(content) is True

    def test_irrelevant_content_rejected(self):
        ollama = MagicMock()
        ollama.is_available.return_value = True
        ollama.generate_json.return_value = {"relevant": False}
        ext = _make_extractor(ollama_client=ollama)
        assert ext.is_relevant("Buy now! Limited offer 50% off!") is False

    def test_no_ollama_defaults_to_true(self):
        """Without Ollama, assume content is relevant (don't discard)."""
        ext = _make_extractor(ollama_client=None)
        assert ext.is_relevant("some content here") is True

    def test_ollama_down_defaults_to_true(self):
        ollama = MagicMock()
        ollama.is_available.return_value = False
        ext = _make_extractor(ollama_client=ollama)
        assert ext.is_relevant("some content") is True

    def test_ollama_fails_defaults_to_true(self):
        ollama = MagicMock()
        ollama.is_available.return_value = True
        ollama.generate_json.return_value = None
        ext = _make_extractor(ollama_client=ollama)
        assert ext.is_relevant("some content") is True

    def test_content_truncated_before_ollama_call(self):
        """Ollama should only receive first 500 chars."""
        ollama = MagicMock()
        ollama.is_available.return_value = True
        ollama.generate_json.return_value = {"relevant": True}
        ext = _make_extractor(ollama_client=ollama)
        long_content = "x" * 2000
        ext.is_relevant(long_content)
        call_args = ollama.generate_json.call_args
        user_msg = call_args[0][1] if call_args[0] else call_args[1].get("user_message", "")
        assert len(user_msg) <= 600  # 500 content + prompt overhead


# ---------------------------------------------------------------------------
# 5. Full pipeline: extract_from_message
# ---------------------------------------------------------------------------

class TestFullPipeline:

    def test_no_urls_returns_empty(self):
        ext = _make_extractor()
        results = ext.extract_from_message("football class Saturday 10 AM")
        assert results == []

    def test_blocked_url_skipped(self):
        ext = _make_extractor()
        results = ext.extract_from_message("see https://wa.me/919999999999")
        assert results == []

    def test_full_pipeline_produces_summary(self):
        ollama = MagicMock()
        ollama.is_available.return_value = True
        ollama.generate_json.return_value = {"relevant": True}

        long_content = "Property stamp duty in Bangalore 2026. " * 15

        ext = _make_extractor(ollama_client=ollama)
        with patch.object(ext, 'fetch_content', return_value=long_content), \
             patch.object(ext, '_summarize', return_value="Stamp duty is 5% in Bangalore."):
            results = ext.extract_from_message(
                "check registration charges https://housing.com/news/stamp-duty"
            )
        assert len(results) == 1
        assert results[0]["url"] == "https://housing.com/news/stamp-duty"
        assert "Stamp duty" in results[0]["summary"]

    def test_low_quality_content_skipped(self):
        ext = _make_extractor()
        with patch.object(ext, 'fetch_content', return_value=None):
            results = ext.extract_from_message("https://example.com/paywall-article")
        assert results == []

    def test_irrelevant_content_skipped(self):
        ollama = MagicMock()
        ollama.is_available.return_value = True
        ollama.generate_json.return_value = {"relevant": False}
        long_content = "Buy now! Flash sale 50% off all products today only! " * 10

        ext = _make_extractor(ollama_client=ollama)
        with patch.object(ext, 'fetch_content', return_value=long_content):
            results = ext.extract_from_message("https://spam-shop.com/sale")
        assert results == []

    def test_multiple_urls_processed(self):
        ext = _make_extractor()
        long_content = "Meaningful article content about property registration. " * 10
        with patch.object(ext, 'fetch_content', return_value=long_content), \
             patch.object(ext, 'is_relevant', return_value=True), \
             patch.object(ext, '_summarize', return_value="Summary."):
            results = ext.extract_from_message(
                "see https://site1.com/a and https://site2.com/b"
            )
        assert len(results) == 2


# ---------------------------------------------------------------------------
# 6. Integration with enrich_messages_with_docs in cli.py
# ---------------------------------------------------------------------------

class TestCliIntegration:

    def test_enrich_adds_url_summaries_to_message(self):
        """enrich_messages_with_docs should append URL summaries to message text."""
        from src.backend.cli import enrich_messages_with_docs
        from src.backend.rich_document_parser import RichDocumentParser

        parser = RichDocumentParser()
        url_ext = MagicMock()
        msg_text = "check https://housing.com/news/registration"
        url_ext.extract_from_messages.return_value = {
            msg_text: [{"url": "https://housing.com/news/registration", "summary": "Stamp duty is 5% in Bangalore for 2026."}]
        }

        msgs = [{"message": msg_text, "media_type": None, "local_path": None}]
        enriched = enrich_messages_with_docs(msgs, parser, url_extractor=url_ext)

        assert "Stamp duty" in enriched[0]["message"]

    def test_enrich_skips_url_extraction_when_no_extractor(self):
        """Without url_extractor, enrich_messages_with_docs still works as before."""
        from src.backend.cli import enrich_messages_with_docs
        from src.backend.rich_document_parser import RichDocumentParser

        parser = RichDocumentParser()
        msgs = [{"message": "check https://housing.com/news", "media_type": None, "local_path": None}]
        enriched = enrich_messages_with_docs(msgs, parser)
        # No URL extraction — message unchanged
        assert enriched[0]["message"] == "check https://housing.com/news"

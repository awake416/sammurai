import pytest
from unittest.mock import MagicMock, patch
from src.backend.topic_extractor import TopicExtractor
from src.backend.llm_client import LLMClient
from src.backend.models import DocumentSummary
from src.backend.utils import redact_pii
from pydantic import ValidationError


def test_topic_extractor_ssrf_https_only():
    client = MagicMock(spec=LLMClient)
    extractor = TopicExtractor(client)

    # Should reject http
    with pytest.raises(ValueError, match="Insecure or invalid URL"):
        extractor.summarize_document("http://example.com/doc")


def test_topic_extractor_ssrf_dns_rebinding_fix():
    client = MagicMock(spec=LLMClient)
    extractor = TopicExtractor(client)

    with (
        patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve,
        patch("src.backend.topic_extractor.httpx.get") as mock_get,
    ):
        mock_resolve.return_value = [
            (None, None, None, None, ("93.184.216.34", 0))
        ]  # example.com
        mock_response = MagicMock()
        mock_response.text = "<html><body>Content</body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client.summarize_document.return_value = {
            "title": "T",
            "summary": "S",
            "key_dates": [],
        }

        extractor.summarize_document("https://example.com/doc")

        # Verify httpx.get was called with the IP address, not the hostname
        args, kwargs = mock_get.call_args
        assert "93.184.216.34" in args[0]
        assert kwargs["headers"]["Host"] == "example.com"


def test_llm_client_https_bypass():
    # Should allow localhost with http
    LLMClient(base_url="http://localhost:8080", api_key="test")
    LLMClient(base_url="http://127.0.0.1:8080", api_key="test")

    # Should reject evil.com/localhost
    with pytest.raises(ValueError, match="Insecure LITELLM_BASE_URL"):
        LLMClient(base_url="http://evil.com/localhost", api_key="test")

    # Should reject other http
    with pytest.raises(ValueError, match="Insecure LITELLM_BASE_URL"):
        LLMClient(base_url="http://api.openai.com", api_key="test")


def test_models_url_strict_https():
    # Should reject http even for localhost
    with pytest.raises(ValueError, match="Only https:// is allowed"):
        DocumentSummary(
            resource_url="http://localhost/doc", title="Title", summary="Summary"
        )

    # Should allow https
    ds = DocumentSummary(
        resource_url="https://example.com/doc", title="Title", summary="Summary"
    )
    assert str(ds.resource_url) == "https://example.com/doc"


def test_topic_extractor_is_safe_url():
    client = MagicMock(spec=LLMClient)
    extractor = TopicExtractor(client)

    # Safe public IP
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        assert extractor._is_safe_url("https://example.com") == "93.184.216.34"

    # Private IP
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [(None, None, None, None, ("192.168.1.1", 0))]
        assert extractor._is_safe_url("https://internal.com") is None

    # Loopback IP
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [(None, None, None, None, ("127.0.0.1", 0))]
        assert extractor._is_safe_url("https://localhost") is None

    # Link-local IP
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [(None, None, None, None, ("169.254.169.254", 0))]
        assert extractor._is_safe_url("https://metadata.google.internal") is None

    # IPv6 Loopback
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [(None, None, None, None, ("::1", 0, 0, 0))]
        assert extractor._is_safe_url("https://[::1]") is None

    # IPv6 Private
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [(None, None, None, None, ("fc00::1", 0, 0, 0))]
        assert extractor._is_safe_url("https://[fc00::1]") is None

    # Mixed safe and unsafe (should block if ANY is unsafe)
    with patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve:
        mock_resolve.return_value = [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
        ]
        assert extractor._is_safe_url("https://example.com") is None


def test_topic_extractor_port_preservation():
    client = MagicMock(spec=LLMClient)
    extractor = TopicExtractor(client)

    with (
        patch("src.backend.topic_extractor.socket.getaddrinfo") as mock_resolve,
        patch("src.backend.topic_extractor.httpx.get") as mock_get,
    ):
        mock_resolve.return_value = [(None, None, None, None, ("93.184.216.34", 0))]
        mock_response = MagicMock()
        mock_response.text = "<html><body>Content</body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        client.summarize_document.return_value = {
            "title": "T",
            "summary": "S",
            "key_dates": [],
        }

        # Test with custom port
        extractor.summarize_document("https://example.com:8443/doc")

        # Verify httpx.get was called with the IP address AND the port
        args, kwargs = mock_get.call_args
        assert "93.184.216.34:8443" in args[0]
        assert kwargs["headers"]["Host"] == "example.com"
        assert kwargs["follow_redirects"] is False
        assert "sni_hostname" in kwargs["extensions"]


def test_llm_client_ipv6_loopback():
    # Should allow IPv6 loopback
    LLMClient(base_url="http://[::1]:8080", api_key="test")


def test_jid_redaction():
    text = "Contact me at 1234567890@s.whatsapp.net or group 987654321@g.us"
    redacted = redact_pii(text)
    assert "[REDACTED]" in redacted
    assert "@s.whatsapp.net" not in redacted
    assert "@g.us" not in redacted

    # Ensure it doesn't break normal phone redaction
    text2 = "Call +1 555 123 4567"
    assert "[REDACTED]" in redact_pii(text2)

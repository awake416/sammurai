"""Tests for OllamaClient — local LLM wrapper for qwen2.5:7b."""

import pytest
import requests
from unittest.mock import MagicMock, patch


def _make_client(**kwargs):
    from src.backend.ollama_client import OllamaClient
    return OllamaClient(**kwargs)


def _mock_ollama(response_text: str):
    mock = MagicMock()
    mock.json.return_value = {"response": response_text}
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# 1. Basic generate_json
# ---------------------------------------------------------------------------

class TestGenerateJson:

    @patch("requests.post")
    def test_returns_parsed_json(self, mock_post):
        mock_post.return_value = _mock_ollama('{"intent": "query"}')
        client = _make_client()
        result = client.generate_json("classify this", "when is football class?")
        assert result == {"intent": "query"}

    @patch("requests.post")
    def test_extracts_json_from_markdown_block(self, mock_post):
        mock_post.return_value = _mock_ollama('Sure! ```json\n{"intent": "capture"}\n```')
        client = _make_client()
        result = client.generate_json("system", "message")
        assert result == {"intent": "capture"}

    @patch("requests.post")
    def test_returns_none_on_no_json(self, mock_post):
        mock_post.return_value = _mock_ollama("I cannot classify this.")
        client = _make_client()
        result = client.generate_json("system", "message")
        assert result is None

    @patch("requests.post", side_effect=Exception("connection refused"))
    def test_returns_none_on_connection_error(self, mock_post):
        client = _make_client()
        result = client.generate_json("system", "message")
        assert result is None

    @patch("requests.post", side_effect=requests.Timeout())
    def test_returns_none_on_timeout(self, mock_post):
        client = _make_client()
        result = client.generate_json("system", "message")
        assert result is None

    @patch("requests.post")
    def test_uses_correct_model_and_options(self, mock_post):
        mock_post.return_value = _mock_ollama('{"ok": true}')
        client = _make_client(model="qwen2.5:7b")
        client.generate_json("sys", "msg")
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "qwen2.5:7b"
        assert payload["options"]["temperature"] == 0.1
        assert payload["stream"] is False

    @patch("requests.post")
    def test_prompt_contains_system_and_user(self, mock_post):
        mock_post.return_value = _mock_ollama('{"x": 1}')
        client = _make_client()
        client.generate_json("SYSTEM INSTRUCTIONS", "USER MESSAGE")
        payload = mock_post.call_args[1]["json"]
        assert "SYSTEM INSTRUCTIONS" in payload["prompt"]
        assert "USER MESSAGE" in payload["prompt"]


# ---------------------------------------------------------------------------
# 2. is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:

    @patch("requests.get")
    def test_returns_true_when_ollama_running(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        client = _make_client()
        assert client.is_available() is True

    @patch("requests.get", side_effect=Exception("not running"))
    def test_returns_false_when_ollama_down(self, mock_get):
        client = _make_client()
        assert client.is_available() is False

    @patch("requests.get")
    def test_returns_false_on_non_200(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500)
        client = _make_client()
        assert client.is_available() is False


# ---------------------------------------------------------------------------
# 3. Integration (skip if ollama not running)
# ---------------------------------------------------------------------------

def _ollama_running():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


ollama_available = pytest.mark.skipif(
    not _ollama_running(), reason="ollama not running"
)


@ollama_available
class TestOllamaIntegration:

    def test_real_json_generation(self):
        from src.backend.ollama_client import OllamaClient
        client = OllamaClient()
        result = client.generate_json(
            system_prompt='Classify as query or capture. Respond with JSON: {"intent": "query" or "capture"}',
            user_message="when is football class?",
        )
        assert result is not None
        assert result.get("intent") in ("query", "capture")

    def test_is_available_real(self):
        from src.backend.ollama_client import OllamaClient
        client = OllamaClient()
        assert client.is_available() is True

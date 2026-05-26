"""Tests for three-tier intent routing: heuristics → Ollama → claude-sonnet fallback."""

import pytest
from unittest.mock import MagicMock, patch
from src.backend.intent_router import Intent, IntentRouter


def _router(ollama=None, llm=None):
    return IntentRouter(ollama_client=ollama, llm_client=llm)


def _mock_ollama(intent: str):
    m = MagicMock()
    m.is_available.return_value = True
    m.generate_json.return_value = {"intent": intent}
    return m


def _mock_ollama_fail():
    m = MagicMock()
    m.is_available.return_value = True
    m.generate_json.return_value = None
    return m


def _mock_ollama_down():
    m = MagicMock()
    m.is_available.return_value = False
    return m


def _mock_llm(intent: str):
    m = MagicMock()
    m.generate_json.return_value = {"intent": intent}
    return m


# ---------------------------------------------------------------------------
# 1. Heuristics (unchanged — must still pass)
# ---------------------------------------------------------------------------

class TestHeuristics:

    def test_question_mark_is_query(self):
        assert _router().classify("When is football class?") == Intent.QUERY

    def test_wh_word_is_query(self):
        assert _router().classify("what are the pending tasks") == Intent.QUERY

    def test_greeting_is_ignore(self):
        assert _router().classify("hi") == Intent.IGNORE

    def test_thanks_is_ignore(self):
        assert _router().classify("thanks") == Intent.IGNORE

    def test_url_is_capture(self):
        assert _router().classify("https://example.com/article") == Intent.CAPTURE

    def test_empty_is_ignore(self):
        assert _router().classify("") == Intent.IGNORE

    def test_forwarded_is_capture(self):
        assert _router().classify("forwarded: check this out") == Intent.CAPTURE


# ---------------------------------------------------------------------------
# 2. Tier 2: Ollama handles ambiguous messages
# ---------------------------------------------------------------------------

class TestOllamaTier:

    def test_ollama_query_returned(self):
        ollama = _mock_ollama("query")
        r = _router(ollama=ollama)
        result = r.classify("school fees ka kya hua")  # ambiguous Hinglish
        ollama.generate_json.assert_called_once()
        assert result == Intent.QUERY

    def test_ollama_capture_returned(self):
        ollama = _mock_ollama("capture")
        r = _router(ollama=ollama)
        result = r.classify("doctor appointment next Tuesday")
        assert result == Intent.CAPTURE

    def test_ollama_not_called_when_heuristic_matches(self):
        ollama = _mock_ollama("query")
        r = _router(ollama=ollama)
        r.classify("what is the school fee?")  # heuristic handles this
        ollama.generate_json.assert_not_called()

    def test_ollama_down_falls_through_to_llm(self):
        ollama = _mock_ollama_down()
        llm = _mock_llm("query")
        r = _router(ollama=ollama, llm=llm)
        result = r.classify("bhai meeting kab hai")
        ollama.generate_json.assert_not_called()
        llm.generate_json.assert_called_once()
        assert result == Intent.QUERY

    def test_ollama_fails_falls_through_to_llm(self):
        ollama = _mock_ollama_fail()
        llm = _mock_llm("capture")
        r = _router(ollama=ollama, llm=llm)
        result = r.classify("reminder school fees friday")
        llm.generate_json.assert_called_once()
        assert result == Intent.CAPTURE

    def test_ollama_fails_no_llm_defaults_to_capture(self):
        ollama = _mock_ollama_fail()
        r = _router(ollama=ollama, llm=None)
        result = r.classify("some ambiguous message here")
        assert result == Intent.CAPTURE

    def test_ollama_down_no_llm_defaults_to_capture(self):
        ollama = _mock_ollama_down()
        r = _router(ollama=ollama, llm=None)
        result = r.classify("some message")
        assert result == Intent.CAPTURE


# ---------------------------------------------------------------------------
# 3. Tier 3: claude-sonnet fallback (existing behavior preserved)
# ---------------------------------------------------------------------------

class TestLLMFallbackTier:

    def test_llm_used_when_no_ollama(self):
        llm = _mock_llm("query")
        r = _router(ollama=None, llm=llm)
        result = r.classify("bhai meeting kab hai")
        llm.generate_json.assert_called_once()
        assert result == Intent.QUERY

    def test_llm_not_called_when_ollama_succeeds(self):
        ollama = _mock_ollama("capture")
        llm = _mock_llm("query")
        r = _router(ollama=ollama, llm=llm)
        r.classify("send me that document")
        llm.generate_json.assert_not_called()

    def test_no_ollama_no_llm_defaults_to_capture(self):
        r = _router()
        result = r.classify("something ambiguous")
        assert result == Intent.CAPTURE


# ---------------------------------------------------------------------------
# 4. Hinglish / Hindi patterns
# ---------------------------------------------------------------------------

class TestHinglish:

    def test_hinglish_question_detected_by_heuristic(self):
        # ends with ? → heuristic catches it
        assert _router().classify("bhai football class kab hai?") == Intent.QUERY

    def test_hinglish_no_question_mark_goes_to_ollama(self):
        ollama = _mock_ollama("query")
        r = _router(ollama=ollama)
        r.classify("school fees kab pay karni hai bhai")
        ollama.generate_json.assert_called_once()

    def test_devanagari_question_goes_to_ollama(self):
        ollama = _mock_ollama("query")
        r = _router(ollama=ollama)
        r.classify("फुटबॉल क्लास कब है")
        ollama.generate_json.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Integration (skip if ollama not running)
# ---------------------------------------------------------------------------

import requests as _requests


def _ollama_running():
    try:
        r = _requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


ollama_available = pytest.mark.skipif(
    not _ollama_running(), reason="ollama not running"
)


@ollama_available
class TestIntentRouterIntegration:

    def test_real_query_classified(self):
        from src.backend.ollama_client import OllamaClient
        ollama = OllamaClient()
        r = IntentRouter(ollama_client=ollama)
        assert r.classify("when is football class?") == Intent.QUERY

    def test_real_capture_classified(self):
        from src.backend.ollama_client import OllamaClient
        ollama = OllamaClient()
        r = IntentRouter(ollama_client=ollama)
        # Ambiguous — no heuristic match, goes to Ollama
        result = r.classify("doctor appointment hai Tuesday ko")
        assert result in (Intent.CAPTURE, Intent.QUERY)  # either valid

    def test_real_ignore_classified(self):
        from src.backend.ollama_client import OllamaClient
        ollama = OllamaClient()
        r = IntentRouter(ollama_client=ollama)
        assert r.classify("ok") == Intent.IGNORE  # heuristic catches this

"""Intent classification: heuristics → Ollama (qwen2.5:7b) → claude-sonnet fallback."""

import logging
import re
from enum import Enum
from typing import Optional

from src.backend.llm_client import LLMClient
from src.backend.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

INTENT_SYSTEM_PROMPT = (
    "Classify this WhatsApp message as either a QUERY (user asking a question "
    "they want answered now) or CAPTURE (information to save for later). "
    'Respond with JSON: {"intent": "query" or "capture"}'
)


class Intent(str, Enum):
    QUERY = "query"
    CAPTURE = "capture"
    IGNORE = "ignore"


class IntentRouter:
    """Three-tier classifier: heuristics → Ollama → claude-sonnet."""

    QUERY_PATTERNS = [
        r"\?$",
        r"^(what|when|where|who|how|why|which|is|are|was|were|do|does|did|can|could|tell me|remind me)\b",
    ]

    CAPTURE_PATTERNS = [
        r"https?://",
        r"^forwarded",
        r"^fwd:",
        r"^reminder:",
    ]

    IGNORE_PATTERNS = [
        r"^(hi|hello|hey|thanks|thank you|ok|okay|👍|🙏)$",
    ]

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        self.ollama_client = ollama_client
        self.llm_client = llm_client

    def classify(self, message: str) -> Intent:
        """Classify message intent.

        Tier 1: heuristics (instant, free)
        Tier 2: Ollama/qwen2.5:7b (local, free, ~3s)
        Tier 3: claude-sonnet via LiteLLM (cloud, fallback)
        """
        if not message or not message.strip():
            return Intent.IGNORE

        text = message.strip().lower()

        # Tier 1: heuristics
        for pattern in self.IGNORE_PATTERNS:
            if re.match(pattern, text, re.IGNORECASE):
                return Intent.IGNORE

        for pattern in self.QUERY_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return Intent.QUERY

        for pattern in self.CAPTURE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return Intent.CAPTURE

        # Tier 2: Ollama (local)
        if self.ollama_client and self.ollama_client.is_available():
            result = self.ollama_client.generate_json(INTENT_SYSTEM_PROMPT, message)
            if result and result.get("intent") in ("query", "capture"):
                return Intent.QUERY if result["intent"] == "query" else Intent.CAPTURE
            logger.debug("Ollama returned no usable result — falling through to LLM")

        # Tier 3: cloud LLM fallback
        if self.llm_client:
            return self._llm_classify(message)

        return Intent.CAPTURE

    def _llm_classify(self, message: str) -> Intent:
        """Cloud LLM fallback for genuinely ambiguous messages."""
        result = self.llm_client.generate_json(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_message=message,
        )
        if result and result.get("intent") == "query":
            return Intent.QUERY
        return Intent.CAPTURE

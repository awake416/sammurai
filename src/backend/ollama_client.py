"""Ollama client: local LLM wrapper for qwen2.5:7b."""

import json
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"


class OllamaClient:
    """Thin wrapper around Ollama HTTP API for local LLM inference."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = OLLAMA_BASE_URL,
        timeout: int = 45,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        """Return True if Ollama is running and reachable."""
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def generate_json(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 200,
    ) -> Optional[dict]:
        """Call Ollama and parse JSON from response. Returns None on failure."""
        prompt = f"{system_prompt}\n\n{user_message}\n\nReturn ONLY valid JSON, nothing else."

        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": max_tokens},
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
            return self._parse_json(raw)
        except Exception as e:
            logger.warning("Ollama call failed: %s", e)
            return None

    def _parse_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response, handling markdown code blocks."""
        # Strip markdown fences
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None

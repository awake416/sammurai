"""Email importance classifier using Ollama (local LLM).

Hybrid filtering: static keywords (fast path) + Qwen semantic classifier (fallback).
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from src.backend.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

CACHE_FILE = Path.home() / ".emailsync" / "filter_cache.json"


class EmailClassifier:
    """Classify email importance using hybrid keyword + LLM approach.

    Attributes:
        include_keywords: Auto-include subjects with these keywords
        exclude_keywords: Auto-exclude subjects with these keywords
        use_llm: Enable LLM classifier for ambiguous cases
        ollama_client: Local LLM client (Qwen)
        cache: Decision cache {hash(subject) -> bool}
    """

    def __init__(
        self,
        include_keywords: list[str] = None,
        exclude_keywords: list[str] = None,
        use_llm: bool = True,
    ):
        """Initialize classifier.

        Args:
            include_keywords: Auto-include list
            exclude_keywords: Auto-exclude list
            use_llm: Enable LLM classifier for ambiguous cases
        """
        self.include_keywords = [kw.lower() for kw in (include_keywords or [])]
        self.exclude_keywords = [kw.lower() for kw in (exclude_keywords or [])]
        self.use_llm = use_llm
        self.ollama_client = OllamaClient() if use_llm else None
        self.cache = self._load_cache()

    def _load_cache(self) -> dict:
        """Load decision cache from disk."""
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text())
            except Exception as e:
                logger.warning(f"Failed to load filter cache: {e}")
        return {}

    def _save_cache(self) -> None:
        """Save decision cache to disk."""
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(self.cache, indent=2))
        except Exception as e:
            logger.warning(f"Failed to save filter cache: {e}")

    def _cache_key(self, subject: str, domain: str) -> str:
        """Generate cache key from subject + domain."""
        key = f"{domain}:{subject}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def is_important(self, subject: str, domain: str) -> bool:
        """Classify email importance.

        Args:
            subject: Email subject line
            domain: Sender domain (e.g., "axisbank.com")

        Returns:
            True if email should be included in digest, False otherwise
        """
        subject_lower = subject.lower()

        # Fast path: keyword match
        if any(kw in subject_lower for kw in self.include_keywords):
            logger.debug(f"Auto-include (keyword match): {subject}")
            return True

        if any(kw in subject_lower for kw in self.exclude_keywords):
            logger.debug(f"Auto-exclude (keyword match): {subject}")
            return False

        # Check cache
        cache_key = self._cache_key(subject, domain)
        if cache_key in self.cache:
            decision = self.cache[cache_key]
            logger.debug(f"Cache hit: {subject} → {decision}")
            return decision

        # LLM classifier (fallback for ambiguous cases)
        if self.use_llm and self.ollama_client:
            decision = self._classify_with_llm(subject, domain)
            self.cache[cache_key] = decision
            self._save_cache()
            return decision

        # Default: include (conservative)
        logger.debug(f"No match, defaulting to include: {subject}")
        return True

    def _classify_with_llm(self, subject: str, domain: str) -> bool:
        """Use Qwen to classify email importance.

        Args:
            subject: Email subject line
            domain: Sender domain

        Returns:
            True if important, False otherwise
        """
        prompt = f"""You are classifying emails for a personal knowledge base.

Email subject: "{subject}"
From domain: {domain}

Is this email important for a personal knowledge base?

Important emails:
- Account closures, loan payoffs
- Interest rate changes (repo rate, MCLR)
- Tax certificates, statements
- Regulatory notices, policy changes
- Transaction confirmations (high-value)

Not important:
- Promotional offers (EMI conversions, credit card upgrades)
- Marketing campaigns
- Generic fraud/security alerts
- Tips and educational content

Answer with ONLY one word: YES or NO"""

        try:
            response = self.ollama_client.generate(
                prompt=prompt,
                system="You are a precise classifier. Answer only YES or NO.",
                max_tokens=10,
                temperature=0.0,
            )

            answer = response.strip().upper()
            if "YES" in answer:
                logger.info(f"LLM classified as important: {subject}")
                return True
            elif "NO" in answer:
                logger.info(f"LLM classified as not important: {subject}")
                return False
            else:
                logger.warning(f"Ambiguous LLM response for '{subject}': {response}")
                return True  # Default to include

        except Exception as e:
            logger.error(f"LLM classification failed for '{subject}': {e}")
            return True  # Fallback to include on error

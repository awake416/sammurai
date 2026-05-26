"""URL extractor: smart filtering + content extraction from message URLs.

Pipeline per URL:
  1. Extract URLs from message text
  2. Blocklist check (social media, messaging apps, link shorteners)
  3. SSRF-safe fetch in parallel (ThreadPoolExecutor)
  4. Content quality gate (< 200 chars = paywall/login, skip)
  5. Ollama relevance check — ONLY in interactive mode (skip in batch/digest)
  6. LLM summarization (cloud) or snippet fallback

Batch mode (digest runs): steps 1-4 + 6 only. No Ollama, concurrent fetches.
Interactive mode (Hermes): all 6 steps.
"""

import ipaddress
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.backend.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

BLOCKED_DOMAINS = {
    "wa.me", "whatsapp.com", "t.me", "telegram.me",
    "instagram.com", "facebook.com", "fb.com", "twitter.com", "x.com",
    "tiktok.com", "snapchat.com", "pinterest.com",
    "youtube.com", "youtu.be", "vimeo.com",
    "maps.google.com", "goo.gl",
    "bit.ly", "tinyurl.com", "ow.ly", "short.io", "rebrand.ly",
    "play.google.com", "apps.apple.com",
}

CONTENT_MIN_CHARS = 200
OLLAMA_CONTENT_MAX = 500
MAX_URLS_PER_BATCH = 5       # cap across all messages in a batch
FETCH_WORKERS = 4            # concurrent URL fetches
FETCH_TIMEOUT = 10           # per-URL timeout (seconds) — reduced from 15

RELEVANCE_SYSTEM_PROMPT = (
    "You are deciding whether a webpage's content is worth saving in a "
    "personal knowledge base (wiki). Relevant = factual info, articles, "
    "documents, how-to guides, news, property/legal/medical/financial details. "
    "Not relevant = ads, social posts, login pages, entertainment, spam. "
    'Respond ONLY with JSON: {"relevant": true} or {"relevant": false}'
)

URL_REGEX = re.compile(r'https://[^\s<>"\']+', re.IGNORECASE)


class URLExtractor:
    """Extract, filter, and summarize URLs found in WhatsApp messages."""

    def __init__(
        self,
        ollama_client: Optional[OllamaClient] = None,
        llm_client=None,
        fetch_timeout: int = FETCH_TIMEOUT,
        batch_mode: bool = False,
    ):
        self.ollama_client = ollama_client
        self.llm_client = llm_client
        self.fetch_timeout = fetch_timeout
        # batch_mode=True: skip Ollama relevance, run concurrent fetches
        self.batch_mode = batch_mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_message(self, text: str) -> list[dict]:
        """Return [{url, summary}] for meaningful URLs in a single message."""
        candidates = [u for u in self.extract_urls(text) if not self.is_blocked(u)]
        if not candidates:
            return []
        return self._process_urls(candidates)

    def extract_from_messages(self, texts: list[str]) -> dict[str, list[dict]]:
        """Process URLs across multiple messages concurrently.

        Returns {message_text: [{url, summary}]}.
        Caps total URLs at MAX_URLS_PER_BATCH across all messages.
        """
        # Collect candidates across all messages
        all_candidates: list[tuple[str, str]] = []  # (message_text, url)
        for text in texts:
            for url in self.extract_urls(text):
                if not self.is_blocked(url):
                    all_candidates.append((text, url))
                if len(all_candidates) >= MAX_URLS_PER_BATCH:
                    break
            if len(all_candidates) >= MAX_URLS_PER_BATCH:
                break

        if not all_candidates:
            return {}

        # Fetch all URLs concurrently
        url_content: dict[str, Optional[str]] = {}
        urls = list({url for _, url in all_candidates})
        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            future_to_url = {pool.submit(self.fetch_content, url): url for url in urls}
            for future in as_completed(future_to_url, timeout=self.fetch_timeout * 2):
                url = future_to_url[future]
                try:
                    url_content[url] = future.result()
                except Exception:
                    url_content[url] = None

        # Build results per message
        results: dict[str, list[dict]] = {}
        for text, url in all_candidates:
            content = url_content.get(url)
            if not content:
                continue
            if not self.batch_mode and not self.is_relevant(content):
                continue
            summary = self._summarize(content, url)
            if summary:
                results.setdefault(text, []).append({"url": url, "summary": summary})

        return results

    def extract_urls(self, text: str) -> list[str]:
        """Extract all https:// URLs from text."""
        return URL_REGEX.findall(text or "")

    def is_blocked(self, url: str) -> bool:
        """Return True if URL's domain is on the blocklist."""
        try:
            hostname = urlparse(url).hostname or ""
            hostname = hostname.removeprefix("www.")
            return hostname in BLOCKED_DOMAINS or any(
                hostname.endswith("." + d) for d in BLOCKED_DOMAINS
            )
        except Exception:
            return True

    def fetch_content(self, url: str) -> Optional[str]:
        """Fetch URL and extract clean text. Returns None if too short or error."""
        if self.is_blocked(url):
            return None
        if not self._is_safe_url(url):
            return None

        try:
            response = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Sammurai/1.0)"},
                timeout=self.fetch_timeout,
                follow_redirects=True,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            lines = (line.strip() for line in soup.get_text(separator=" ").splitlines())
            clean = " ".join(chunk for line in lines for chunk in line.split("  ") if chunk)

            if len(clean) < CONTENT_MIN_CHARS:
                return None

            return clean[:16000]

        except Exception as e:
            logger.debug("Fetch failed for %s: %s", url, e)
            return None

    def is_relevant(self, content: str) -> bool:
        """Ollama relevance check. Defaults True if unavailable or batch_mode."""
        if self.batch_mode:
            return True
        if not self.ollama_client or not self.ollama_client.is_available():
            return True

        result = self.ollama_client.generate_json(
            system_prompt=RELEVANCE_SYSTEM_PROMPT,
            user_message=content[:OLLAMA_CONTENT_MAX],
        )
        return bool((result or {}).get("relevant", True))

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _process_urls(self, urls: list[str]) -> list[dict]:
        """Fetch + filter + summarize a list of pre-filtered URLs."""
        if self.batch_mode:
            # Concurrent fetches in batch mode
            content_map: dict[str, Optional[str]] = {}
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                futures = {pool.submit(self.fetch_content, u): u for u in urls}
                for future in as_completed(futures, timeout=self.fetch_timeout * 2):
                    url = futures[future]
                    try:
                        content_map[url] = future.result()
                    except Exception:
                        content_map[url] = None
        else:
            content_map = {u: self.fetch_content(u) for u in urls}

        results = []
        for url in urls:
            content = content_map.get(url)
            if not content:
                continue
            if not self.is_relevant(content):
                continue
            summary = self._summarize(content, url)
            if summary:
                results.append({"url": url, "summary": summary})
        return results

    def _summarize(self, content: str, url: str) -> Optional[str]:
        """Summarize via LLM or return first 300 chars as snippet."""
        if self.llm_client:
            try:
                result = self.llm_client.generate_json(
                    system_prompt=(
                        "Summarize this webpage in 2-3 sentences for a personal knowledge base. "
                        "Focus on facts, dates, amounts, decisions. "
                        'Respond with JSON: {"summary": "..."}'
                    ),
                    user_message=f"URL: {url}\n\nContent:\n{content[:4000]}",
                )
                if result:
                    return result.get("summary")
            except Exception as e:
                logger.warning("LLM summarization failed: %s", e)

        return content[:300].rstrip() + "..." if len(content) > 300 else content

    def _is_safe_url(self, url: str) -> Optional[str]:
        """SSRF protection: reject private/loopback IPs."""
        try:
            parsed = urlparse(url)
            if parsed.scheme != "https":
                return None
            hostname = parsed.hostname
            if not hostname:
                return None

            try:
                addr_info = socket.getaddrinfo(hostname, None)
                ips = [info[4][0] for info in addr_info]
            except (socket.gaierror, ValueError):
                try:
                    ip = ipaddress.ip_address(hostname)
                    ips = [str(ip)]
                except ValueError:
                    return None

            for ip_str in ips:
                ip = ipaddress.ip_address(ip_str)
                if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast:
                    return None
                return ip_str

        except Exception as e:
            logger.error("URL validation error: %s", e)
        return None

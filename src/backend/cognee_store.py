"""Cognee-backed knowledge store: wiki ingestion + semantic/graph search."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Must set env before cognee import (cognee reads env at module load)
def _setup_cognee_env_early() -> None:
    """Set cognee env vars from litellm config before cognee import."""
    if os.environ.get("_COGNEE_ENV_SET"):
        return  # Already configured

    litellm_api_key = os.environ.get("LITELLM_API_KEY", "")
    litellm_base_url = os.environ.get("LITELLM_BASE_URL", "")

    defaults = {
        "LLM_API_KEY": litellm_api_key,
        "LLM_ENDPOINT": litellm_base_url,
        "LLM_MODEL": "gemini-2.5-flash",
        "LLM_PROVIDER": "custom",
        "EMBEDDING_API_KEY": litellm_api_key,
        "EMBEDDING_ENDPOINT": litellm_base_url,
        "EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_PROVIDER": "custom",
        "EMBEDDING_DIMENSIONS": "1536",
        "COGNEE_SKIP_CONNECTION_TEST": "true",
        "_COGNEE_ENV_SET": "1",
    }
    for key, val in defaults.items():
        if not os.environ.get(key) and val:
            os.environ[key] = val

_setup_cognee_env_early()


def _setup_cognee_env(config: Optional[dict] = None) -> None:
    """Map sammurai/litellm env vars to cognee's expected env vars.

    Uses litellm remote API for both LLM and embeddings (slow but reliable).
    Ollama embeddings incompatible: qwen2.5:7b outputs 3584 dims, cognee needs 1536.
    """
    litellm_api_key = os.environ.get("LITELLM_API_KEY", "")
    litellm_base_url = os.environ.get("LITELLM_BASE_URL", "")

    if config:
        llm_cfg = config.get("llm", {})
        embed_cfg = config.get("embeddings", {})
        llm_model = llm_cfg.get("model", "gemini-2.5-flash")  # Fast model for cognee
        embed_model = embed_cfg.get("model", "text-embedding-3-small")
    else:
        llm_model = os.environ.get("LLM_MODEL_NAME", "gemini-2.5-flash")
        embed_model = "text-embedding-3-small"

    # Strip provider prefix — LiteLLM proxy identifies models by name only,
    # not "openai/model-name". Adding the prefix causes 400 "Invalid model name".
    if "/" in llm_model:
        llm_model = llm_model.split("/", 1)[1]
    if "/" in embed_model:
        embed_model = embed_model.split("/", 1)[1]

    defaults = {
        "LLM_API_KEY": litellm_api_key,
        "LLM_ENDPOINT": litellm_base_url,
        "LLM_MODEL": llm_model,
        "LLM_PROVIDER": "custom",
        "EMBEDDING_API_KEY": litellm_api_key,
        "EMBEDDING_ENDPOINT": litellm_base_url,
        "EMBEDDING_MODEL": embed_model,
        "EMBEDDING_PROVIDER": "custom",
        "EMBEDDING_DIMENSIONS": "1536",
        "COGNEE_SKIP_CONNECTION_TEST": "true",
    }

    for key, val in defaults.items():
        if not os.environ.get(key) and val:
            os.environ[key] = val


def _run(coro):
    """Run async coroutine from sync context, reusing loop if already running."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


class CogneeStore:
    """Wiki knowledge store backed by cognee (vector + knowledge graph)."""

    def __init__(
        self,
        wiki_path: str,
        dataset_name: str = "sammurai_wiki",
        config: Optional[dict] = None,
    ):
        self.wiki_path = Path(wiki_path).expanduser()
        self.wiki_dir = self.wiki_path / "wiki"
        self.dataset_name = dataset_name
        _setup_cognee_env(config)

    def ingest_wiki(self) -> int:
        """Incrementally ingest all wiki .md files. Returns file count."""
        return _run(self._ingest_wiki())

    def rebuild_index(self) -> int:
        """Full re-index: prune cognee state then re-ingest all wiki files."""
        return _run(self._rebuild_index())

    def search(self, query: str) -> list[dict]:
        """Semantic + graph search over the wiki."""
        return _run(self._search(query))

    _NO_INFO_PHRASES = (
        "no information",
        "not available",
        "not found",
        "i don't have",
        "no data",
    )

    def get_relevant_context(self, query: str, context_limit: int = 3000) -> str:
        """Search and format results as a plain-text context string."""
        results = self.search(query)
        chunks = []
        total = 0
        for r in results:
            for text in r.get("search_result", []) if isinstance(r, dict) else [str(r)]:
                if not text:
                    continue
                # Drop LLM-fabricated "No information" sentences from SUMMARIES mode
                text_lower = text.lower()
                if any(p in text_lower for p in self._NO_INFO_PHRASES) and len(text) < 300:
                    logger.debug("Filtered fabricated no-info result: %s", text[:80])
                    continue
                if total + len(text) > context_limit:
                    break
                chunks.append(text)
                total += len(text)
        return "\n\n".join(chunks)

    async def _ingest_wiki(self) -> int:
        import cognee

        if not self.wiki_dir.exists():
            logger.warning("Wiki dir not found: %s", self.wiki_dir)
            return 0

        md_files = list(self.wiki_dir.glob("**/*.md"))
        if not md_files:
            logger.warning("No .md files in wiki dir")
            return 0

        for md_file in md_files:
            text = md_file.read_text(encoding="utf-8")
            await cognee.add(text, dataset_name=self.dataset_name)

        await cognee.cognify()
        logger.info("Ingested %d wiki files into cognee", len(md_files))
        return len(md_files)

    async def _rebuild_index(self) -> int:
        import cognee

        try:
            await cognee.prune.prune_data()
            await cognee.prune.prune_system(metadata=True)
        except Exception as e:
            logger.warning("Prune failed (continuing): %s", e)

        return await self._ingest_wiki()

    async def _search(self, query: str) -> list[dict]:
        import cognee
        import hashlib
        import json
        from pathlib import Path
        import time

        # Query cache (5min TTL)
        cache_dir = Path.home() / ".cache" / "sammurai" / "cognee"
        cache_dir.mkdir(parents=True, exist_ok=True)

        query_hash = hashlib.sha256(f"{self.dataset_name}:{query}".encode()).hexdigest()[:16]
        cache_file = cache_dir / f"{query_hash}.json"

        # Check cache
        if cache_file.exists():
            cache_age = time.time() - cache_file.stat().st_mtime
            if cache_age < 300:  # 5min TTL
                try:
                    with open(cache_file) as f:
                        cached = json.load(f)
                    logger.info("Cognee cache hit: %.1fs old", cache_age)
                    return cached
                except Exception:
                    pass

        # Cache miss — query cognee
        # CHUNKS returns raw text excerpts; SUMMARIES uses LLM which hallucinates
        # "No information available" strings that poison the context window.
        # Try CHUNKS first; fall back to SUMMARIES only if CHUNKS returns nothing.
        from cognee.api.v1.search.search import SearchType

        results = []
        for search_type in (SearchType.CHUNKS, SearchType.SUMMARIES):
            try:
                raw = await cognee.search(
                    query,
                    query_type=search_type,
                    datasets=[self.dataset_name],
                )
                if raw:
                    results = raw
                    logger.info("Cognee %s: %d results", search_type.name, len(raw))
                    break
            except Exception as e:
                logger.warning("Cognee %s search failed: %s", search_type.name, e)

        # Save cache
        try:
            cacheable = []
            for r in results:
                cached_r = {
                    "dataset_name": getattr(r, "dataset_name", r.get("dataset_name") if isinstance(r, dict) else None),
                    "search_result": r.get("search_result", []) if isinstance(r, dict) else [str(r)],
                }
                cacheable.append(cached_r)
            with open(cache_file, "w") as f:
                json.dump(cacheable, f)
        except Exception as e:
            logger.warning("Cache save failed: %s", e)

        return results

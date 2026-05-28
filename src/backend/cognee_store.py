"""Cognee-backed knowledge store: wiki ingestion + semantic/graph search."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _setup_cognee_env(config: Optional[dict] = None) -> None:
    """Map sammurai/litellm env vars to cognee's expected env vars."""
    litellm_api_key = os.environ.get("LITELLM_API_KEY", "")
    litellm_base_url = os.environ.get("LITELLM_BASE_URL", "")

    if config:
        llm_cfg = config.get("llm", {})
        embed_cfg = config.get("embeddings", {})
        model = llm_cfg.get("model", "claude-sonnet-4.6")
        embed_model = embed_cfg.get("model", "text-embedding-3-small")
    else:
        model = os.environ.get("LLM_MODEL_NAME", "gemini-2.5-flash")
        embed_model = "text-embedding-3-small"

    # litellm proxy requires "openai/" prefix for custom endpoints
    if "/" not in model:
        model = f"openai/{model}"
    if "/" not in embed_model:
        embed_model = f"openai/{embed_model}"

    defaults = {
        "LLM_API_KEY": litellm_api_key,
        "LLM_ENDPOINT": litellm_base_url,
        "LLM_MODEL": model,
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

    def get_relevant_context(self, query: str, context_limit: int = 3000) -> str:
        """Search and format results as a plain-text context string."""
        results = self.search(query)
        chunks = []
        total = 0
        for r in results:
            for text in r.get("search_result", []):
                if not text:
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

        results = await cognee.search(query, datasets=self.dataset_name)
        return results if results else []

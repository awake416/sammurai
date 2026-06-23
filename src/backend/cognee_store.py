"""Cognee-backed knowledge store: wiki ingestion + semantic search."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
import hashlib
import json
import time

logger = logging.getLogger(__name__)


def _configure_cognee() -> None:
    """Configure cognee via its config API (not env vars — avoids lru_cache timing issues)."""
    import cognee
    from dotenv import load_dotenv

    # Load sammurai-brain .env first (contains GEMINI_API_KEY for personal use)
    brain_env = Path.home() / "sammurai-brain" / ".env"
    if brain_env.exists():
        load_dotenv(brain_env, override=False)

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    litellm_key = os.environ.get("LITELLM_API_KEY", "")
    litellm_url = os.environ.get("LITELLM_BASE_URL", "")

    if litellm_key and litellm_url:
        # LiteLLM proxy — openai/ prefix forces routing to api_base, not Vertex AI
        cognee.config.set_llm_provider("custom")
        cognee.config.set_llm_endpoint(litellm_url)
        cognee.config.set_llm_api_key(litellm_key)
        cognee.config.set_llm_model("openai/gemini-3.5-flash")
    elif gemini_key:
        # Personal Gemini key fallback (free tier — rate-limited for large wikis)
        cognee.config.set_llm_provider("gemini")
        cognee.config.set_llm_api_key(gemini_key)
        cognee.config.set_llm_model("gemini/gemini-2.0-flash")

    # Embeddings — only via LiteLLM proxy; bare model name routes to wrong provider
    # openai/ prefix forces the proxy to use api_base instead of direct OpenAI/Vertex
    if litellm_key and litellm_url:
        cognee.config.set_embedding_provider("litellm")
        cognee.config.set_embedding_endpoint(litellm_url)
        cognee.config.set_embedding_api_key(litellm_key)
        cognee.config.set_embedding_model("openai/text-embedding-3-small")
        cognee.config.set_embedding_dimensions(1536)
    elif gemini_key:
        # Gemini embeddings fallback — uses text-embedding-004
        cognee.config.set_embedding_provider("gemini")
        cognee.config.set_embedding_api_key(gemini_key)
        cognee.config.set_embedding_model("models/text-embedding-004")
        cognee.config.set_embedding_dimensions(768)

    os.environ["COGNEE_SKIP_CONNECTION_TEST"] = "true"


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
    """Wiki knowledge store backed by cognee (vector search, no graph extraction)."""

    def __init__(
        self,
        wiki_path: str,
        dataset_name: str = "sammurai_wiki",
        config: Optional[dict] = None,
    ):
        self.wiki_path = Path(wiki_path).expanduser()
        self.wiki_dir = self.wiki_path / "wiki"
        self.dataset_name = dataset_name
        self._cache_dir = Path.home() / ".cache" / "sammurai" / "cognee"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def ingest_wiki(self) -> int:
        """Incrementally ingest all wiki .md files. Returns file count."""
        return _run(self._ingest_wiki())

    def rebuild_index(self) -> int:
        """Full re-index: prune cognee state then re-ingest all wiki files."""
        return _run(self._rebuild_index())

    def search(self, query: str) -> list[dict]:
        """Semantic search over the wiki using cognee CHUNKS."""
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
        chunks: list[str] = []
        total = 0
        for r in results:
            items = r.get("search_result", []) if isinstance(r, dict) else [r]
            for text in items:
                if isinstance(text, dict):
                    text = text.get("text") or text.get("content") or str(text)
                if not text or not isinstance(text, str):
                    continue
                text_lower = text.lower()
                if any(p in text_lower for p in self._NO_INFO_PHRASES) and len(text) < 300:
                    logger.debug("Filtered no-info result: %s", text[:80])
                    continue
                if total + len(text) > context_limit:
                    break
                chunks.append(text)
                total += len(text)
        return "\n\n".join(chunks)

    async def _run_pipeline(self, datasets) -> None:
        """Run cognee's ingest pipeline with graph extraction + 512-token chunks.

        Uses custom task list to control chunk size and avoid embed_triplets
        which causes excessive embedding calls when graph edges scale up.
        """
        import cognee
        from cognee.modules.chunking.TextChunker import TextChunker
        from cognee.modules.pipelines import run_pipeline
        from cognee.modules.pipelines.layers.pipeline_execution_mode import get_pipeline_executor
        from cognee.modules.pipelines.tasks.task import Task
        from cognee.modules.users.methods import get_default_user
        from cognee.shared.data_models import KnowledgeGraph
        from cognee.tasks.documents import classify_documents, extract_chunks_from_documents
        from cognee.tasks.graph.extract_graph_and_summarize import extract_graph_and_summarize
        from cognee.tasks.ingestion.extract_dlt_fk_edges import extract_dlt_fk_edges
        from cognee.tasks.storage import add_data_points
        from cognee.modules.ontology.get_default_ontology_resolver import get_default_ontology_resolver

        user = await get_default_user()
        config = {"ontology_config": {"ontology_resolver": get_default_ontology_resolver()}}

        tasks = [
            Task(classify_documents),
            Task(extract_chunks_from_documents, max_chunk_size=512, chunker=TextChunker),
            Task(
                extract_graph_and_summarize,
                graph_model=KnowledgeGraph,
                config=config,
                task_config={"batch_size": 20},
            ),
            # embed_triplets=False avoids embedding graph edges (expensive + error-prone)
            Task(add_data_points, embed_triplets=False, task_config={"batch_size": 50}),
            Task(extract_dlt_fk_edges),
        ]

        executor = get_pipeline_executor(run_in_background=False)
        await executor(
            pipeline=run_pipeline,
            tasks=tasks,
            user=user,
            datasets=datasets if isinstance(datasets, list) else [datasets],
            vector_db_config=None,
            graph_db_config=None,
            incremental_loading=True,
            use_pipeline_cache=True,
            pipeline_name="cognify_pipeline",
            data_per_batch=10,
        )

    async def _ingest_wiki(self) -> int:
        import cognee

        _configure_cognee()

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

        await self._run_pipeline(self.dataset_name)
        logger.info("Ingested %d wiki files into cognee (with graph extraction)", len(md_files))
        return len(md_files)

    async def _rebuild_index(self) -> int:
        import cognee
        import glob

        _configure_cognee()

        # Clear stale LadybugDB lock files left by crashed processes
        db_path = Path.home() / ".venv/lib/python3.12/site-packages/cognee/.cognee_system/databases"
        for lbug in list(db_path.glob("**/*.lbug")) + list(db_path.glob("**/*.lbug.wal")):
            try:
                lbug.unlink()
            except OSError:
                pass

        try:
            await cognee.prune.prune_data()
            await cognee.prune.prune_system(metadata=True)
        except Exception as e:
            logger.warning("Prune failed (continuing): %s", e)

        return await self._ingest_wiki()

    async def _search(self, query: str) -> list[dict]:
        import cognee
        from cognee.api.v1.search.search import SearchType

        _configure_cognee()

        cache_key = hashlib.sha256(f"{self.dataset_name}:{query}".encode()).hexdigest()[:16]
        cache_file = self._cache_dir / f"{cache_key}.json"

        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < 300:
                try:
                    return json.loads(cache_file.read_text())
                except Exception:
                    pass

        results = []
        # CHUNKS: raw text vector search (no LLM needed, honest empty on miss)
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
                logger.warning("Cognee %s failed: %s", search_type.name, e)

        cacheable = []
        for r in results:
            cacheable.append({
                "search_result": r.get("search_result", []) if isinstance(r, dict) else [str(r)],
            })

        try:
            cache_file.write_text(json.dumps(cacheable))
        except Exception:
            pass

        return results

"""RAG pipeline: ingest wiki files, query with cognee (vector + knowledge graph)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.backend.cognee_store import CogneeStore

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    """Configuration for the RAG pipeline."""

    wiki_path: str = "~/sammurai-brain/wiki"
    dataset_name: str = "sammurai_wiki"
    llm_model: str = "claude-sonnet-4.6"
    embedding_model: str = "text-embedding-3-small"
    top_k: int = 5


class RAGPipeline:
    """Orchestrates wiki ingestion and RAG querying via cognee."""

    def __init__(self, config: PipelineConfig, sammurai_config: Optional[dict] = None):
        self.config = config
        self.wiki_path = str(Path(config.wiki_path).expanduser())
        self.store = CogneeStore(
            wiki_path=self.wiki_path,
            dataset_name=config.dataset_name,
            config=sammurai_config,
        )

    def ingest(self) -> dict:
        """Incrementally index wiki files into cognee."""
        count = self.store.ingest_wiki()
        if count == 0:
            return {"status": "empty", "files_processed": 0}
        return {"status": "success", "files_processed": count}

    def rebuild(self) -> dict:
        """Full re-index: prune cognee state and re-ingest all wiki files."""
        count = self.store.rebuild_index()
        return {"status": "success", "files_processed": count}

    def query(self, question: str, top_k: Optional[int] = None) -> dict:
        """RAG query: retrieve context + generate answer via LLM."""
        from src.backend.llm_client import LLMClient

        context = self.store.get_relevant_context(question, context_limit=4000)

        if not context:
            return {
                "answer": "No relevant information found in the wiki.",
                "sources": [],
            }

        llm = LLMClient(model=self.config.llm_model)
        response = llm.generate_json(
            system_prompt=(
                "You are a knowledge assistant. Answer the question using ONLY "
                "the provided context. If the context doesn't contain the answer, "
                "say so. Be concise. Respond with JSON: "
                '{"answer": "your answer here"}'
            ),
            user_message=f"Context:\n{context}\n\nQuestion: {question}",
        )

        return {
            "answer": response.get("answer", "Unable to generate answer."),
            "sources": [],
        }

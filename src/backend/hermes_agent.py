"""Hermes agent: read-only query agent for the wiki Second Brain."""

import logging
from pathlib import Path
from typing import Optional

from src.backend.cognee_store import CogneeStore
from src.backend.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Hermes, a personal knowledge assistant connected to the user's Second Brain wiki.

Rules:
- ONLY answer based on information retrieved from the wiki context provided
- NEVER invent, hallucinate, or guess information
- If the context doesn't contain the answer, say "I don't have that in my notes"
- Always cite which file your answer comes from (e.g., "from tasks.md" or "from school.md")
- Keep responses concise — this goes to WhatsApp (max 2-3 sentences)
- Use plain text, no markdown formatting (WhatsApp doesn't render it well)

Respond with JSON: {"answer": "your response text", "sources": ["filename1.md", "filename2.md"]}
"""


class HermesAgent:
    """Read-only agent that answers questions from the wiki."""

    def __init__(
        self,
        llm_client: LLMClient,
        cognee_store: CogneeStore,
        wiki_path: str,
    ):
        self.llm_client = llm_client
        self.cognee_store = cognee_store
        self.wiki_path = Path(wiki_path).expanduser() / "wiki"

    def answer(self, question: str) -> str:
        """Answer a question using cognee semantic + graph search."""
        if not question.strip():
            return "Please ask me a question."

        context = self.cognee_store.get_relevant_context(question, context_limit=3000)

        if not context:
            return "I don't have any information about that in my notes yet."

        result = self.llm_client.generate_json(
            system_prompt=SYSTEM_PROMPT,
            user_message=f"Context from wiki:\n{context}\n\nUser question: {question}",
        )

        if not result:
            return "Sorry, I couldn't process that question. Try again."

        answer = result.get("answer", "I don't have that in my notes.")
        sources = result.get("sources", [])

        if sources:
            return answer + f" (from: {', '.join(sources)})"
        return answer

    def read_file(self, filepath: str) -> Optional[str]:
        """Read a specific wiki file (path-validated to wiki dir)."""
        requested = Path(filepath)

        if requested.is_absolute():
            resolved = requested.resolve()
        else:
            resolved = (self.wiki_path / requested).resolve()

        wiki_resolved = self.wiki_path.resolve()
        if not str(resolved).startswith(str(wiki_resolved)):
            logger.warning("Path traversal attempt blocked: %s", filepath)
            return None

        if not resolved.exists():
            return None

        return resolved.read_text(encoding="utf-8")

    def search_wiki(self, query: str) -> list[dict]:
        """Search wiki via cognee. Returns raw cognee result dicts."""
        return self.cognee_store.search(query)

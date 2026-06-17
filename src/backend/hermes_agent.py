"""Hermes agent: read-only query agent for the wiki Second Brain."""

import logging
from pathlib import Path

from src.backend.cognee_store import CogneeStore
from src.backend.llm_client import LLMClient
from src.backend.memory_consolidator import MemoryConsolidator
from src.backend.memory_inbox import MemoryInbox

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Hermes, a personal knowledge assistant connected to the user's Second Brain wiki.

## Reading the Wiki

The compiled/ directory contains agent-facing pages with YAML frontmatter lifecycle tags:

  status: hypothesis   → Single source, unverified. Prefix answer: "Possibly: ..."
  status: tested       → Corroborated or confirmed. Cite normally.
  status: decision     → Human-verified. Cite with full confidence.
  status: conflict     → Contradictory claims pending human review.
                         NEVER cite a conflict page as a decision.
                         Present BOTH values and flag: "⚠️ Conflict — needs review."

## Answering Rules

- ONLY answer based on retrieved wiki context
- NEVER invent, hallucinate, or guess information
- Match confidence to lifecycle: hedge on hypothesis, flag conflicts, assert on decision
- If the context doesn't contain the answer, say "I don't have that in my notes"
- Always cite source file (e.g., "from tasks.md")
- Keep responses concise — WhatsApp delivery, max 2-3 sentences
- Plain text only, no markdown

## Saving New Information

If the user tells you something NEW (a fact, decision, task, observation):
- Acknowledge it
- Say: "Noted — I'll save this to my inbox for consolidation."
- The calling system will invoke dump_to_inbox() programmatically.

## Output Format

Respond with JSON:
{
  "answer": "your response text",
  "sources": ["filename1.md"],
  "lifecycle_warning": "conflict|hypothesis|null"
}
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
        self.brain_path = Path(wiki_path).expanduser()
        self.inbox = MemoryInbox(str(self.brain_path))

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

    def read_file(self, filepath: str) -> str | None:
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

    def dump_to_inbox(self, note: str, tags: list[str] | None = None) -> bool:
        """Append a raw note to the ambient inbox for next consolidation pass."""
        try:
            self.inbox.dump(note, tags=tags)
            return True
        except Exception as e:
            logger.error("Failed to write to inbox: %s", e)
            return False

    def get_lifecycle_status(self, filename: str) -> str | None:
        """Return the lifecycle status of a compiled/ page, or None if untracked."""
        compiled_path = self.brain_path / "compiled" / filename
        if not compiled_path.exists():
            return None
        content = compiled_path.read_text(encoding="utf-8")
        fm, _ = MemoryConsolidator._parse_frontmatter(content)
        return fm.get("status")

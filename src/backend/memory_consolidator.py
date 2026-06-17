"""Memory Consolidator: reads inbox, structures facts, routes to compiled/ with conflict detection.

Algorithm:
1. Read all pending inbox files
2. LLM extracts facts with lifecycle stage
3. Per fact: compare with existing compiled/ page
4. If conflict: auto-resolve by timestamp (newer wins as live content)
   but inject [!CONFLICT] marker for human review
5. Mark inbox files as processed (immutable — never deleted)
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from src.backend.llm_client import LLMClient
from src.backend.memory_inbox import MemoryInbox

logger = logging.getLogger(__name__)

def _ngrams(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = text.lower().split()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


CONSOLIDATION_PROMPT = """You are a memory consolidator. Extract structured facts from raw session notes.

For each distinct fact/claim/task/decision found, produce one entry.

Lifecycle rules:
- hypothesis: single source, first mention, unverified
- tested: corroborated by 2+ sources OR confirmed by action/outcome
- decision: explicitly decided, irreversible, or user-marked

Output JSON array:
[
  {
    "slug": "snake_case_topic_identifier",
    "claim": "The specific factual claim or task, one sentence",
    "lifecycle": "hypothesis|tested|decision",
    "source_timestamp": "ISO timestamp from the note, or null",
    "tags": ["work", "personal", "protium", etc],
    "page_hint": "existing_page_filename.md or null if new topic"
  }
]

Only extract concrete, durable facts — not pleasantries or one-off messages.
"""


class ConsolidatedFact:
    """A structured fact ready for compiled/ routing."""

    def __init__(
        self,
        slug: str,
        claim: str,
        lifecycle: str,
        source_timestamp: str | None,
        tags: list[str],
        page_hint: str | None,
    ):
        self.slug = slug
        self.claim = claim
        self.lifecycle = lifecycle if lifecycle in ("hypothesis", "tested", "decision") else "hypothesis"
        self.source_timestamp = source_timestamp
        self.tags = tags
        self.page_hint = page_hint


class MemoryConsolidator:
    """Reads inbox, structures facts, writes to compiled/ with conflict detection."""

    def __init__(
        self,
        brain_path: str = "~/sammurai-brain",
        llm_client: LLMClient | None = None,
    ):
        self.brain_path = Path(brain_path).expanduser()
        self.compiled_dir = self.brain_path / "compiled"
        self.raw_dir = self.brain_path / "raw" / "consolidated"
        self.inbox = MemoryInbox(brain_path)
        self.llm = llm_client
        self._compiled_dir_resolved = self.compiled_dir.resolve()

    def run(self) -> int:
        """Process all pending inbox files. Returns count of facts consolidated."""
        pending = self.inbox.pending_files()
        if not pending:
            logger.info("No pending inbox files")
            return 0

        self.compiled_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        total = 0
        for inbox_file in pending:
            facts = self._extract_facts(inbox_file)
            if not facts:
                self.inbox.mark_processed(inbox_file)
                continue

            # Archive raw structured facts
            self._archive_raw(inbox_file, facts)

            # Route each fact to compiled/
            for fact in facts:
                self._route_fact(fact, inbox_file)
                total += 1

            self.inbox.mark_processed(inbox_file)
            logger.info("Consolidated %d facts from %s", len(facts), inbox_file.name)

        return total

    def _extract_facts(self, inbox_file: Path) -> list[ConsolidatedFact]:
        """Call LLM to extract structured facts from raw inbox content."""
        content = inbox_file.read_text(encoding="utf-8")
        # Strip frontmatter for LLM
        body = re.sub(r"^---.*?---\s*", "", content, flags=re.DOTALL).strip()

        if not body or len(body) < 20:
            return []

        if not self.llm:
            logger.warning("No LLM configured — skipping fact extraction")
            return []

        result = self.llm.generate_json(CONSOLIDATION_PROMPT, f"Session notes:\n{body}")
        if not result or not isinstance(result, list):
            logger.error("Consolidator LLM returned no valid facts")
            return []

        facts = []
        for item in result:
            try:
                facts.append(
                    ConsolidatedFact(
                        slug=item.get("slug", "unknown"),
                        claim=item.get("claim", ""),
                        lifecycle=item.get("lifecycle", "hypothesis"),
                        source_timestamp=item.get("source_timestamp"),
                        tags=item.get("tags", []),
                        page_hint=item.get("page_hint"),
                    )
                )
            except Exception as e:
                logger.warning("Skipping malformed fact: %s", e)

        return facts

    def _route_fact(self, fact: ConsolidatedFact, source_file: Path) -> None:
        """Write fact to compiled/ page, injecting [!CONFLICT] if claim conflicts."""
        target_file = self._resolve_target(fact)
        now_iso = datetime.now(UTC).isoformat()

        if not target_file.exists():
            self._create_page(target_file, fact, now_iso)
            return

        existing_content = target_file.read_text(encoding="utf-8")
        frontmatter, body = self._parse_frontmatter(existing_content)

        # Check for semantic conflict: same slug already has a different claim
        existing_claim = frontmatter.get("claim")
        if existing_claim and self._claims_conflict(existing_claim, fact.claim):
            self._inject_conflict(
                target_file, frontmatter, body,
                fact, existing_claim, now_iso,
            )
        else:
            self._append_fact(target_file, frontmatter, body, fact, now_iso)

    def _resolve_target(self, fact: ConsolidatedFact) -> Path:
        """Determine compiled/ file path for a fact (path-traversal safe)."""
        safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", fact.slug)[:100] or "unknown"
        if fact.page_hint:
            target = (self.compiled_dir / fact.page_hint).resolve()
            if not target.is_relative_to(self._compiled_dir_resolved):
                logger.warning("page_hint traversal blocked: %s", fact.page_hint)
                return self.compiled_dir / f"{safe_slug}.md"
            return target
        return self.compiled_dir / f"{safe_slug}.md"

    def _create_page(self, path: Path, fact: ConsolidatedFact, now_iso: str) -> None:
        """Write new compiled page with YAML frontmatter."""
        fm = {
            "slug": fact.slug,
            "claim": fact.claim,
            "status": fact.lifecycle,
            "created_at": now_iso,
            "updated_at": now_iso,
            "conflict_resolved": True,
            "tags": fact.tags,
        }
        body = (
            f"# {fact.slug.replace('_', ' ').title()}\n\n"
            f"**Status:** `{fact.lifecycle}`\n\n"
            f"{fact.claim}\n"
        )
        path.write_text(self._render_frontmatter(fm) + body, encoding="utf-8")
        logger.debug("Created compiled page: %s", path.name)

    def _append_fact(
        self,
        path: Path,
        frontmatter: dict,
        body: str,
        fact: ConsolidatedFact,
        now_iso: str,
    ) -> None:
        """Append new observation to existing page and update frontmatter."""
        frontmatter["updated_at"] = now_iso
        # Promote lifecycle: hypothesis < tested < decision
        order = {"hypothesis": 0, "tested": 1, "decision": 2}
        if order.get(fact.lifecycle, 0) > order.get(frontmatter.get("status", "hypothesis"), 0):
            frontmatter["status"] = fact.lifecycle

        new_section = f"\n## Update ({now_iso[:10]})\n\n{fact.claim}\n"
        new_content = self._render_frontmatter(frontmatter) + body.rstrip() + new_section
        path.write_text(new_content, encoding="utf-8")

    @staticmethod
    def _sanitize_claim(claim: str) -> str:
        """Strip prompt-injection prefixes from LLM-controlled claim text."""
        # Remove leading `>` (blockquote markers that would escape the conflict block)
        # and common instruction-injection patterns
        sanitized = claim.strip().replace("\n", " ").replace("\r", "")
        sanitized = re.sub(r"^(>+\s*)+", "", sanitized)
        return sanitized

    def _inject_conflict(
        self,
        path: Path,
        frontmatter: dict,
        body: str,
        new_fact: ConsolidatedFact,
        old_claim: str,
        now_iso: str,
    ) -> None:
        """Auto-resolve by timestamp (newer wins), inject [!CONFLICT] for human review."""
        old_ts = frontmatter.get("updated_at", "unknown")
        new_ts = new_fact.source_timestamp or now_iso

        # Newer timestamp wins as live canonical claim
        try:
            old_dt = datetime.fromisoformat(old_ts) if old_ts != "unknown" else datetime.min.replace(tzinfo=UTC)
            new_dt = datetime.fromisoformat(new_ts)
            newer_wins = new_dt >= old_dt
        except ValueError:
            newer_wins = True  # default: new wins if timestamps unparseable

        canonical = self._sanitize_claim(new_fact.claim if newer_wins else old_claim)
        superseded = self._sanitize_claim(old_claim if newer_wins else new_fact.claim)
        canonical_ts = new_ts if newer_wins else old_ts
        superseded_ts = old_ts if newer_wins else new_ts

        conflict_block = f"""
> [!CONFLICT] AUTO-RESOLVED by timestamp
> **CANONICAL** ({canonical_ts[:10]}): `{canonical}`
> **SUPERSEDED** ({superseded_ts[:10]}): `{superseded}`
> **STATUS:** awaiting human review — delete this block and set `status: tested|decision` to clear
"""

        frontmatter["claim"] = canonical
        frontmatter["status"] = "conflict"
        frontmatter["updated_at"] = now_iso
        frontmatter["conflict_resolved"] = False
        frontmatter["conflict_since"] = now_iso

        new_content = self._render_frontmatter(frontmatter) + body.rstrip() + "\n" + conflict_block
        path.write_text(new_content, encoding="utf-8")
        logger.warning(
            "Conflict injected in %s (auto-resolved: '%s' wins)",
            path.name,
            "new" if newer_wins else "old",
        )

    def _archive_raw(self, inbox_file: Path, facts: list[ConsolidatedFact]) -> None:
        """Save structured facts to raw/consolidated/ (immutable record)."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        archive_file = self.raw_dir / f"consolidated_{inbox_file.stem}_{stamp}.md"

        lines = [f"# Consolidated Facts: {inbox_file.name}\n\nSource: {inbox_file}\n"]
        for f in facts:
            lines.append(
                f"\n## {f.slug}\n\n"
                f"- **claim:** {f.claim}\n"
                f"- **lifecycle:** {f.lifecycle}\n"
                f"- **tags:** {', '.join(f.tags)}\n"
                f"- **source_ts:** {f.source_timestamp or 'null'}\n"
            )

        archive_file.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _claims_conflict(existing: str, incoming: str) -> bool:
        """Heuristic: claims conflict if they assert different values for the same subject.

        Simple: if both non-empty and they differ enough (>30 chars diff or no common 5-gram).
        A full semantic diff needs an LLM call; this avoids that cost on every write.
        """
        if not existing or not incoming:
            return False
        if existing.strip().lower() == incoming.strip().lower():
            return False
        shared = _ngrams(existing) & _ngrams(incoming)
        return len(shared) == 0 and abs(len(existing) - len(incoming)) > 5

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """Split YAML frontmatter from body. Returns (frontmatter_dict, body_str)."""
        match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        if not match:
            return {}, content
        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        body = content[match.end():]
        return fm, body

    @staticmethod
    def _render_frontmatter(fm: dict) -> str:
        """Render dict back to YAML frontmatter block."""
        dumped = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        return f"---\n{dumped}\n---\n\n"

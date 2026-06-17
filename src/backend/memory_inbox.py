"""Ambient Inbox: frictionless append-only session note capture.

Agents dump raw thoughts here without structure. Consolidator reads later.
Each session gets its own file: inbox/YYYY-MM-DD_<session_id>.md
"""

import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

INBOX_HEADER = """\
---
session_id: {session_id}
created_at: {created_at}
status: pending
---

# Session Inbox: {date_str}

"""


class MemoryInbox:
    """Append-only inbox for session notes."""

    def __init__(self, brain_path: str = "~/sammurai-brain"):
        self.brain_path = Path(brain_path).expanduser()
        self.inbox_dir = self.brain_path / "inbox"
        self._session_file: Path | None = None

    def _get_session_file(self) -> Path:
        """Return current session file, creating it if needed."""
        if self._session_file is not None:
            return self._session_file

        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        # Stable session ID: hash of PID + day (one file per process per day)
        now = datetime.now(UTC)
        day_str = now.strftime("%Y-%m-%d")
        raw = f"{os.getpid()}:{day_str}"
        session_id = hashlib.sha1(raw.encode()).hexdigest()[:8]

        self._session_file = self.inbox_dir / f"{day_str}_{session_id}.md"

        if not self._session_file.exists():
            created_at = now.isoformat()
            header = INBOX_HEADER.format(
                session_id=session_id,
                created_at=created_at,
                date_str=day_str,
            )
            self._session_file.write_text(header, encoding="utf-8")
            logger.debug("Created inbox session: %s", self._session_file.name)

        return self._session_file

    def dump(self, note: str, tags: list[str] | None = None) -> None:
        """Append a raw note to the current session inbox file.

        Args:
            note: Free-form text (thought, observation, task, decision).
            tags: Optional list of routing hints (e.g. ["work", "protium"]).
        """
        if not note.strip():
            return

        session_file = self._get_session_file()
        ts = datetime.now(UTC).strftime("%H:%M UTC")
        tag_str = f"  <!-- tags: {', '.join(tags)} -->" if tags else ""

        entry = f"\n## {ts}{tag_str}\n\n{note.strip()}\n"
        with open(session_file, "a", encoding="utf-8") as f:
            f.write(entry)

        logger.debug("Inbox dump: %d chars → %s", len(note), session_file.name)

    def pending_files(self) -> list[Path]:
        """Return all inbox files with status: pending."""
        if not self.inbox_dir.exists():
            return []

        pending = []
        for f in sorted(self.inbox_dir.glob("*.md")):
            try:
                content = f.read_text(encoding="utf-8")
                if "status: pending" in content[:300]:
                    pending.append(f)
            except OSError:
                pass
        return pending

    def mark_processed(self, inbox_file: Path) -> None:
        """Stamp inbox file as processed (preserves content — immutable source)."""
        content = inbox_file.read_text(encoding="utf-8")
        processed_at = datetime.now(UTC).isoformat()
        content = content.replace(
            "status: pending",
            f"status: processed\nprocessed_at: {processed_at}",
            1,
        )
        inbox_file.write_text(content, encoding="utf-8")
        logger.info("Marked processed: %s", inbox_file.name)

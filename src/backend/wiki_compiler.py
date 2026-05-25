"""Wiki compiler: reads raw digests, calls LLM to produce structured wiki updates."""

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.backend.llm_client import LLMClient
from src.backend.wiki_models import WikiUpdate, ConceptPage, TaskEntry, LogEntry

logger = logging.getLogger(__name__)

COMPILATION_SYSTEM_PROMPT = """You are a wiki compiler for a personal knowledge management system.
Given the current wiki state and a new daily digest, produce a structured JSON update.

Rules:
- Extract NEW tasks into tasks_to_add (don't duplicate existing tasks)
- If a task in the digest is clearly completed, add its text to tasks_to_remove
- Create concept pages for topics that deserve their own page (recurring themes, important events)
- Update existing concept pages by using action "append" (don't overwrite)
- Add links to new concept pages in index_additions
- Write a brief log entry summarizing what changed

Output JSON matching this exact schema:
{
    "tasks_to_add": [{"text": "...", "priority": "High|Medium|Low", "due_date": "YYYY-MM-DD or null", "category": "...", "source_group": "..."}],
    "tasks_to_remove": ["exact text of completed tasks"],
    "concept_pages": [{"filename": "topic_name.md", "content": "# Title\\n\\nContent...", "action": "create|update|append"}],
    "index_additions": ["- [Topic Name](topic_name.md) — brief description"],
    "log_entry": {"summary": "What was updated", "pages_affected": ["tasks.md", "topic.md"]}
}

Rules for concept pages:
- Use snake_case filenames
- Content must be valid Markdown with a # heading
- Only create pages for substantive topics (not one-off messages)
- When appending, include only the NEW content section (with ## subheading)
"""


class WikiCompiler:
    """Compiles raw digests into structured wiki updates."""

    def __init__(self, llm_client: LLMClient, wiki_path: str, schema_path: Optional[str] = None):
        self.llm_client = llm_client
        self.wiki_path = Path(wiki_path).expanduser()
        self.wiki_dir = self.wiki_path / "wiki"
        self.raw_dir = self.wiki_path / "raw"

        self.schema = ""
        if schema_path:
            schema_file = self.wiki_path / schema_path
            if schema_file.exists():
                self.schema = schema_file.read_text(encoding="utf-8")

    def ensure_structure(self) -> None:
        """Create wiki directory structure if it doesn't exist."""
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

        index = self.wiki_dir / "index.md"
        if not index.exists():
            index.write_text("# Knowledge Index\n\n## Pages\n\n", encoding="utf-8")

        tasks = self.wiki_dir / "tasks.md"
        if not tasks.exists():
            tasks.write_text("# Active Tasks\n\n", encoding="utf-8")

        log = self.wiki_dir / "log.md"
        if not log.exists():
            log.write_text("# Update Log\n\n", encoding="utf-8")

    def compile_digest(self, raw_digest_path: str) -> Optional[WikiUpdate]:
        """Read raw digest, call LLM, return structured WikiUpdate."""
        digest_file = Path(raw_digest_path)
        if not digest_file.exists():
            logger.error(f"Digest file not found: {raw_digest_path}")
            return None

        raw_content = digest_file.read_text(encoding="utf-8")
        if not raw_content.strip():
            logger.warning("Empty digest, skipping compilation")
            return None

        current_index = self._read_wiki_file("index.md")
        current_tasks = self._read_wiki_file("tasks.md")

        system_prompt = COMPILATION_SYSTEM_PROMPT
        if self.schema:
            system_prompt += f"\n\nWiki Schema:\n{self.schema}"

        user_message = (
            f"Current index.md:\n```\n{current_index}\n```\n\n"
            f"Current tasks.md:\n```\n{current_tasks}\n```\n\n"
            f"New Daily Digest:\n```\n{raw_content}\n```"
        )

        result = self.llm_client.generate_json(system_prompt, user_message)
        if not result:
            logger.error("LLM returned no result for wiki compilation")
            return None

        try:
            return WikiUpdate(**result)
        except Exception as e:
            logger.error(f"Failed to parse WikiUpdate: {e}")
            return None

    def apply_update(self, update: WikiUpdate) -> None:
        """Write wiki update to filesystem."""
        if not update.has_changes():
            logger.info("No changes to apply")
            return

        self.ensure_structure()

        if update.tasks_to_add:
            self._append_tasks(update.tasks_to_add)

        if update.tasks_to_remove:
            self._remove_tasks(update.tasks_to_remove)

        for page in update.concept_pages:
            self._write_concept_page(page)

        if update.index_additions:
            self._update_index(update.index_additions)

        self._append_log(update.log_entry)
        logger.info(f"Applied wiki update: {update.log_entry.summary}")

    def git_commit(self, message: Optional[str] = None) -> bool:
        """Stage all changes and commit, with safety guard against cascade hallucinations."""
        if not message:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            message = f"Auto-update: {date_str}"

        try:
            subprocess.run(
                ["git", "add", "."],
                cwd=self.wiki_path,
                check=True,
                capture_output=True,
            )

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.wiki_path,
                capture_output=True,
                text=True,
            )
            if not status.stdout.strip():
                logger.info("No changes to commit")
                return False

            # Safety guard: if >50% of tracked wiki files changed AND last commit was
            # recent (<1h), this looks like an LLM hallucination cascade — skip commit.
            changed_files = [
                l for l in status.stdout.strip().splitlines()
                if l.strip().startswith(("M ", "A ", "D ", "R "))
            ]
            tracked = subprocess.run(
                ["git", "ls-files", "wiki/"],
                cwd=self.wiki_path,
                capture_output=True,
                text=True,
            )
            tracked_count = len([l for l in tracked.stdout.strip().splitlines() if l])
            if tracked_count > 0:
                change_ratio = len(changed_files) / tracked_count
                if change_ratio > 0.5:
                    last_commit = subprocess.run(
                        ["git", "log", "-1", "--format=%ct"],
                        cwd=self.wiki_path,
                        capture_output=True,
                        text=True,
                    )
                    last_ts = int(last_commit.stdout.strip() or 0)
                    age_seconds = datetime.now(timezone.utc).timestamp() - last_ts
                    if age_seconds < 3600:
                        logger.error(
                            "Safety guard: %.0f%% of wiki files changed within 1h of last commit "
                            "— possible LLM cascade. Skipping commit. Review manually.",
                            change_ratio * 100,
                        )
                        subprocess.run(
                            ["git", "reset", "HEAD"],
                            cwd=self.wiki_path,
                            capture_output=True,
                        )
                        return False

            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.wiki_path,
                check=True,
                capture_output=True,
            )
            logger.info("Committed: %s", message)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("Git operation failed: %s", e.stderr)
            return False

    def _read_wiki_file(self, filename: str) -> str:
        path = self.wiki_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _append_tasks(self, tasks: list[TaskEntry]) -> None:
        tasks_file = self.wiki_dir / "tasks.md"
        content = tasks_file.read_text(encoding="utf-8") if tasks_file.exists() else "# Active Tasks\n\n"

        new_lines = []
        for task in tasks:
            line = f"- [ ] [{task.priority}] {task.text}"
            if task.due_date:
                line += f" (Due: {task.due_date})"
            if task.source_group:
                line += f" — {task.source_group}"
            new_lines.append(line)

        content = content.rstrip() + "\n" + "\n".join(new_lines) + "\n"
        tasks_file.write_text(content, encoding="utf-8")

    def _remove_tasks(self, patterns: list[str]) -> None:
        tasks_file = self.wiki_dir / "tasks.md"
        if not tasks_file.exists():
            return

        lines = tasks_file.read_text(encoding="utf-8").splitlines()
        filtered = []
        for line in lines:
            if any(pattern.lower() in line.lower() for pattern in patterns):
                filtered.append(line.replace("- [ ]", "- [x]"))
            else:
                filtered.append(line)

        tasks_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")

    def _write_concept_page(self, page: ConceptPage) -> None:
        filepath = self.wiki_dir / page.filename
        if page.action == "create" or not filepath.exists():
            filepath.write_text(page.content + "\n", encoding="utf-8")
        elif page.action == "append":
            existing = filepath.read_text(encoding="utf-8")
            filepath.write_text(existing.rstrip() + "\n\n" + page.content + "\n", encoding="utf-8")
        elif page.action == "update":
            filepath.write_text(page.content + "\n", encoding="utf-8")

    def _update_index(self, additions: list[str]) -> None:
        index_file = self.wiki_dir / "index.md"
        content = index_file.read_text(encoding="utf-8") if index_file.exists() else "# Knowledge Index\n\n## Pages\n\n"

        existing_lower = content.lower()
        new_entries = [a for a in additions if a.lower() not in existing_lower]

        if new_entries:
            content = content.rstrip() + "\n" + "\n".join(new_entries) + "\n"
            index_file.write_text(content, encoding="utf-8")

    def _append_log(self, entry: LogEntry) -> None:
        log_file = self.wiki_dir / "log.md"
        content = log_file.read_text(encoding="utf-8") if log_file.exists() else "# Update Log\n\n"

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        pages = ", ".join(entry.pages_affected) if entry.pages_affected else "none"
        log_line = f"- **{timestamp}**: {entry.summary} (pages: {pages})"

        content = content.rstrip() + "\n" + log_line + "\n"
        log_file.write_text(content, encoding="utf-8")

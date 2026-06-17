"""Memory Linter: validates compiled/ wiki for broken links and orphaned pages.

Runs as part of the compile pipeline. Exits non-zero on errors (blocks git commit).

Checks:
1. Broken markdown links: [text](file.md) → file must exist in compiled/
2. Orphaned pages: exists in compiled/ but not reachable from index.md or any other page
3. Conflict status pages: reports pages with status: conflict for human review
"""

import logging
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Matches [text](relative_link.md) — ignores http:// and anchors
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!https?://)([^)#]+\.md)[^)]*\)")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class LintError:
    def __init__(self, file: str, line: int, kind: str, detail: str):
        self.file = file
        self.line = line
        self.kind = kind
        self.detail = detail

    def __str__(self):
        return f"{self.file}:{self.line}: [{self.kind}] {self.detail}"


class MemoryLinter:
    """Validates the compiled/ wiki directory."""

    def __init__(self, brain_path: str = "~/sammurai-brain"):
        self.compiled_dir = Path(brain_path).expanduser() / "compiled"

    def run(self, raise_on_error: bool = True) -> list[LintError]:
        """Run all lint checks. Returns list of errors; raises SystemExit if any found."""
        if not self.compiled_dir.exists():
            logger.info("compiled/ dir does not exist — skipping lint")
            return []

        file_contents: dict[str, str] = {}
        for md_file in self.compiled_dir.glob("*.md"):
            try:
                file_contents[md_file.name] = md_file.read_text(encoding="utf-8")
            except OSError:
                pass

        errors: list[LintError] = []
        errors.extend(self._check_broken_links(file_contents))
        errors.extend(self._check_orphaned_pages(file_contents))
        conflicts = self._report_conflicts(file_contents)

        if conflicts:
            for c in conflicts:
                logger.warning("CONFLICT: %s", c)

        if errors:
            for e in errors:
                logger.error("LINT: %s", e)
            if raise_on_error:
                logger.error(
                    "Linter found %d error(s) — fix before committing.", len(errors)
                )
                sys.exit(1)

        return errors

    def _check_broken_links(self, file_contents: dict[str, str]) -> list[LintError]:
        """Find [text](file.md) links pointing to non-existent files."""
        errors = []
        for name, content in file_contents.items():
            for lineno, line in enumerate(content.splitlines(), start=1):
                for match in MD_LINK_RE.finditer(line):
                    target = match.group(2).strip()
                    if target not in file_contents:
                        errors.append(
                            LintError(
                                file=name,
                                line=lineno,
                                kind="BROKEN_LINK",
                                detail=f"Link target not found: {target}",
                            )
                        )
        return errors

    def _check_orphaned_pages(self, file_contents: dict[str, str]) -> list[LintError]:
        """Find pages not reachable from any other compiled/ page."""
        all_pages = set(file_contents.keys())
        if "index.md" not in all_pages:
            return []

        reachable: set[str] = set()
        stack = ["index.md"]

        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)

            for match in MD_LINK_RE.finditer(file_contents.get(current, "")):
                target = match.group(2).strip()
                if target in all_pages and target not in reachable:
                    stack.append(target)

        # Exempt: tasks.md and log.md are implicitly linked (core files)
        exempt = {"tasks.md", "log.md", "index.md"}
        orphans = all_pages - reachable - exempt

        return [
            LintError(
                file=page,
                line=0,
                kind="ORPHANED_PAGE",
                detail=f"{page} exists but is not linked from any other compiled/ page",
            )
            for page in sorted(orphans)
        ]

    def _report_conflicts(self, file_contents: dict[str, str]) -> list[str]:
        """Return list of pages with status: conflict (for warning, not error)."""
        conflicts = []
        for name, content in file_contents.items():
            match = FRONTMATTER_RE.match(content)
            if match:
                try:
                    fm = yaml.safe_load(match.group(1)) or {}
                    if fm.get("status") == "conflict":
                        since = fm.get("conflict_since", "unknown")
                        conflicts.append(
                            f"{name} (conflict since {since[:10]})"
                        )
                except yaml.YAMLError:
                    pass
        return conflicts

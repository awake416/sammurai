"""sammurai init-brain: scaffold ~/sammurai-brain/ repo."""

import logging
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SCHEMA_MD = """\
# SCHEMA.md — Sammurai Brain Constitution

This file governs how the wiki is structured. The WikiCompiler reads it before
every compilation run.

## Directory Structure

- `wiki/index.md` — master index of all pages (auto-maintained)
- `wiki/tasks.md` — active tasks, append-only, format: `- [ ] [Priority] text`
- `wiki/log.md`   — append-only update log
- `wiki/*.md`     — concept pages, one topic per file, snake_case filenames
- `raw/`          — daily digest dumps (never edit manually)

## Rules

1. Never overwrite existing content — always append or update specific sections.
2. Tasks removed from tasks.md must be marked `- [x]` not deleted.
3. Concept pages must have a single `# Title` heading.
4. New concept pages must be linked in index.md.
5. Log entry required after every compilation run.

## Task Format

```
- [ ] [High|Medium|Low] Task description (Due: YYYY-MM-DD) — source group
```

## Concept Page Format

```markdown
# Topic Name

Brief description of what this page covers.

## Section

Content...
```
"""

INDEX_MD = """\
# Knowledge Index

## Pages

"""

TASKS_MD = """\
# Active Tasks

"""

LOG_MD = """\
# Update Log

"""


def init_brain(brain_path: str, force: bool = False) -> None:
    """Scaffold the brain repo at brain_path."""
    root = Path(brain_path).expanduser()

    if root.exists() and any(root.iterdir()) and not force:
        print(f"Directory {root} already exists and is not empty. Use --force to overwrite.")
        sys.exit(1)

    # Create directories
    wiki_dir = root / "wiki"
    raw_dir = root / "raw"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Write files (skip if already exist unless force)
    def write(path: Path, content: str) -> None:
        if not path.exists() or force:
            path.write_text(content, encoding="utf-8")
            print(f"  created {path.relative_to(root)}")
        else:
            print(f"  skipped {path.relative_to(root)} (already exists)")

    write(root / "SCHEMA.md", SCHEMA_MD)
    write(wiki_dir / "index.md", INDEX_MD)
    write(wiki_dir / "tasks.md", TASKS_MD)
    write(wiki_dir / "log.md", LOG_MD)

    # Init git repo
    git_dir = root / ".git"
    if not git_dir.exists():
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "chore: init sammurai brain repo"],
            cwd=str(root),
            check=True,
            capture_output=True,
        )
        print(f"  git init {root}")
    else:
        print(f"  skipped git init (already a repo)")

    print(f"\nBrain repo ready at: {root}")
    print("Next: set wiki.path in config.yaml to this path.")


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="Scaffold a sammurai brain repo")
    parser.add_argument(
        "path",
        nargs="?",
        default="~/sammurai-brain",
        help="Path for the brain repo (default: ~/sammurai-brain)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()

    init_brain(args.path, force=args.force)


if __name__ == "__main__":
    main()

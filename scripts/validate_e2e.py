#!/usr/bin/env python3
"""End-to-end validation: digest → wiki → cognee → query.

Run from project root:
    python scripts/validate_e2e.py

Exits 0 on success, 1 on failure.
"""

import os
import sys
import tempfile
import pathlib
import subprocess
import logging

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

SAMPLE_DIGEST = """\
# Daily Digest — 2026-05-21

## Group: Family

- Pay school fees of 15000 INR by Friday via NEFT to ABC School account
- Football class confirmed for Saturday 10 AM at Green Park, Coach Ravi attending
- Dentist appointment needed for next week for Priya

## Group: Work

- Send Q2 report to Rohan by EOD Thursday
- Team standup moved to 10:30 AM from next Monday
"""

TEST_QUERIES = [
    ("when is football class?", ["saturday", "10 am", "10am", "green park"]),
    ("what tasks are pending?", ["school fees", "football", "dentist", "report"]),
    ("how much are school fees?", ["15000", "inr", "neft"]),
]


def run(label: str, fn):
    print(f"  {label}...", end=" ", flush=True)
    try:
        result = fn()
        print("OK" + (f" ({result})" if result else ""))
        return result
    except Exception as e:
        print(f"FAIL: {e}")
        return None


def main():
    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        brain_root = pathlib.Path(tmpdir) / "test_brain"

        # Step 1: init-brain
        result = run("init-brain", lambda: subprocess.run(
            [sys.executable, "-m", "src.backend.brain_init", str(brain_root)],
            capture_output=True, check=True,
        ))
        if not result:
            failures.append("init-brain failed")

        # Step 2: write sample digest
        raw_dir = brain_root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        digest_file = raw_dir / "digest_2026-05-21.txt"
        digest_file.write_text(SAMPLE_DIGEST, encoding="utf-8")
        print("  wrote sample digest... OK")

        # Step 3: wiki compilation
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
        from src.backend.llm_client import LLMClient
        from src.backend.wiki_compiler import WikiCompiler

        llm = LLMClient(model="gemini-2.5-flash")
        compiler = WikiCompiler(llm_client=llm, wiki_path=str(brain_root))
        compiler.ensure_structure()

        update = run("wiki compilation", lambda: compiler.compile_digest(str(digest_file)))
        if not update:
            failures.append("wiki compilation returned None")
        elif not update.has_changes():
            failures.append("wiki compilation produced no changes")
        else:
            compiler.apply_update(update)
            tasks_file = brain_root / "wiki" / "tasks.md"
            if tasks_file.exists():
                content = tasks_file.read_text()
                run("tasks.md non-empty", lambda: len(content) > 50 or None)
                if len(content) < 50:
                    failures.append("tasks.md looks empty after compilation")
            print(f"    tasks_to_add: {len(update.tasks_to_add)}, concept_pages: {len(update.concept_pages)}")

        # Step 4: cognee ingest
        from src.backend.cognee_store import CogneeStore
        store = CogneeStore(wiki_path=str(brain_root), dataset_name="e2e_test")
        count = run("cognee ingest", lambda: store.rebuild_index())
        if not count:
            failures.append("cognee ingest returned 0 files")

        # Step 5: RAG queries
        from src.backend.hermes_agent import HermesAgent
        agent = HermesAgent(llm_client=llm, cognee_store=store, wiki_path=str(brain_root))

        for question, expected_keywords in TEST_QUERIES:
            answer = run(f"query: {question[:40]}", lambda q=question: agent.answer(q))
            if answer:
                answer_lower = answer.lower()
                matched = [kw for kw in expected_keywords if kw in answer_lower]
                if not matched:
                    failures.append(f"Query '{question}' returned unexpected answer: {answer!r}")
                    print(f"    expected one of: {expected_keywords}")
                    print(f"    got: {answer!r}")

    print()
    if failures:
        print(f"FAILED ({len(failures)} failures):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()

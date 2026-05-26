#!$HOME/.venv/bin/python3
"""Query sammurai cognee store from Hermes.

Usage:
    python3 query_brain.py "What are my pending tasks?"
    Or use sammurai venv: ~/.venv/bin/python3 query_brain.py "..."
"""

import json
import sys
from pathlib import Path

# Add sammurai to path
SAMMURAI_ROOT = Path.home() / "ai" / "sammurai"
sys.path.insert(0, str(SAMMURAI_ROOT / "src"))

try:
    import yaml
    from backend.cognee_store import CogneeStore
except ImportError as e:
    print(json.dumps({"error": f"Import failed: {e}. Is sammurai installed?"}))
    sys.exit(1)


def load_config():
    """Load sammurai config.yaml."""
    config_path = SAMMURAI_ROOT / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f)


def query_brain(question: str, context_limit: int = 3000) -> dict:
    """Query cognee store and return context + sources.

    Returns:
        {"context": str, "sources": list[str]}
    """
    if not question.strip():
        return {"context": "", "sources": []}

    try:
        config = load_config()
        wiki_path = Path(config.get("wiki", {}).get("path", "~/sammurai-brain")).expanduser()

        store = CogneeStore(wiki_path=str(wiki_path), config=config)
        context = store.get_relevant_context(question, context_limit=context_limit)

        if not context:
            return {"context": "", "sources": []}

        # Extract source files mentioned in context (basic heuristic)
        sources = []
        wiki_files = list((wiki_path / "wiki").glob("*.md"))
        for wf in wiki_files:
            if wf.name in context or wf.stem in context:
                sources.append(wf.name)

        return {
            "context": context,
            "sources": sources if sources else ["wiki"]
        }

    except Exception as e:
        return {
            "error": str(e),
            "context": "",
            "sources": []
        }


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: query_brain.py '<question>'"}))
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    result = query_brain(question)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

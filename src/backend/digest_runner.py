"""Daily digest runner — cron/systemd entry point.

Generates digest from WhatsApp messages, saves to raw/, compiles wiki, rebuilds index.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.backend.database import WhatsAppDB
from src.backend.rich_document_parser import RichDocumentParser as DocumentParser
from src.backend.llm_client import LLMClient
from src.backend.topic_extractor import TopicExtractor
from src.backend.cognee_store import CogneeStore
from src.backend.wiki_compiler import WikiCompiler
from src.backend.cli import extract_from_group, process_groups_parallel

logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def run_daily_digest(config: dict) -> None:
    """Generate daily digest, save to raw, compile wiki, rebuild vector index."""
    wiki_config = config.get("wiki", {})
    wiki_path = Path(wiki_config.get("path", "~/sammurai-brain")).expanduser()
    raw_path = Path(wiki_config.get("raw_path", str(wiki_path / "raw"))).expanduser()
    schema_path = wiki_config.get("schema", "SCHEMA.md")
    cron_config = config.get("cron", {})
    days = cron_config.get("days", 1)

    llm_config = config.get("llm", {})
    llm_client = LLMClient(
        model=llm_config.get("model", "claude-sonnet-4.6"),
        confidence_threshold=llm_config.get("confidence_threshold", 0.75),
    )

    db_path = config.get("database", {}).get("path", "~/.wacli/wacli.db")
    db = WhatsAppDB(str(Path(db_path).expanduser()))

    document_parser = DocumentParser()
    topic_extractor = TopicExtractor(llm_client, document_parser=document_parser)

    # Determine groups to process
    parallel_config = config.get("parallel", {})
    groups = cron_config.get("groups") or parallel_config.get("groups", [])

    # Normalize JIDs
    normalized_groups = [
        f"{g}@g.us" if "@" not in str(g) and ("-" in str(g) or str(g).isdigit()) else str(g)
        for g in groups
    ]
    unique_groups = list(dict.fromkeys(normalized_groups))

    if not unique_groups:
        logger.error("No groups configured for daily digest")
        return

    workers = parallel_config.get("workers", 5)
    batch_size = llm_config.get("batch_size", 50)
    batch_workers = parallel_config.get("batch_workers", 3)

    logger.info(f"Generating daily digest for {len(unique_groups)} groups (last {days} days)")

    # Generate digest using existing pipeline
    result = process_groups_parallel(
        db=db,
        groups=unique_groups,
        config=config,
        days=days,
        use_llm=True,
        llm_client=llm_client,
        batch_size=batch_size,
        workers=workers,
        parallel_batches=batch_workers,
        topic_extractor=topic_extractor,
        digest=True,
        document_parser=document_parser,
    )

    db.close()

    if not result:
        logger.warning("No digest content generated")
        return

    # Save raw digest
    raw_path.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest_file = raw_path / f"digest_{date_str}.txt"
    digest_file.write_text(result, encoding="utf-8")
    logger.info(f"Saved raw digest: {digest_file}")

    # Compile wiki
    compiler = WikiCompiler(
        llm_client=llm_client,
        wiki_path=str(wiki_path),
        schema_path=schema_path,
    )
    compiler.ensure_structure()

    update = compiler.compile_digest(str(digest_file))
    if update and update.has_changes():
        compiler.apply_update(update)
        compiler.git_commit(f"Auto-update: {date_str}")
        logger.info("Wiki compiled and committed")

        # Rebuild cognee index
        store = CogneeStore(wiki_path=str(wiki_path), config=config)
        count = store.rebuild_index()
        logger.info(f"Cognee index rebuilt: {count} files")
    else:
        logger.info("No wiki changes from digest")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        config = load_config()
        run_daily_digest(config)
    except Exception as e:
        logger.error(f"Digest runner failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

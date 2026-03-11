#!/usr/bin/env python3
"""
WhatsApp Action Item Extractor

Usage:
    python -m src.backend.cli                    # List all groups
    python -m src.backend.cli "Group Name"       # Extract action items from specific group
    python -m src.backend.cli --all              # Extract from all groups
"""

import argparse
import logging
import sys
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

# Configure logging
logger = logging.getLogger(__name__)


def setup_logging(debug: bool = False) -> None:
    """Configure logging level and format."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        force=True,
    )
    logger.setLevel(level)


sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backend.database import WhatsAppDB
from src.backend.parser import parse_messages
from src.backend.models import ActionableItem, Message, Priority, DocumentSummary
from src.backend.llm_client import LLMClient, LLMError
from src.backend.topic_extractor import TopicExtractor
from src.backend.document_parser import DocumentParser


def format_timestamp(ts: Optional[str]) -> str:
    """Convert timestamp to human-readable format."""
    if not ts:
        return "N/A"
    try:
        # Handle both seconds and milliseconds
        ts_int = int(ts)
        if ts_int > 10**10:
            ts_int = ts_int / 1000
        return datetime.fromtimestamp(ts_int, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (ValueError, OSError):
        return ts


def get_date_range(messages: List[dict]) -> str:
    """Calculate date range from a list of messages."""
    if not messages:
        return ""

    timestamps = []
    for msg in messages:
        ts = msg.get("timestamp")
        if ts:
            try:
                timestamps.append(int(ts))
            except (ValueError, TypeError):
                logger.debug(f"Skipping message with unparseable timestamp: {ts!r}")
                continue

    if not timestamps:
        return ""

    min_ts = min(timestamps)
    max_ts = max(timestamps)

    def to_date_str(ts_val):
        if ts_val > 10**10:
            ts_val = ts_val / 1000
        return datetime.fromtimestamp(ts_val, tz=timezone.utc).strftime("%Y-%m-%d")

    start_date = to_date_str(min_ts)
    end_date = to_date_str(max_ts)

    if start_date == end_date:
        return start_date
    return f"{start_date} to {end_date}"


def sanitize_tsv_field(value: Optional[str], max_length: Optional[int] = 100) -> str:
    """Sanitize a field for TSV output.

    - Replaces tabs, newlines, and carriage returns with spaces
    - Truncates to max_length (default 100) if provided
    - Returns "-" for empty/None values
    """
    if value is None or str(value).strip() == "":
        return "-"

    # Convert to string and replace whitespace
    sanitized = str(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")

    if max_length is not None:
        return sanitized[:max_length]
    return sanitized


def format_item_tsv(
    item: ActionableItem, include_group: bool = False, compact: bool = True
) -> str:
    """Format an ActionableItem as a TSV row."""
    msg_ref = f"#{item.message_ref}" if item.message_ref is not None else "-"
    priority = item.priority.value

    task_text = item.task
    if item.topic_tags:
        tags_str = ", ".join(item.topic_tags)
        task_text = f"[Tags: {tags_str}] {task_text}"
    task = sanitize_tsv_field(task_text, max_length=None)
    deadline = sanitize_tsv_field(item.deadline)

    if compact:
        return "\t".join([msg_ref, priority, task, deadline])

    # Full format
    category = item.category.value
    context = sanitize_tsv_field(item.context, max_length=None)
    assignee = sanitize_tsv_field(item.assignee)
    sender = sanitize_tsv_field(item.sender)
    date = format_timestamp(item.timestamp)

    resources_list = [r.value for r in item.resources] if item.resources else []
    resources = ", ".join(resources_list) if resources_list else "-"
    resources = sanitize_tsv_field(resources)

    fields = [
        msg_ref,
        priority,
        category,
        task,
        context,
        assignee,
        deadline,
        sender,
        resources,
    ]

    if include_group:
        group = sanitize_tsv_field(item.group_name)
        fields.append(group)

    fields.append(date)
    return "\t".join(fields)


def display_action_items(
    action_items: list[ActionableItem],
    title: str,
    include_group: bool = False,
    full: bool = False,
) -> str:
    """Format action items as a TSV table string, sorted by priority.

    Args:
        action_items: List of ActionableItem objects
        title: Title for the output section
        include_group: Whether to include the group name column
        full: Whether to show all columns (default: False/compact)

    Returns:
        Formatted TSV table as a string
    """
    if not action_items:
        return f"No action items found {title}."

    # Priority mapping for sorting
    priority_order = {
        Priority.HIGH: 0,
        Priority.MEDIUM: 1,
        Priority.LOW: 2,
    }

    # Sort all items by task (alphabetically), then by priority (High > Medium > Low)
    sorted_items = sorted(
        action_items, key=lambda x: (x.task.lower(), priority_order.get(x.priority, 99))
    )

    output = [f"Found {len(action_items)} tasks {title}:"]
    output.append("=" * 80)

    # TSV header
    if full:
        header_fields = [
            "REF",
            "PRIORITY",
            "CATEGORY",
            "TASK",
            "CONTEXT",
            "ASSIGNEE",
            "DEADLINE",
            "SENDER",
            "RESOURCES",
        ]
        if include_group:
            header_fields.append("GROUP")
        header_fields.append("DATE")
    else:
        header_fields = ["REF", "PRIORITY", "TASK", "DEADLINE"]

    output.append("\t".join(header_fields))

    for item in sorted_items:
        output.append(
            format_item_tsv(item, include_group=include_group, compact=not full)
        )

    output.append("=" * 80)

    return "\n".join(output)


def process_action_items(action_items: list[dict]) -> list[ActionableItem]:
    """Convert action item dictionaries to ActionableItem objects with normalization."""
    items = []
    for item in action_items:
        try:
            # Normalize priority to match Enum (High, Medium, Low)
            if "priority" in item and isinstance(item["priority"], str):
                item["priority"] = item["priority"].capitalize()
            # Normalize category to match Enum
            if "category" in item and isinstance(item["category"], str):
                item["category"] = item["category"].capitalize()
            items.append(ActionableItem(**item))
        except Exception as e:
            logger.warning(f"Skipping invalid action item: {e}")
    return items


def get_document_summaries(
    messages: List[Message], topic_extractor: TopicExtractor
) -> List[DocumentSummary]:
    """Extract document summaries from messages."""
    doc_summaries = []
    for msg in messages:
        if (
            msg.media_type == "document"
            and msg.local_path
            and msg.local_path.lower().endswith(".pdf")
        ):
            logger.info(f"Summarizing document: {msg.filename or msg.local_path}")
            summary = topic_extractor.summarize_document(
                url=msg.filename or "local_file", file_path=msg.local_path
            )
            doc_summaries.append(summary)
    return doc_summaries


def enrich_messages_with_docs(
    messages: List[dict], document_parser: DocumentParser
) -> List[dict]:
    """Enrich messages with extracted document content for LLM extraction."""
    logger.debug(f"enrich_messages_with_docs called with {len(messages)} messages")
    for msg in messages:
        media_type = msg.get("media_type")
        local_path = msg.get("local_path")

        if (
            media_type == "document"
            and local_path
            and local_path.lower().endswith(".pdf")
        ):
            logger.debug(f"Found PDF document for enrichment: {local_path}")
            try:
                content = document_parser.extract_text(local_path)
                if content:
                    original_message = msg.get("message") or ""
                    msg["message"] = (
                        f"{original_message}\n[Extracted Document Content]: {content}"
                    )
                    logger.debug(f"Enriched message with content from {local_path}")
                else:
                    logger.debug(f"No content extracted from {local_path}")
            except Exception as e:
                logger.warning(
                    f"Failed to extract text from {local_path} for enrichment: {e}"
                )
    return messages


def validate_db_path(db_path: str) -> Path:
    """Validate and resolve the database path.

    Args:
        db_path: User-provided database path (may contain ~)

    Returns:
        Resolved absolute Path to the database file

    Raises:
        ValueError: If path is unsafe or invalid
    """
    # Early exit: empty path
    if not db_path:
        raise ValueError("Database path cannot be empty")

    # Expand user home directory
    path = Path(db_path).expanduser()

    # Resolve to absolute path to prevent traversal
    try:
        path = path.resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid database path: {e}")

    # Ensure parent directory exists (will create if needed for new databases)
    parent = path.parent
    if not parent.exists():
        raise ValueError(f"Parent directory does not exist: {parent}")

    # Check for path traversal patterns (basic check - resolved path should not differ wildly)
    # After resolve(), we have absolute path - just validate it's within expected bounds
    if ".." in Path(db_path).parts:
        raise ValueError("Path traversal not allowed")

    # Validate it's a valid SQLite file extension
    if path.suffix not in ("", ".db", ".sqlite", ".sqlite3"):
        raise ValueError(
            f"Invalid database file extension: {path.suffix}. Use .db, .sqlite, or .sqlite3"
        )

    return path


def load_config() -> dict:
    # Look in project root (2 levels up from cli.py: backend -> src -> root)
    config_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {"parser": {"use_llm": False, "fallback_to_rule_based": True}, "llm": {}}


def list_groups(db: WhatsAppDB, days_active: Optional[int] = None) -> None:
    """List all WhatsApp groups."""
    groups = db.get_groups(days_active=days_active)
    if not groups:
        filter_msg = f" in the last {days_active} days" if days_active else ""
        logger.info(
            f"No groups found{filter_msg}. Make sure wacli has synced messages."
        )
        return

    logger.info(f"Found {len(groups)} groups:\n")
    for i, group in enumerate(groups, 1):
        activity = ""
        if group.get("last_activity"):
            # Convert epoch timestamp to human-readable format
            try:
                ts = int(group["last_activity"])
                # Handle milliseconds (if > 10 billion) vs seconds
                if ts > 10**10:
                    ts = ts / 1000
                readable_time = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                activity = f" ({readable_time})"
            except (ValueError, OSError):
                activity = f" ({group['last_activity']})"

        display_name = (
            group["name"]
            if group["name"] and group["name"].strip()
            else f"[Unnamed Group: {group['jid']}]"
        )
        logger.info(f"  {i}. {display_name} [{group['jid']}]{activity}")


def extract_from_group(
    db: WhatsAppDB,
    group: str,
    config: dict,
    limit: int = 100,
    days: Optional[int] = None,
    use_llm: bool = False,
    no_llm: bool = False,
    llm_client: Optional[LLMClient] = None,
    batch_size: int = 50,
    parallel_batches: int = 1,
    topic_extractor: Optional[TopicExtractor] = None,
    digest: bool = False,
    topics_only: bool = False,
    document_parser: Optional[DocumentParser] = None,
    full: bool = False,
) -> str:
    """Extract action items from a specific group.

    Args:
        db: WhatsAppDB instance
        group: Either a group name or a group JID (if contains '@g.us')
        config: Configuration dictionary
        limit: Number of messages to fetch
        days: Number of days to look back
        use_llm: Force LLM-based extraction
        no_llm: Force rule-based extraction only
        llm_client: Pre-configured LLMClient instance (optional)
        batch_size: Number of messages to process in a single LLM batch
        parallel_batches: Number of concurrent batches to process
        topic_extractor: TopicExtractor instance for digest/topics (optional)
        digest: Whether to generate a community digest
        topics_only: Whether to only show topics and skip action items
        document_parser: DocumentParser instance for enriching messages (optional)
        full: Whether to show all columns in action items table

    Returns:
        Formatted output string (digest + action items table)
    """
    # Early exit: check if input is empty
    if not group:
        logger.warning("Group name or JID is required.")
        return ""

    # Check if input is a JID (contains '@g.us')
    if "@g.us" in group:
        group_jid = group
        # Try to find group name from JID
        groups = db.get_groups()
        group_name = next((g["name"] for g in groups if g["jid"] == group_jid), None)
        if not group_name or not group_name.strip():
            group_name = f"[Unnamed Group: {group_jid}]"
    else:
        # Input is a group name - look up the JID
        group_jid = db.get_group_jid(group)
        group_name = db._resolve_group_name(group_jid) if group_jid else group

    # Early exit: group not found
    if not group_jid:
        logger.warning(
            f"Group '{group}' not found. Use --list to see available groups."
        )
        return ""

    logger.info(f"Extracting from: {group_name} ({group_jid})")

    messages = db.get_messages_by_group(group_jid, limit, days)
    logger.debug(f"Fetched {len(messages)} messages from group {group_jid}")

    if not messages:
        logger.info(
            f"No messages found in group '{group_name}'. Make sure wacli has synced messages."
        )
        return ""

    for msg in messages:
        msg["group_name"] = group_name

    # Enrich messages with document content if parser is available
    if document_parser:
        messages = enrich_messages_with_docs(messages, document_parser)

    # Determine which parser to use based on config and flags
    fallback_enabled = config.get("parser", {}).get("fallback_to_rule_based", True)
    llm_enabled = use_llm or (
        config.get("parser", {}).get("use_llm", False) and not no_llm
    )

    action_items = []

    if llm_enabled:
        logger.info("Using LLM-based extraction...")
        if llm_client is None:
            try:
                llm_config = config.get("llm", {})
                llm_client = LLMClient(
                    model=llm_config.get("model", "gemini/gemini-2.5-flash"),
                    confidence_threshold=llm_config.get("confidence_threshold", 0.75),
                )
            except ValueError as e:
                if use_llm:
                    logger.error(f"CRITICAL ERROR: {e}")
                    raise LLMError(str(e))
                logger.warning(f"LLM not available: {e}")
                if fallback_enabled:
                    logger.info("Falling back to rule-based parser...")
                    llm_enabled = False
                else:
                    logger.error("LLM not available and fallback is disabled.")
                    return ""

        if llm_client:
            try:
                action_items = llm_client.extract_batch(
                    messages, batch_size=batch_size, parallel_batches=parallel_batches
                )
            except Exception as e:
                if use_llm:
                    logger.error(f"CRITICAL ERROR: LLM extraction failed: {e}")
                    raise LLMError(str(e))
                logger.warning(f"LLM extraction failed: {e}")
                if fallback_enabled:
                    logger.info("Falling back to rule-based parser...")
                    llm_enabled = False
                else:
                    logger.error("LLM extraction failed and fallback is disabled.")
                    return ""

            if not action_items and fallback_enabled:
                # If LLM returned no results, fall back to rule-based
                logger.info("Falling back to rule-based parser...")
                llm_enabled = False

    if not llm_enabled:
        logger.info("Using rule-based extraction...")
        action_items = parse_messages(messages)

    # Convert to ActionableItem objects
    items = process_action_items(action_items)

    output_parts = []

    if (digest or topics_only) and topic_extractor:
        msg_objs = [Message(**m) for m in messages]
        topics = topic_extractor.extract_topics(msg_objs)
        items = topic_extractor.tag_items_with_topics(items, topics)
        date_range = get_date_range(messages)

        # Summarize documents if any
        doc_summaries = get_document_summaries(msg_objs, topic_extractor)

        digest_text = topic_extractor.generate_digest(
            items,
            topics,
            document_summaries=doc_summaries,
            group_name=group_name,
            date_range=date_range,
        )
        output_parts.append(digest_text)

    if topics_only:
        return "\n".join(output_parts)

    # Don't show separate TSV table when digest is already shown
    if digest:
        return "\n".join(output_parts)

    if not items:
        logger.info(f"No action items found in group '{group_name}'.")
        return "\n".join(output_parts)

    # Output as TSV
    output_parts.append(
        display_action_items(
            items, f"in '{group_name}'", include_group=False, full=full
        )
    )

    return "\n".join(output_parts)


def extract_from_all_groups(
    db: WhatsAppDB,
    config: dict,
    limit: int = 100,
    days: Optional[int] = None,
    use_llm: bool = False,
    no_llm: bool = False,
    llm_client: Optional[LLMClient] = None,
    batch_size: int = 50,
    parallel_batches: int = 1,
    topic_extractor: Optional[TopicExtractor] = None,
    digest: bool = False,
    topics_only: bool = False,
    document_parser: Optional[DocumentParser] = None,
    full: bool = False,
) -> str:
    """Extract action items from all groups.

    Args:
        db: WhatsAppDB instance
        config: Configuration dictionary
        limit: Number of messages to fetch per group
        days: Number of days to look back
        use_llm: Force LLM-based extraction
        no_llm: Force rule-based extraction only
        llm_client: Pre-configured LLMClient instance (optional)
        batch_size: Number of messages to process in a single LLM batch
        parallel_batches: Number of concurrent batches to process
        topic_extractor: TopicExtractor instance for digest/topics (optional)
        digest: Whether to generate a community digest
        topics_only: Whether to only show topics and skip action items
        document_parser: DocumentParser instance for enriching messages (optional)
        full: Whether to show all columns in action items table

    Returns:
        Formatted output string
    """
    messages = db.get_recent_messages(limit * 10, days)
    logger.debug(f"Fetched {len(messages)} recent messages across all groups")

    if not messages:
        logger.info("No messages found. Make sure wacli has synced messages.")
        return ""

    # Enrich messages with document content if parser is available
    if document_parser:
        messages = enrich_messages_with_docs(messages, document_parser)

    # Determine which parser to use based on config and flags
    fallback_enabled = config.get("parser", {}).get("fallback_to_rule_based", True)
    llm_enabled = use_llm or (
        config.get("parser", {}).get("use_llm", False) and not no_llm
    )

    action_items = []

    if llm_enabled:
        logger.info("Using LLM-based extraction...")
        if llm_client is None:
            try:
                llm_config = config.get("llm", {})
                llm_client = LLMClient(
                    model=llm_config.get("model", "gemini/gemini-2.5-flash"),
                    confidence_threshold=llm_config.get("confidence_threshold", 0.75),
                )
            except ValueError as e:
                if use_llm:
                    logger.error(f"CRITICAL ERROR: {e}")
                    raise LLMError(str(e))
                logger.warning(f"LLM not available: {e}")
                if fallback_enabled:
                    logger.info("Falling back to rule-based parser...")
                    llm_enabled = False
                else:
                    logger.error("LLM not available and fallback is disabled.")
                    return ""

        if llm_client:
            try:
                action_items = llm_client.extract_batch(
                    messages, batch_size=batch_size, parallel_batches=parallel_batches
                )
            except Exception as e:
                if use_llm:
                    logger.error(f"CRITICAL ERROR: LLM extraction failed: {e}")
                    raise LLMError(str(e))
                logger.warning(f"LLM extraction failed: {e}")
                if fallback_enabled:
                    logger.info("Falling back to rule-based parser...")
                    llm_enabled = False
                else:
                    logger.error("LLM extraction failed and fallback is disabled.")
                    return ""

            if not action_items and fallback_enabled:
                logger.info("Falling back to rule-based parser...")
                llm_enabled = False

    if not llm_enabled:
        logger.info("Using rule-based extraction...")
        action_items = parse_messages(messages)

    # Convert to ActionableItem objects
    items = process_action_items(action_items)

    output_parts = []

    if (digest or topics_only) and topic_extractor:
        msg_objs = [Message(**m) for m in messages]
        topics = topic_extractor.extract_topics(msg_objs)
        items = topic_extractor.tag_items_with_topics(items, topics)
        date_range = get_date_range(messages)

        # Summarize documents if any
        doc_summaries = get_document_summaries(msg_objs, topic_extractor)

        digest_text = topic_extractor.generate_digest(
            items,
            topics,
            document_summaries=doc_summaries,
            group_name="All Groups",
            date_range=date_range,
        )
        output_parts.append(digest_text)

    if topics_only:
        return "\n".join(output_parts)

    # Don't show separate TSV table when digest is already shown
    if digest:
        return "\n".join(output_parts)

    if not items:
        logger.info("No action items found in any group.")
        return "\n".join(output_parts)

    # Output as TSV
    output_parts.append(
        display_action_items(items, "across all groups", include_group=True, full=full)
    )

    return "\n".join(output_parts)


def process_groups_parallel(
    db: WhatsAppDB,
    groups: List[str],
    config: dict,
    limit: int = 100,
    days: Optional[int] = None,
    use_llm: bool = False,
    no_llm: bool = False,
    llm_client: Optional[LLMClient] = None,
    batch_size: int = 50,
    workers: int = 5,
    parallel_batches: int = 1,
    topic_extractor: Optional[TopicExtractor] = None,
    digest: bool = False,
    topics_only: bool = False,
    document_parser: Optional[DocumentParser] = None,
    full: bool = False,
) -> str:
    """Process multiple groups in parallel.

    Args:
        db: WhatsAppDB instance
        groups: List of group names or JIDs
        config: Configuration dictionary
        limit: Number of messages to fetch per group
        days: Number of days to look back
        use_llm: Force LLM-based extraction
        no_llm: Force rule-based extraction only
        llm_client: Pre-configured LLMClient instance (optional)
        batch_size: Number of messages to process in a single LLM batch
        workers: Number of concurrent groups to process
        parallel_batches: Number of concurrent batches to process per group
        topic_extractor: TopicExtractor instance for digest/topics (optional)
        digest: Whether to generate a community digest
        topics_only: Whether to only show topics and skip action items
        document_parser: DocumentParser instance for enriching messages (optional)
        full: Whether to show all columns in action items table

    Returns:
        Formatted output string containing all group reports
    """
    if not groups:
        logger.info("No groups to process.")
        return ""

    logger.info(f"Processing {len(groups)} groups with {workers} workers...")

    # Use a list to store results in the same order as groups
    results: List[Optional[str]] = [None] * len(groups)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Map each group to its index to preserve order
        futures = {
            executor.submit(
                extract_from_group,
                db=db,
                group=group,
                config=config,
                limit=limit,
                days=days,
                use_llm=use_llm,
                no_llm=no_llm,
                llm_client=llm_client,
                batch_size=batch_size,
                parallel_batches=parallel_batches,
                topic_extractor=topic_extractor,
                digest=digest,
                topics_only=topics_only,
                document_parser=document_parser,
                full=full,
            ): i
            for i, group in enumerate(groups)
        }

        for future in as_completed(futures):
            idx = futures[future]
            group = groups[idx]
            try:
                result = future.result()
                if result:
                    results[idx] = result
            except LLMError:
                # Re-raise LLMError to trigger fail-fast in main
                raise
            except Exception as e:
                logger.error(f"Error processing group '{group}': {e}")

    # Filter out None results and join
    valid_results = [res for res in results if res is not None]
    if not valid_results:
        return ""

    output = ["\n" + "=" * 80, "FINAL REPORTS", "=" * 80 + "\n"]
    for i, res in enumerate(valid_results):
        output.append(res)
        if i < len(valid_results) - 1:
            output.append("-" * 40)

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Extract action items from WhatsApp messages via wacli"
    )
    parser.add_argument(
        "group_name", nargs="?", help="Group name or JID to extract action items from"
    )
    parser.add_argument(
        "--list", "-l", action="store_true", help="List all available groups"
    )
    parser.add_argument(
        "--days-active",
        "-d",
        type=int,
        help="Filter groups active in the last N days (used with --list)",
    )
    parser.add_argument(
        "--all", "-a", action="store_true", help="Extract from all groups"
    )
    parser.add_argument(
        "--groups",
        "-g",
        help="Comma-separated list of group names or JIDs to process",
    )
    parser.add_argument(
        "--parallel",
        "-p",
        type=int,
        help="Number of concurrent groups to process",
    )
    parser.add_argument(
        "--parallel-batches",
        type=int,
        help="Number of concurrent batches to process per group",
    )
    msg_group = parser.add_mutually_exclusive_group()
    msg_group.add_argument(
        "--limit",
        "-n",
        type=int,
        default=100,
        help="Number of messages to fetch per group (default: 100)",
    )
    msg_group.add_argument(
        "--days",
        type=int,
        help="Number of days to look back for messages (mutually exclusive with --limit)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of messages to process in a single LLM batch (default: 50)",
    )
    parser.add_argument(
        "--db-path", default="~/.wacli/wacli.db", help="Path to wacli database"
    )
    parser.add_argument(
        "--use-llm", action="store_true", help="Force LLM-based extraction"
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="Force rule-based extraction only"
    )
    parser.add_argument(
        "--digest", action="store_true", help="Output both Digest and Tagged items"
    )
    parser.add_argument(
        "--topics-only",
        action="store_true",
        help="Only show topic summary, skip action items",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Show all columns in action items table (default: compact)",
    )
    parser.add_argument(
        "--debug", "--verbose", "-v", action="store_true", help="Enable debug logging"
    )

    args = parser.parse_args()

    # Setup logging based on debug flag
    setup_logging(args.debug)
    logger.debug(f"Starting CLI with arguments: {args}")

    # Validate db-path
    try:
        validated_db_path = validate_db_path(args.db_path)
        logger.debug(f"Database path validated: {validated_db_path}")
    except ValueError as e:
        logger.error(f"Invalid --db-path: {e}")
        sys.exit(1)

    # Validate days-active if provided
    if args.days_active is not None and args.days_active < 0:
        logger.error("--days-active must be a non-negative integer")
        sys.exit(1)

    # Validate batch-size
    if args.batch_size <= 0:
        logger.error("--batch-size must be a positive integer")
        sys.exit(1)

    # Validate parallel arguments
    if args.parallel is not None and args.parallel < 1:
        logger.error("--parallel must be at least 1")
        sys.exit(1)

    if args.parallel_batches is not None and args.parallel_batches < 1:
        logger.error("--parallel-batches must be at least 1")
        sys.exit(1)

    config = load_config()
    # Get parallel config
    parallel_config = config.get("parallel", {})
    workers = args.parallel or parallel_config.get("workers", 5)
    parallel_batches = args.parallel_batches or parallel_config.get("batch_workers", 3)

    db = WhatsAppDB(str(validated_db_path))

    try:
        # Pre-create LLMClient if needed (loaded once, passed to functions)
        llm_client = None
        try:
            llm_enabled = args.use_llm or config.get("parser", {}).get("use_llm", False)
            if llm_enabled and not args.no_llm:
                llm_config = config.get("llm", {})
                try:
                    llm_client = LLMClient(
                        model=llm_config.get("model", "gemini/gemini-2.5-flash"),
                        confidence_threshold=llm_config.get(
                            "confidence_threshold", 0.75
                        ),
                    )
                except ValueError as e:
                    if args.use_llm:
                        logger.error(f"CRITICAL ERROR: {llm_config.get('model')} {e}")
                        sys.exit(1)
                    logger.warning(f"LLM not available: {e}")
                    logger.info("Will fall back to rule-based parser if needed")

            if (args.digest or args.topics_only) and not llm_client:
                logger.error(
                    "Digest and topics require LLM to be active. Use --use-llm or check your config."
                )
                sys.exit(1)

            topic_extractor = None
            document_parser = DocumentParser()
            if (args.digest or args.topics_only) and llm_client:
                topic_extractor = TopicExtractor(
                    llm_client, document_parser=document_parser
                )

            if args.list:
                list_groups(db, args.days_active)
            elif args.all:
                result = extract_from_all_groups(
                    db,
                    config,
                    args.limit,
                    args.days,
                    args.use_llm,
                    args.no_llm,
                    llm_client,
                    args.batch_size,
                    parallel_batches,
                    topic_extractor,
                    args.digest,
                    args.topics_only,
                    document_parser,
                    args.full,
                )
                if result:
                    print(result)
            elif args.groups or args.group_name:
                # Determine which groups to process
                groups_to_process = []
                if args.groups:
                    groups_to_process = [g.strip() for g in args.groups.split(",")]
                elif args.group_name:
                    groups_to_process = [args.group_name]

                if len(groups_to_process) > 1 or workers > 1:
                    result = process_groups_parallel(
                        db,
                        groups_to_process,
                        config,
                        args.limit,
                        args.days,
                        args.use_llm,
                        args.no_llm,
                        llm_client,
                        args.batch_size,
                        workers,
                        parallel_batches,
                        topic_extractor,
                        args.digest,
                        args.topics_only,
                        document_parser,
                        args.full,
                    )
                    if result:
                        print(result)
                else:
                    result = extract_from_group(
                        db,
                        groups_to_process[0],
                        config,
                        args.limit,
                        args.days,
                        args.use_llm,
                        args.no_llm,
                        llm_client,
                        args.batch_size,
                        parallel_batches,
                        topic_extractor,
                        args.digest,
                        args.topics_only,
                        document_parser,
                        args.full,
                    )
                    if result:
                        print(result)
            elif parallel_config.get("groups"):
                # Process groups from config if no specific group or --all is provided
                config_groups = parallel_config.get("groups", [])
                normalized_groups = [
                    f"{g}@g.us"
                    if "@" not in str(g) and ("-" in str(g) or str(g).isdigit())
                    else str(g)
                    for g in config_groups
                ]
                unique_groups = list(dict.fromkeys(normalized_groups))
                result = process_groups_parallel(
                    db,
                    unique_groups,
                    config,
                    args.limit,
                    args.days,
                    args.use_llm,
                    args.no_llm,
                    llm_client,
                    args.batch_size,
                    workers,
                    parallel_batches,
                    topic_extractor,
                    args.digest,
                    args.topics_only,
                    document_parser,
                    args.full,
                )
                if result:
                    print(result)
            else:
                parser.print_help()
                logger.info("\nExamples:")
                logger.info("  python -m src.backend.cli --list")
                logger.info('  python -m src.backend.cli "My Team"')
                logger.info('  python -m src.backend.cli --groups "Team A, Team B"')
                logger.info("  python -m src.backend.cli --all")
        except LLMError:
            # LLMError already logged, just exit
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

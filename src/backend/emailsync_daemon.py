"""Email sync daemon — systemd service for Gmail polling.

Incremental sync via historyId. Runs continuously with configurable poll interval.
"""

import logging
import sys
import time
from pathlib import Path

import yaml

from src.backend.email_database import EmailDB
from src.backend.gmail_client import GmailClient

logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load config from config.yaml."""
    config_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {}


def main() -> None:
    """Email sync daemon entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = load_config()
    email_config = config.get("email", {})

    if not email_config.get("enabled"):
        logger.info("Email sync disabled in config")
        sys.exit(0)

    # Initialize clients
    gmail = GmailClient(
        token_path="~/.emailsync/token.json",
        credentials_path="~/.emailsync/credentials.json",
    )

    db_path = email_config["database"]["path"]
    email_db = EmailDB(db_path=db_path)

    sync_config = email_config["sync"]
    poll_interval = sync_config.get("poll_interval", 300)
    max_results = sync_config.get("max_results_per_sync", 100)
    labels = sync_config.get("labels_to_sync", ["INBOX"])
    skip_labels = sync_config.get("skip_labels", ["SPAM"])

    logger.info(
        f"Starting email sync daemon (poll_interval={poll_interval}s, "
        f"labels={labels}, skip={skip_labels})"
    )

    while True:
        try:
            last_history_id = email_db.get_last_history_id()
            logger.debug(f"Last historyId: {last_history_id}")

            # Fetch new messages
            messages, new_history_id = gmail.fetch_messages(
                since_history_id=last_history_id,
                labels=labels,
                skip_labels=skip_labels,
                max_results=max_results,
            )

            if messages:
                logger.info(f"Fetched {len(messages)} new messages")

                # Atomic transaction: insert messages + update historyId
                conn = email_db._get_connection()
                try:
                    conn.execute("BEGIN")
                    for msg in messages:
                        email_db.insert_message(msg)
                    email_db.update_history_id(new_history_id)
                    conn.commit()
                    logger.info(f"Synced {len(messages)} messages, historyId={new_history_id}")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Sync failed, rolled back: {e}")
                    raise
            else:
                logger.debug(f"No new messages (historyId={new_history_id})")
                # Update historyId even if no messages (prevents stale historyId)
                email_db.update_history_id(new_history_id)
                email_db._get_connection().commit()

        except Exception as e:
            logger.error(f"Sync error: {e}", exc_info=True)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()

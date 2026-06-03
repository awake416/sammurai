"""Agent daemon: polls WhatsApp for queries, routes to Hermes, dispatches responses."""

import argparse
import json
import logging
import random
import subprocess
import sys
import time
from pathlib import Path

import yaml

from src.backend.cognee_store import CogneeStore
from src.backend.database import WhatsAppDB
from src.backend.hermes_agent import HermesAgent
from src.backend.intent_router import Intent, IntentRouter
from src.backend.llm_client import LLMClient
from src.backend.ollama_client import OllamaClient
from src.backend.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

STATE_FILE = Path("~/.config/sammurai/agent_state.json").expanduser()


class AgentDaemon:
    """Polling daemon that monitors a WhatsApp chat and responds to queries."""

    def __init__(self, config: dict):
        self.config = config
        agent_config = config.get("agent", {})

        self.brain_jid = agent_config.get("brain_chat_jid", "")
        if not self.brain_jid:
            raise ValueError("agent.brain_chat_jid must be configured")

        self.poll_interval = agent_config.get("poll_interval", 5)
        self.wacli_path = agent_config.get("wacli_path", "wacli")
        self.rate_limiter = RateLimiter(agent_config.get("rate_limit", 10))

        # Database
        db_path = config.get("database", {}).get("path", "~/.wacli/wacli.db")
        self.db = WhatsAppDB(str(Path(db_path).expanduser()))

        # LLM
        llm_config = config.get("llm", {})
        self.llm_client = LLMClient(
            model=llm_config.get("model", "claude-sonnet-4.6"),
            confidence_threshold=llm_config.get("confidence_threshold", 0.75),
        )

        # Cognee store
        wiki_path = Path(config.get("wiki", {}).get("path", "~/sammurai-brain")).expanduser()
        self.cognee_store = CogneeStore(
            wiki_path=str(wiki_path),
            config=config,
        )

        # Agent components
        ollama = OllamaClient()
        self.intent_router = IntentRouter(ollama_client=ollama, llm_client=self.llm_client)
        self.hermes = HermesAgent(
            llm_client=self.llm_client,
            cognee_store=self.cognee_store,
            wiki_path=str(wiki_path),
        )

        # wacli health probe state
        self.db_path = Path(db_path).expanduser()
        self._last_msg_count: int = -1
        self._flat_count_polls: int = 0

        # State
        self.last_seen_ts = self._load_state()
        self.processed_ids: set[str] = set()

    def run(self) -> None:
        """Main polling loop."""
        logger.info(f"Agent daemon started. Monitoring: {self.brain_jid}")
        logger.info(f"Poll interval: {self.poll_interval}s")

        try:
            while True:
                self._poll_and_handle()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")
        finally:
            self._save_state()
            self.db.close()

    def _check_wacli_health(self) -> bool:
        """Return False if wacli DB looks stale or stuck."""
        try:
            mtime = self.db_path.stat().st_mtime
            age = time.time() - mtime
            if age > 300:
                logger.warning("wacli DB not modified in %.0fs — wacli may be disconnected", age)
                return False

            conn = self.db._get_connection()
            row = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
            count = row[0] if row else 0

            if self._last_msg_count >= 0 and count == self._last_msg_count:
                self._flat_count_polls += 1
                if self._flat_count_polls >= 2:
                    logger.warning(
                        "Message count flat at %d for %d polls — wacli may be stuck",
                        count,
                        self._flat_count_polls,
                    )
            else:
                self._flat_count_polls = 0

            self._last_msg_count = count
        except Exception as e:
            logger.warning("wacli health check failed: %s", e)

        return True

    def _poll_and_handle(self) -> None:
        """Poll for new messages and handle them."""
        self._check_wacli_health()
        messages = self.db.get_messages_since(self.brain_jid, self.last_seen_ts)

        for msg in messages:
            msg_id = msg.get("id", "")
            if msg_id in self.processed_ids:
                continue

            self._handle_message(msg)
            self.processed_ids.add(msg_id)

            # Update last seen timestamp
            ts = msg.get("timestamp")
            if ts:
                try:
                    self.last_seen_ts = max(self.last_seen_ts, int(ts))
                except (ValueError, TypeError):
                    pass

        self._save_state()

    def _handle_message(self, msg: dict) -> None:
        """Route a single message through intent classification and response."""
        text = msg.get("message", "").strip()
        if not text:
            return

        intent = self.intent_router.classify(text)
        logger.debug(f"Message classified as {intent.value}: {text[:50]}...")

        if intent == Intent.QUERY:
            self._handle_query(text)
        elif intent == Intent.CAPTURE:
            logger.info(f"Captured for nightly digest: {text[:50]}...")
        # Intent.IGNORE — do nothing

    def _handle_query(self, question: str) -> None:
        """Process a query and dispatch response."""
        if not self.rate_limiter.acquire():
            wait = self.rate_limiter.wait_time()
            logger.warning(f"Rate limited. Next message in {wait:.1f}s")
            return

        logger.info(f"Processing query: {question[:80]}...")
        response = self.hermes.answer(question)

        if response:
            self._dispatch(response)

    def _dispatch(self, text: str) -> None:
        """Send response via wacli with jitter to avoid bot-detection patterns."""
        jitter = random.uniform(1.0, 5.0)
        time.sleep(jitter)

        for attempt in range(3):
            try:
                subprocess.run(
                    [self.wacli_path, "send", "--chat", self.brain_jid, "--text", text],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                logger.info("Sent response (%d chars, jitter=%.1fs)", len(text), jitter)
                return
            except subprocess.CalledProcessError as e:
                logger.error("wacli send failed (attempt %d/3): %s", attempt + 1, e.stderr)
                if attempt < 2:
                    time.sleep(60)
            except subprocess.TimeoutExpired:
                logger.error("wacli send timed out (attempt %d/3)", attempt + 1)
                if attempt < 2:
                    time.sleep(60)
            except FileNotFoundError:
                logger.error("wacli not found at: %s", self.wacli_path)
                return

    def _load_state(self) -> int:
        """Load last-seen timestamp from state file."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return data.get("last_seen_ts", 0)
            except (json.JSONDecodeError, OSError):
                pass
        return 0

    def _save_state(self) -> None:
        """Persist state to disk."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_seen_ts": self.last_seen_ts}
        STATE_FILE.write_text(json.dumps(data), encoding="utf-8")


def load_config(config_path: str = "config.yaml") -> dict:
    """Load config from specified path."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    config_file = repo_root / config_path
    if config_file.exists():
        with open(config_file) as f:
            return yaml.safe_load(f)
    return {}


def main():
    parser = argparse.ArgumentParser(description="Sammurai agent daemon")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Config file path (default: config.yaml)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    config = load_config(args.config)
    try:
        daemon = AgentDaemon(config)
        daemon.run()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Daemon failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

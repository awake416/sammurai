"""Gmail API client with OAuth2 authentication and incremental sync.

Uses historyId for race-free incremental sync. Handles token refresh automatically.
"""

import base64
import logging
from pathlib import Path
from typing import Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailClient:
    """Gmail API client with OAuth2 and incremental sync.

    Attributes:
        credentials_path: Path to OAuth2 client credentials JSON
        token_path: Path to stored token JSON (refresh + access)
    """

    def __init__(
        self,
        credentials_path: str = "~/.emailsync/credentials.json",
        token_path: str = "~/.emailsync/token.json",
    ):
        """Initialize Gmail client.

        Args:
            credentials_path: Path to OAuth2 credentials from GCP Console
            token_path: Path to store/load refresh token
        """
        self.credentials_path = Path(credentials_path).expanduser()
        self.token_path = Path(token_path).expanduser()
        self.service = None
        self.creds = None

    def authenticate(self) -> None:
        """Authenticate with Gmail API via OAuth2.

        Opens browser for consent on first run. Refreshes token automatically
        on subsequent runs. Raises if DISPLAY not set (headless server).
        """
        # Load existing token
        if self.token_path.exists():
            self.creds = Credentials.from_authorized_user_file(
                str(self.token_path), SCOPES
            )

        # Refresh or get new token
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                    logger.info("Refreshed OAuth token")
                except RefreshError as e:
                    logger.error(f"Token refresh failed: {e}")
                    # Delete invalid token, force re-auth
                    if self.token_path.exists():
                        self.token_path.unlink()
                    raise
            else:
                # First-time OAuth flow (requires browser)
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Credentials not found: {self.credentials_path}. "
                        "Download from GCP Console."
                    )

                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                self.creds = flow.run_local_server(port=0)
                logger.info("Completed OAuth consent flow")

            # Save token for next run
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, "w") as f:
                f.write(self.creds.to_json())
            # Set restrictive permissions (owner read/write only)
            import os
            os.chmod(self.token_path, 0o600)

        self.service = build("gmail", "v1", credentials=self.creds)

    def fetch_messages(
        self,
        since_history_id: Optional[str] = None,
        labels: list[str] = None,
        skip_labels: list[str] = None,
        max_results: int = 100,
        after_date: Optional[str] = None,
    ) -> tuple[list[dict], str]:
        """Fetch messages since last historyId.

        Args:
            since_history_id: Gmail historyId for incremental sync (None = full sync)
            labels: Label IDs to include (e.g., ["INBOX", "IMPORTANT"])
            skip_labels: Label IDs to exclude (e.g., ["SPAM"])
            max_results: Maximum messages to fetch
            after_date: Date filter in YYYY/MM/DD format (e.g., "2026/05/01")

        Returns:
            Tuple of (message_dicts, new_history_id)

        Raises:
            HttpError: Gmail API error (rate limit, auth, etc.)
        """
        if not self.service:
            self.authenticate()

        messages = []

        if since_history_id:
            # Incremental sync via history API
            # Note: labelId param only accepts 1 label, so we filter all labels
            # in _should_include_message instead
            try:
                history = (
                    self.service.users()
                    .history()
                    .list(
                        userId="me",
                        startHistoryId=since_history_id,
                        maxResults=max_results,
                        historyTypes=["messageAdded"],
                    )
                    .execute()
                )

                new_history_id = history.get("historyId")
                history_records = history.get("history", [])

                for record in history_records:
                    for msg_added in record.get("messagesAdded", []):
                        msg = msg_added.get("message", {})
                        if self._should_include_message(
                            msg, labels, skip_labels
                        ):
                            full_msg = self._fetch_full_message(msg["id"])
                            if full_msg:
                                messages.append(
                                    self._parse_message(full_msg)
                                )

                return messages, new_history_id

            except HttpError as e:
                if e.resp.status == 404:
                    # historyId too old, fall back to full sync
                    logger.warning(
                        "historyId expired, falling back to full sync"
                    )
                    since_history_id = None
                else:
                    raise

        # Full sync (first run or historyId expired) with pagination
        query_parts = []

        # Labels use OR logic (any label matches)
        if labels:
            label_query = " OR ".join([f"label:{label}" for label in labels])
            query_parts.append(f"{{{label_query}}}")

        # Skip labels use AND NOT logic (exclude all)
        # Quote labels containing spaces or special chars
        if skip_labels:
            for label in skip_labels:
                if " " in label or "/" in label or "[" in label:
                    query_parts.append(f'-label:"{label}"')
                else:
                    query_parts.append(f"-label:{label}")

        # Date filter (after:YYYY/MM/DD)
        if after_date:
            query_parts.append(f"after:{after_date}")

        query = " ".join(query_parts) if query_parts else None

        try:
            page_token = None
            total_fetched = 0

            while True:
                result = (
                    self.service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        maxResults=min(max_results - total_fetched, 500),
                        pageToken=page_token
                    )
                    .execute()
                )

                message_ids = [m["id"] for m in result.get("messages", [])]

                for msg_id in message_ids:
                    full_msg = self._fetch_full_message(msg_id)
                    if full_msg:
                        messages.append(self._parse_message(full_msg))
                        total_fetched += 1

                        if total_fetched >= max_results:
                            break

                page_token = result.get("nextPageToken")

                # Stop if no more pages or hit limit
                if not page_token or total_fetched >= max_results:
                    break

                logger.info(f"Fetched {total_fetched}/{max_results} messages, continuing...")

            # Get current historyId for next sync
            profile = self.service.users().getProfile(userId="me").execute()
            new_history_id = profile.get("historyId")

            return messages, new_history_id

        except HttpError as e:
            logger.error(f"Gmail API error: {e}")
            raise

    def _should_include_message(
        self, msg: dict, labels: list[str], skip_labels: list[str]
    ) -> bool:
        """Check if message matches label filters."""
        msg_labels = set(msg.get("labelIds", []))

        if skip_labels and any(label in msg_labels for label in skip_labels):
            return False

        if labels and not any(label in msg_labels for label in labels):
            return False

        return True

    def _fetch_full_message(self, msg_id: str) -> Optional[dict]:
        """Fetch full message details."""
        try:
            return (
                self.service.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
        except HttpError as e:
            logger.warning(f"Failed to fetch message {msg_id}: {e}")
            return None

    def _parse_message(self, msg: dict) -> dict:
        """Parse Gmail API message to email.db schema."""
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }

        # Parse From header: "Name <email@example.com>"
        from_header = headers.get("from", "")
        if "<" in from_header and ">" in from_header:
            sender_name = from_header.split("<")[0].strip()
            sender_jid = from_header.split("<")[1].split(">")[0].strip()
        else:
            sender_name = from_header
            sender_jid = from_header

        # Get message body
        text = self._extract_text(msg.get("payload", {}))

        return {
            "chat_jid": msg.get("threadId"),
            "chat_name": headers.get("subject", "(No Subject)"),
            "msg_id": msg["id"],
            "sender_jid": sender_jid,
            "sender_name": sender_name,
            "ts": int(msg.get("internalDate", 0)) // 1000,  # ms -> seconds
            "from_me": 0,  # Gmail API only fetches received messages
            "text": text,
            "display_text": text,  # TODO: Extract HTML body
            "media_type": None,  # TODO: Handle attachments
            "media_caption": None,
            "filename": None,
            "mime_type": None,
            "direct_path": None,
            "media_key": None,
            "file_sha256": None,
            "file_enc_sha256": None,
            "file_length": None,
            "local_path": None,
            "downloaded_at": None,
        }

    def _extract_text(self, payload: dict) -> str:
        """Extract plain text from message payload."""
        if "body" in payload and payload["body"].get("data"):
            # Base64-decode body
            data = payload["body"]["data"]
            # Gmail uses URL-safe base64
            data = data.replace("-", "+").replace("_", "/")
            return base64.b64decode(data).decode("utf-8", errors="ignore")

        # Multipart message
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    if "body" in part and part["body"].get("data"):
                        data = part["body"]["data"]
                        data = data.replace("-", "+").replace("_", "/")
                        return base64.b64decode(data).decode(
                            "utf-8", errors="ignore"
                        )

        return ""

    def download_attachment(
        self, msg_id: str, attachment_id: str, save_path: str
    ) -> bool:
        """Download message attachment.

        Args:
            msg_id: Gmail message ID
            attachment_id: Attachment ID from message payload
            save_path: Local path to save file

        Returns:
            True if download succeeded, False otherwise
        """
        if not self.service:
            self.authenticate()

        try:
            attachment = (
                self.service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=msg_id, id=attachment_id)
                .execute()
            )

            data = attachment["data"]
            data = data.replace("-", "+").replace("_", "/")
            file_data = base64.b64decode(data)

            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(file_data)

            logger.info(f"Downloaded attachment to {save_path}")
            return True

        except HttpError as e:
            logger.error(f"Failed to download attachment: {e}")
            return False
        except OSError as e:
            logger.error(f"Failed to write attachment: {e}")
            return False

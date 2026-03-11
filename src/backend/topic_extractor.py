import logging
import httpx
import os
import ipaddress
import socket
import ssl
from urllib.parse import urlparse
from typing import List, Optional
from bs4 import BeautifulSoup
from src.backend.models import (
    Message,
    TopicItem,
    DocumentSummary,
    ActionableItem,
)
from src.backend.llm_client import LLMClient
from src.backend.document_parser import DocumentParser
from src.backend.utils import redact_pii


logger = logging.getLogger(__name__)


class TopicExtractor:
    def __init__(
        self, llm_client: LLMClient, document_parser: Optional[DocumentParser] = None
    ):
        self.llm_client = llm_client
        self.document_parser = document_parser

    def extract_topics(self, messages: List[Message]) -> List[TopicItem]:
        """Uses LLM to identify recurring themes from a list of messages.

        Messages should be enriched beforehand if document content is needed.
        """
        # Law of the Early Exit
        if not messages:
            return []

        # Convert Message objects to dicts for LLMClient
        msg_dicts = [msg.model_dump() for msg in messages]

        result = self.llm_client.extract_topics(msg_dicts)
        if not result or "topics" not in result:
            return []

        return [TopicItem(**topic) for topic in result["topics"]]

    def _is_safe_url(self, url: str) -> Optional[str]:
        """Validate URL to prevent SSRF. Returns resolved IP if safe, None otherwise."""
        try:
            parsed = urlparse(url)
            # Restrict to https only
            if parsed.scheme != "https":
                logger.warning(
                    f"Insecure URL scheme: {parsed.scheme}. Only https is allowed."
                )
                return None

            hostname = parsed.hostname
            if not hostname:
                return None

            # Resolve hostname to IP to check for private ranges
            try:
                # Use getaddrinfo to support both IPv4 and IPv6
                addr_info = socket.getaddrinfo(hostname, None)
                ips = [info[4][0] for info in addr_info]
            except (socket.gaierror, ValueError):
                # If we can't resolve it, it might be an invalid hostname or an IP already
                try:
                    ip = ipaddress.ip_address(hostname)
                    ips = [str(ip)]
                except ValueError:
                    return None

            safe_ip = None
            for ip_str in ips:
                ip = ipaddress.ip_address(ip_str)
                if (
                    ip.is_loopback
                    or ip.is_private
                    or ip.is_link_local
                    or ip.is_multicast
                ):
                    logger.warning(f"Blocked internal/private IP: {ip}")
                    return None
                # Use the first safe IP found
                if safe_ip is None:
                    safe_ip = ip_str

            return safe_ip
        except Exception as e:
            logger.error(f"URL validation error: {e}")
            return None

    def summarize_document(
        self, url: str, file_path: Optional[str] = None
    ) -> DocumentSummary:
        """Fetches URL content or reads local file and extracts summary using LLM."""
        # SSRF Protection: Validate URL before any processing if no local file
        use_local = bool(
            file_path and os.path.exists(file_path) and self.document_parser
        )
        safe_ip = None

        if not use_local:
            safe_ip = self._is_safe_url(url)
            if not safe_ip:
                raise ValueError(f"Insecure or invalid URL: {url}")

        try:
            if use_local:
                # Use local file if provided and exists
                clean_text = self.document_parser.extract_text(file_path)  # type: ignore
            else:
                parsed = urlparse(url)
                # Construct URL with IP to prevent DNS rebinding (TOCTOU)
                # We connect directly to the IP but pass the original hostname in the Host header.
                # safe_ip is guaranteed to be set here because use_local is False

                # Preserve port if it exists
                safe_netloc = f"{safe_ip}:{parsed.port}" if parsed.port else safe_ip

                from urllib.parse import urlunparse

                target_url = urlunparse(
                    (
                        parsed.scheme,
                        safe_netloc,
                        parsed.path,
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )

                # Parse Don't Validate: Fetch content first
                # Use a custom transport to handle SNI and certificate verification properly
                # when connecting to an IP address.
                ctx = ssl.create_default_context()
                transport = httpx.HTTPTransport(verify=ctx)

                response = httpx.get(
                    target_url,
                    headers={"Host": parsed.hostname},
                    extensions={"sni_hostname": parsed.hostname.encode()},
                    transport=transport,
                    timeout=30.0,
                    follow_redirects=False,
                )
                response.raise_for_status()

                # Extract text using BeautifulSoup
                soup = BeautifulSoup(response.text, "html.parser")

                # Remove script and style elements
                for script_or_style in soup(["script", "style"]):
                    script_or_style.decompose()

                # Get text and clean up whitespace
                text = soup.get_text(separator=" ")
                lines = (line.strip() for line in text.splitlines())
                chunks = (
                    phrase.strip() for line in lines for phrase in line.split("  ")
                )
                clean_text = " ".join(chunk for chunk in chunks if chunk)

            # Truncate to avoid token limits (approx 4000 words)
            truncated_content = clean_text[:16000]
        except Exception as e:
            # Fail Fast, Fail Loud (log error and return error summary)
            logger.error(
                f"Failed to fetch or parse document {redact_pii(file_path or url)}: {e}"
            )
            return DocumentSummary(
                resource_url=url,
                title="Error",
                summary=f"Could not fetch content: {str(e)}",
                key_dates=[],
            )

        result = self.llm_client.summarize_document(truncated_content, url)
        if not result:
            return DocumentSummary(
                resource_url=url,
                title="Summary Failed",
                summary="LLM failed to generate summary.",
                key_dates=[],
            )

        return DocumentSummary(
            resource_url=url,
            title=result.get("title", "Untitled"),
            summary=result.get("summary", "No summary available."),
            key_dates=result.get("key_dates", []),
        )

    def _format_action_item(self, item: ActionableItem, count: int = 1) -> str:
        """Formats a single action item for the digest."""
        deadline_str = f" - Due: {item.deadline}" if item.deadline else ""
        count_str = f" ({count} similar tasks)" if count > 1 else ""
        return f"- [{item.priority.value}] {item.task}{count_str}{deadline_str}"

    def _aggregate_tasks(
        self, tasks: List[ActionableItem]
    ) -> List[tuple[ActionableItem, int]]:
        """Groups tasks that have similar text (>50% words shared)."""
        # Law of the Early Exit
        if not tasks:
            return []

        aggregated: List[tuple[ActionableItem, int]] = []

        for task in tasks:
            found_match = False
            # Law of Intentional Naming: task_words
            task_words = set(task.task.lower().split())

            for i, (agg_task, count) in enumerate(aggregated):
                agg_words = set(agg_task.task.lower().split())

                if not task_words or not agg_words:
                    continue

                shared_words = task_words.intersection(agg_words)
                # Similarity: shared words / max words in either task
                similarity = len(shared_words) / max(len(task_words), len(agg_words))

                if similarity > 0.5:
                    aggregated[i] = (agg_task, count + 1)
                    found_match = True
                    break

            if not found_match:
                aggregated.append((task, 1))

        return aggregated

    def generate_digest(
        self,
        action_items: List[ActionableItem],
        topics: List[TopicItem],
        document_summaries: Optional[List[DocumentSummary]] = None,
        group_name: str = "",
        date_range: str = "",
    ) -> str:
        """Format A: Community Digest."""
        # Law of Intentional Naming: digest_parts
        suffix = f" : {group_name}" if group_name else ""
        date_suffix = f" [{date_range}]" if date_range else ""
        header = f"# 🏘️ Community digest{suffix}{date_suffix}"

        digest_parts = [header, ""]

        if topics:
            digest_parts.append("## 📊 Trending Topics")
            digest_parts.append("")
            # Sort topics by message_count descending (Law of Intentional Naming)
            sorted_topics = sorted(topics, key=lambda x: x.message_count, reverse=True)
            for topic in sorted_topics:
                digest_parts.append(
                    f"### {topic.topic} ({topic.message_count} messages)"
                )
                digest_parts.append(topic.summary)
                digest_parts.append("")
        else:
            digest_parts.append("No topics identified.")
            digest_parts.append("")

        if document_summaries:
            digest_parts.append("## 📄 Document Summaries")
            digest_parts.append("")
            for ds in document_summaries:
                digest_parts.append(f"### {ds.title}")
                digest_parts.append(ds.summary)
                if ds.key_dates:
                    digest_parts.append(f"**Key Dates:** {', '.join(ds.key_dates)}")
                digest_parts.append("")

        if action_items:
            digest_parts.append("## 📋 Tasks")
            digest_parts.append("")

            # Group by primary tag
            grouped_tasks = {}
            for item in action_items:
                tag = item.topic_tags[0] if item.topic_tags else "General"
                if tag not in grouped_tasks:
                    grouped_tasks[tag] = []
                grouped_tasks[tag].append(item)

            # Sort tags (General last)
            sorted_tags = sorted([t for t in grouped_tasks.keys() if t != "General"])
            if "General" in grouped_tasks:
                sorted_tags.append("General")

            for tag in sorted_tags:
                digest_parts.append(f"### {tag}")
                aggregated_tasks = self._aggregate_tasks(grouped_tasks[tag])
                for item, count in aggregated_tasks:
                    digest_parts.append(self._format_action_item(item, count))
                digest_parts.append("")

        return "\n".join(digest_parts).strip()

    def tag_items_with_topics(
        self, action_items: List[ActionableItem], topics: List[TopicItem]
    ) -> List[ActionableItem]:
        """Format B: Topics as tags. Maps action items to identified topics.

        Returns a new list of ActionableItem objects with topic_tags updated.
        """
        # Law of the Early Exit
        if not action_items or not topics:
            return [item.model_copy(deep=True) for item in action_items]

        topic_names = [t.topic for t in topics]
        item_dicts = [item.model_dump() for item in action_items]

        result = self.llm_client.tag_items_with_topics(item_dicts, topic_names)
        if not result or "tagged_items" not in result:
            return [item.model_copy(deep=True) for item in action_items]

        # Create new items to avoid in-place mutation (Atomic Predictability)
        new_items = [item.model_copy(deep=True) for item in action_items]

        # Map results back to items
        for tag_info in result["tagged_items"]:
            idx = tag_info.get("item_index")
            try:
                if idx is not None:
                    idx = int(idx)
            except (ValueError, TypeError):
                continue

            if idx is not None and 0 <= idx < len(new_items):
                new_items[idx].topic_tags = [
                    t for t in tag_info.get("topics", []) if t and t.strip()
                ]

        return new_items

"""LLM Client for WhatsApp Action Item Extraction using LiteLLM."""

import os
import json
import logging
import litellm

# Completely disable litellm verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False

# Suppress all litellm-related loggers
for logger_name in [
    "litellm",
    "litellm._logging",
    "litellm.main",
    "LiteLLM",
    "LiteLLM Proxy",
    "LiteLLM Router",
]:
    logging.getLogger(logger_name).setLevel(logging.CRITICAL)

# Also suppress httpx and other HTTP client logs
for logger_name in ["httpx", "urllib3", "httpcore", "openai"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

# Set root logger to WARNING to catch any stray logs
logging.getLogger().setLevel(logging.WARNING)

from datetime import datetime, timezone
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.backend.utils import redact_pii

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Custom exception for LLM-related errors."""

    pass


class LLMClient:
    TOPIC_EXTRACTION_SYSTEM_PROMPT = """You are an expert at analyzing WhatsApp group conversations to identify recurring topics and themes.
Given a list of messages, identify the main topics being discussed.
For each topic, provide:
1. The topic name (e.g., 'Parking', 'Security', 'School Events').
2. A 2-3 sentence summary of what was discussed.
3. The number of messages related to this topic.
4. A few sample messages (or snippets) that represent the topic.

Respond with JSON in this exact format:
{
    "topics": [
        {
            "topic": "Topic Name",
            "summary": "2-3 sentence description of what was discussed.",
            "message_count": 5,
            "sample_messages": ["Sample message 1", "Sample message 2"]
        }
    ]
}

Rules:
- Identify all distinct topics discussed, even if mentioned only once. Provide a detailed 2-3 sentence summary for each topic.
- Be specific with topic names.
- If no clear topics are found, return an empty list for "topics".
"""

    DOCUMENT_SUMMARIZATION_SYSTEM_PROMPT = """You are an expert at summarizing documents and web pages.
Given the content of a page, provide:
1. A concise title for the document.
2. A brief summary (2-3 sentences) of the main content.
3. A list of any important dates mentioned (e.g., deadlines, event dates).

Respond with JSON in this exact format:
{
    "title": "Document Title",
    "summary": "Brief summary...",
    "key_dates": ["YYYY-MM-DD", "Event on Monday"]
}
"""

    # Braces are doubled {{ / }} because this prompt is used with .format()
    TAG_ITEMS_SYSTEM_PROMPT = """You are an expert at categorizing tasks into topics.
Given a list of action items and a list of available topics, assign the most relevant topics to each action item.
Available Topics: {topic_names}

Respond with JSON in this exact format:
{{
    "tagged_items": [
        {{
            "item_index": 0,
            "topics": ["Topic A", "Topic B"]
        }}
    ]
}}

Rules:
- Only use topics from the provided list.
- An item can have multiple topics or none.
- MUST include the correct item_index.
"""

    _EXTRACTION_RULES = """Rules:
- Only include actual action items (verb + action).
- SCHOOL RULE: If the message is about school (exams, tests, trips, syllabus, homework), it IS an action item (e.g., 'Prepare for exam', 'Sign permission slip'). You MUST extract the exact date/deadline for the event.
- NOT an action item: Jokes, sarcasm, self-deprecating humor, buzzword usage without a real task, rhetorical questions, statements about oneself (e.g., 'I should update my LinkedIn').
- Example of a JOKE to reject: 'Update LinkedIn profile to include CEO of North India'.
- Example of sarcasm to reject: 'Apply agentic workflow'.
- Asking vs Telling: Distinguish between asking for advice ("Should I do X?") vs telling someone to do something ("Please do X"). Only "Telling" is an action item.
- Ignore casual conversation, greetings, FYIs.
- Extract specific assignee if mentioned (name or @mention).
- Extract deadline if explicitly stated (YYYY-MM-DD format, or null if not specified).
- Priority Rules: Default to Medium. Use High ONLY for urgent deadlines (within 24-48 hours) or emergencies. Use Low for general FYI tasks or non-urgent additions.
- CRITICAL: Extract ANY and ALL URLs, document names, poll links, form links, or other resources mentioned in the message into the "resources" array. Be extremely aggressive—if you see anything that looks like a link or a resource, extract it.
- CRITICAL: If the message implies an action but doesn't have a link, specify HOW the action should be taken (e.g., 'reply in this group', 'call them').
- Explicitly extract syllabus details, test dates, poll links, and payment links into the "resources" array.
- Anti-Hallucination: Do NOT invent tasks from vague statements. If the message could be interpreted as a joke, it IS a joke. When in doubt, mark as NOT an action item."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "gemini/gemini-2.5-flash",
        confidence_threshold: float = 0.75,
    ):
        self.base_url = (base_url or os.environ.get("LITELLM_BASE_URL") or "").rstrip(
            "/"
        )
        self.api_key = api_key or os.environ.get("LITELLM_API_KEY")
        self.model = model
        self.confidence_threshold = confidence_threshold

        if not self.base_url:
            raise ValueError(
                "LLM Configuration Error: LiteLLM base URL is missing. "
                "Please set the LITELLM_BASE_URL environment variable."
            )
        if not self.api_key:
            raise ValueError(
                "LLM Authentication Error: API key is missing. "
                "Please set the LITELLM_API_KEY environment variable (e.g., export LITELLM_API_KEY='your-key-here')."
            )

    def _get_completions_url(self) -> str:
        """Get the proper chat completions URL, handling /v1 suffix correctly."""
        # If base_url already ends with /v1, don't add it again
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/v1/chat/completions"

    def extract_action_item(
        self, message: str, sender: Optional[str] = None
    ) -> Optional[dict]:
        """Use LLM to extract action item from a WhatsApp message."""

        system_prompt = f"""You are an expert at analyzing WhatsApp messages to extract action items/tasks. Your goal is to free the user from reading the original WhatsApp messages. Provide enough context and a clear, standalone action so they know exactly what to do without checking the chat.

The current date is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}. Use this to resolve relative dates like 'tonight', 'tomorrow', or 'next week'.

Given a WhatsApp message, determine if it contains an actionable task that someone needs to do.

If it's an action item, respond with JSON in this exact format:
{{
    "is_action_item": true,
    "task": "A highly descriptive, standalone action item. MUST include WHERE or HOW to do it if EXPLICITLY stated (e.g., 'Cast your vote in the poll above', 'Email Jacob Eapen to push back on his proposal'). For school-related tasks, be extremely specific about the event (e.g., 'Prepare for Grade 5 Math Exam', 'Sign permission slip for Kodai trip').",
    "category": "School", "Bills", "Community", "Events", "Work", or "Other",
    "context": "A brief 1-2 sentence summary of the background info (e.g., 'The electricity company sent a notice about the overdue payment for January.')",
    "assignee": "Person responsible (or 'unassigned' if unclear)",
    "deadline": "Deadline if mentioned (YYYY-MM-DD format, or null if not specified). For school events, this is the date of the exam/trip/test.",
    "priority": "High", "Medium", or "Low",
    "confidence": 0.0-1.0,
    "resources": [
        {{
            "type": "url", "document", "poll", "form", or "event",
            "value": "The actual URL, document name, or link mentioned",
            "description": "Brief description of what the resource is"
        }}
    ]
}}

If it's NOT an action item, respond with:
{{
    "is_action_item": false,
    "confidence": 0.0-1.0
}}

{LLMClient._EXTRACTION_RULES}"""

        user_message = f"Message: {message}"
        if sender:
            user_message += f"\nSender: {sender}"

        # Log raw prompt (redacted and truncated)
        redacted_prompt = redact_pii(user_message)
        logger.debug(f"LLM Prompt: {redacted_prompt[:500]}...")

        try:
            logger.debug(f"Sending request to {self.base_url} with model {self.model}")
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                api_key=self.api_key or "sk-dummy",
                base_url=self.base_url,
                custom_llm_provider="openai",
                timeout=120,
            )

            content = response.choices[0].message.content
            if content is None:
                logger.warning("LLM response content is None")
                return None

            # Log raw response (redacted)
            redacted_response = redact_pii(content)
            logger.debug(f"LLM Raw Response: {redacted_response}")

            logger.debug(f"LLM response received (length: {len(content)})")

            # Parse JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start < 0 or json_end <= json_start:
                logger.warning("Could not parse JSON from LLM response")
                return None

            parsed = json.loads(content[json_start:json_end])

            # Enforce confidence threshold
            if (
                parsed.get("is_action_item")
                and parsed.get("confidence", 0.0) < self.confidence_threshold
            ):
                logger.info(
                    f"Action item found but confidence ({parsed.get('confidence')}) "
                    f"is below threshold ({self.confidence_threshold}). Marking as non-action item."
                )
                parsed["is_action_item"] = False

            # Log without sensitive data
            logger.debug(
                f"Parsed LLM response: is_action_item={parsed.get('is_action_item')}, confidence={parsed.get('confidence')}"
            )
            return parsed

        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return None
        except (
            litellm.AuthenticationError,
            litellm.BadRequestError,
            litellm.APIConnectionError,
        ):
            # Re-raise authentication, configuration, and connection errors
            raise
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return None

    def generate_json(
        self, system_prompt: str, user_message: str, temperature: float = 0.1
    ) -> Optional[dict]:
        """Generic method to generate JSON from LLM."""
        try:
            logger.debug(
                f"Sending generic JSON request to {self.base_url} with model {self.model}"
            )
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                api_key=self.api_key or "sk-dummy",
                base_url=self.base_url,
                custom_llm_provider="openai",
                timeout=120,
            )

            content = response.choices[0].message.content
            if content is None:
                logger.warning("LLM response content is None")
                return None

            # Parse JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start < 0 or json_end <= json_start:
                logger.warning("Could not parse JSON from LLM response")
                return None

            return json.loads(content[json_start:json_end])

        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM request failed: {e}")
            return None

    def extract_topics(self, messages: List[dict]) -> Optional[dict]:
        """Extract recurring topics from a list of messages."""
        if not messages:
            return {"topics": []}

        formatted_messages = []
        for i, msg in enumerate(messages):
            sender_info = f" [from {msg.get('sender')}]" if msg.get("sender") else ""
            formatted_messages.append(f"[{i}]{sender_info}: {msg.get('message', '')}")

        user_message = "Messages to analyze for recurring topics:\n" + "\n".join(
            formatted_messages
        )

        return self.generate_json(self.TOPIC_EXTRACTION_SYSTEM_PROMPT, user_message)

    def summarize_document(
        self, content: str, url: Optional[str] = None
    ) -> Optional[dict]:
        """Summarize document content."""
        if not content:
            return None

        user_message = f"Content to summarize:\n{content}"
        if url:
            user_message = f"URL: {url}\n" + user_message

        return self.generate_json(
            self.DOCUMENT_SUMMARIZATION_SYSTEM_PROMPT, user_message
        )

    def tag_items_with_topics(
        self, items: List[dict], topic_names: List[str]
    ) -> Optional[dict]:
        """Tag action items with relevant topics."""
        if not items or not topic_names:
            return {"tagged_items": []}

        formatted_items = []
        for i, item in enumerate(items):
            formatted_items.append(
                f"[{i}] {item.get('task')} (Context: {item.get('context') or 'N/A'})"
            )

        user_message = "Action Items to tag:\n" + "\n".join(formatted_items)
        system_prompt = self.TAG_ITEMS_SYSTEM_PROMPT.format(
            topic_names=", ".join(topic_names)
        )

        return self.generate_json(system_prompt, user_message)

    def extract_batch(
        self,
        messages: list[dict],
        batch_size: int = 50,
        parallel_batches: int = 1,
    ) -> list[dict]:
        """Extract action items from multiple messages in batches.

        Args:
            messages: List of dicts with 'message' and optional 'sender', 'timestamp' keys
            batch_size: Number of messages to process per LLM call (default: 50)
            parallel_batches: Number of concurrent batches to process (default: 1)

        Returns:
            List of action items found
        """
        if not messages:
            return []

        all_action_items = []

        # Prepare batches
        batches = []
        for i in range(0, len(messages), batch_size):
            batches.append((messages[i : i + batch_size], i))

        num_batches = len(batches)
        logger.info(
            f"Processing {len(messages)} messages in {num_batches} batches "
            f"(parallel_batches={parallel_batches})..."
        )

        if parallel_batches > 1:
            # Pre-size list to maintain order
            batch_results = [[] for _ in range(num_batches)]
            with ThreadPoolExecutor(max_workers=parallel_batches) as executor:
                futures = {
                    executor.submit(
                        self._extract_batch_single, batch, batch_offset=offset
                    ): batch_num
                    for batch_num, (batch, offset) in enumerate(batches)
                }

                for future in as_completed(futures):
                    batch_num = futures[future]
                    try:
                        batch_items = future.result()
                        batch_results[batch_num] = batch_items
                        logger.info(f"Completed batch {batch_num + 1}/{num_batches}")
                    except Exception as e:
                        logger.error(f"Error in batch {batch_num + 1}: {e}")
                        raise  # Re-raise to be caught by the caller

            # Flatten results in order
            for batch_items in batch_results:
                all_action_items.extend(batch_items)
        else:
            # Sequential processing
            for batch_num, (batch, offset) in enumerate(batches, 1):
                logger.info(f"Processing batch {batch_num}/{num_batches}...")
                batch_items = self._extract_batch_single(batch, batch_offset=offset)
                all_action_items.extend(batch_items)

        return all_action_items

    def _extract_batch_single(
        self, messages: list[dict], batch_offset: int = 0
    ) -> list[dict]:
        """Extract action items from a single batch of messages.

        Args:
            messages: List of dicts with 'message' and optional 'sender', 'timestamp' keys
            batch_offset: Offset to add to message indices for global positioning

        Returns:
            List of action items found in this batch
        """
        system_prompt = f"""You are an expert at analyzing WhatsApp messages to extract action items/tasks. Your goal is to free the user from reading the original WhatsApp messages. Provide enough context and a clear, standalone action so they know exactly what to do without checking the chat.

The current date is {datetime.now(timezone.utc).strftime("%Y-%m-%d")}. Use this to resolve relative dates like 'tonight', 'tomorrow', or 'next week'.

Given a list of WhatsApp messages, determine which ones contain actionable tasks.

Respond with JSON in this exact format:
{{
    "action_items": [
        {{
            "is_action_item": true,
            "task": "A highly descriptive, standalone action item. MUST include WHERE or HOW to do it if EXPLICITLY stated (e.g., 'Cast your vote in the poll above', 'Email Jacob Eapen to push back on his proposal'). For school-related tasks, be extremely specific about the event (e.g., 'Prepare for Grade 5 Math Exam', 'Sign permission slip for Kodai trip').",
            "category": "School", "Bills", "Community", "Events", "Work", or "Other",
            "context": "A brief 1-2 sentence summary of the background info (e.g., 'The electricity company sent a notice about the overdue payment for January.')",
            "assignee": "Person responsible (or 'unassigned' if unclear)",
            "deadline": "Deadline if mentioned (YYYY-MM-DD format, or null). For school events, this is the date of the exam/trip/test.",
            "priority": "High", "Medium", or "Low",
            "confidence": 0.0-1.0,
            "resources": [
                {{"type": "url", "document", "poll", "form", or "event", "value": "URL/name/link", "description": "..."}}
            ],
            "original_message_index": <integer index of the message from the input list>
        }}
    ]
}}

{LLMClient._EXTRACTION_RULES}
- MUST include the correct original_message_index so we can map it back."""

        # Format messages for the prompt
        formatted_messages = []
        for i, msg in enumerate(messages):
            sender_info = f" [from {msg.get('sender')}]" if msg.get("sender") else ""
            formatted_messages.append(f"[{i}]{sender_info}: {msg.get('message', '')}")

        user_message = "Messages to analyze:\n" + "\n".join(formatted_messages)

        # Log raw prompt (redacted and truncated)
        redacted_prompt = redact_pii(user_message)
        logger.debug(f"LLM Batch Prompt: {redacted_prompt[:1000]}...")

        try:
            logger.debug(
                f"Sending batch request to {self.base_url} with model {self.model}"
            )
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.1,
                api_key=self.api_key or "sk-dummy",
                base_url=self.base_url,
                custom_llm_provider="openai",
                timeout=60,
            )

            content = response.choices[0].message.content
            if content is None:
                logger.warning("LLM batch response content is None")
                return []

            # Log raw response (redacted)
            redacted_response = redact_pii(content)
            logger.debug(f"LLM Batch Raw Response: {redacted_response}")

            # Parse JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start < 0 or json_end <= json_start:
                logger.warning("Could not parse JSON from LLM response")
                return []

            parsed_data = json.loads(content[json_start:json_end])

            action_items = []
            for item in parsed_data.get("action_items", []):
                if (
                    item.get("is_action_item")
                    and item.get("confidence", 0.0) >= self.confidence_threshold
                ):
                    idx = item.get("original_message_index")

                    # Handle string index from LLM
                    try:
                        if idx is not None:
                            idx = int(idx)
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Invalid message index type: {type(idx)} ({idx})"
                        )
                        continue

                    # Validate index before accessing messages
                    if idx is None or not (0 <= idx < len(messages)):
                        logger.warning(
                            f"Invalid message index {idx} (batch offset: {batch_offset}), skipping item"
                        )
                        continue

                    # Adjust index by batch offset to get absolute position
                    absolute_idx = batch_offset + idx
                    original_msg = messages[idx]

                    action_items.append(
                        {
                            "task": item.get("task"),
                            "category": item.get("category", "Other"),
                            "context": item.get("context"),
                            "assignee": item.get("assignee", "unassigned"),
                            "deadline": item.get("deadline"),
                            "priority": item.get("priority", "Medium"),
                            "confidence": item.get("confidence"),
                            "resources": item.get("resources", []),
                            "original_message": original_msg.get("message", ""),
                            "sender": original_msg.get("sender"),
                            "timestamp": original_msg.get("timestamp"),
                            "group_name": original_msg.get("group_name"),
                            "group_jid": original_msg.get("group_jid"),
                            "message_ref": absolute_idx,
                        }
                    )
            return action_items

        except (KeyError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse LLM batch response: {e}")
            return []
        except (
            litellm.AuthenticationError,
            litellm.BadRequestError,
            litellm.APIConnectionError,
        ):
            # Re-raise critical exceptions to trigger fail-fast logic
            raise
        except Exception as e:
            logger.error(f"LLM batch request failed: {e}")
            return []

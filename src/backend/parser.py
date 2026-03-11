import re
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.backend.utils import redact_pii

spacy = None
nlp = None

try:
    import spacy

    # Try to load model, fallback gracefully if not installed or incompatible
    try:
        nlp = spacy.load("en_core_web_sm")
    except Exception as e:
        logging.warning(
            f"spaCy model not available: {e}. NLP features will use regex fallback."
        )
        nlp = None
except Exception as e:
    logging.warning(
        f"spaCy not installed or incompatible: {e}. NLP features will use regex fallback."
    )

logger = logging.getLogger(__name__)


# Pre-compiled regex patterns for performance
MENTION_PATTERN = re.compile(r"@(\w+)")
ASSIGNEE_NAME_PATTERN = re.compile(
    r"(\w+)\s+(?:will|should|needs to|has to|to)\s+", re.IGNORECASE
)
NEWLINE_PATTERN = re.compile(r"\n")
WHITESPACE_PATTERN = re.compile(r"\s+")
SENTENCE_SPLIT_PATTERN = re.compile(r"[.!?]")

# Pre-compiled date patterns for extract_date
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", re.IGNORECASE), "MDY"),
    (re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", re.IGNORECASE), "MDY"),
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", re.IGNORECASE), "YMD"),
    (re.compile(r"\btoday\b", re.IGNORECASE), "special"),
    (re.compile(r"\btomorrow\b", re.IGNORECASE), "special"),
    (re.compile(r"\bnext week\b", re.IGNORECASE), "special"),
    (
        re.compile(
            r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.IGNORECASE,
        ),
        "next_weekday",
    ),
    (
        re.compile(
            r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.IGNORECASE,
        ),
        "weekday",
    ),
]


ABBREVIATIONS = {
    "ASAP": "as soon as possible",
    "FYI": "for your information",
    "LFG": "let's go",
    "EOD": "end of day",
    "EOW": "end of week",
    "EOM": "end of month",
    "EOQ": "end of quarter",
    "TBD": "to be determined",
    "TBA": "to be announced",
    "NDA": "non-disclosure agreement",
    "OTP": "one-time password",
    "WIP": "work in progress",
    "ETA": "estimated time of arrival",
    "BRB": "be right back",
    "IMO": "in my opinion",
    "IMHO": "in my humble opinion",
}

# Pre-compiled patterns for abbreviation expansion
ABBREVIATION_PATTERNS = {
    abbr: re.compile(rf"\b{abbr}\b", re.IGNORECASE) for abbr in ABBREVIATIONS.keys()
}

PROJECT_PHASES = [
    "kick-off",
    "kickoff",
    "design review",
    "sprint planning",
    "daily standup",
    "standup",
    "retrospective",
    "retro",
    "demo",
    "showcase",
    "deployment",
    "release",
    "launch",
    "go-live",
    "post-mortem",
    "brainstorm",
    "scoping",
    "estimation",
    "planning",
    "review",
    "testing",
    "QA",
    "UAT",
    "bug bash",
]

ACTION_KEYWORDS = [
    "action",
    "todo",
    "to-do",
    "task",
    "follow up",
    "follow-up",
    "followup",
    "deadline",
    "due",
    "by",
    "assigned",
    "responsible",
    "owner",
    "please",
    "can you",
    "could you",
    "need to",
    "must",
    "should",
    "remember to",
    "don't forget",
    "ensure",
    "make sure",
    "verify",
    "confirm",
    "check",
    "review",
    "update",
    "send",
    "prepare",
    "create",
    "write",
    "schedule",
    "arrange",
    "organize",
    "complete",
    "finish",
]

URGENCY_KEYWORDS = {
    "high": [
        "urgent",
        "asap",
        "immediately",
        "now",
        "critical",
        "important",
        "priority",
    ],
    "medium": ["soon", "this week", "eod", "end of day", "today"],
    "low": ["whenever", "no rush", "later", "sometime", "eow", "end of week"],
}


def expand_abbreviations(text: str) -> str:
    for abbr, pattern in ABBREVIATION_PATTERNS.items():
        full = ABBREVIATIONS[abbr]
        text = pattern.sub(full, text)
    return text


def detect_project_phase(text: str) -> Optional[str]:
    text_lower = text.lower()
    for phase in PROJECT_PHASES:
        if phase in text_lower:
            return phase
    return None


def detect_urgency(text: str) -> Optional[str]:
    text_lower = text.lower()
    for keyword in URGENCY_KEYWORDS["high"]:
        if keyword in text_lower:
            return "High"
    for keyword in URGENCY_KEYWORDS["medium"]:
        if keyword in text_lower:
            return "Medium"
    for keyword in URGENCY_KEYWORDS["low"]:
        if keyword in text_lower:
            return "Low"
    return None


def extract_date(text: str, current_date: Optional[datetime] = None) -> Optional[str]:
    if current_date is None:
        current_date = datetime.now()

    for pattern, fmt in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            logger.debug(
                f"Date pattern match found: {fmt} for text: '{redact_pii(match.group(0))}'"
            )
            if fmt == "special":
                return parse_special_date(match.group(0), current_date)
            elif fmt == "weekday":
                return parse_weekday(match.group(1), current_date, is_next=False)
            elif fmt == "next_weekday":
                # For "next [weekday]", group(1) contains the weekday name
                return parse_weekday(match.group(1), current_date, is_next=True)
            elif fmt in ("MDY", "YMD"):
                return parse_numeric_date(match, fmt)

    return None


def parse_special_date(word: str, current_date: datetime) -> Optional[str]:
    word_lower = word.lower()
    result = current_date

    if word_lower == "today":
        return result.strftime("%Y-%m-%d")
    elif word_lower == "tomorrow":
        result += timedelta(days=1)
        return result.strftime("%Y-%m-%d")
    elif word_lower == "next week":
        result += timedelta(days=7)
        return result.strftime("%Y-%m-%d")
    return None


def parse_weekday(
    day_name: str, current_date: datetime, is_next: bool = False
) -> Optional[str]:
    # Python's weekday(): Monday=0, Sunday=6
    # Use direct mapping instead of list to avoid index mismatch
    day_mapping = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    try:
        target_day = day_mapping[day_name.lower()]
        current_day = current_date.weekday()  # Monday=0, Sunday=6
        days_to_add = target_day - current_day
        if days_to_add <= 0:
            days_to_add += 7
        # For "next [weekday]", add another 7 days to get to the following week
        if is_next:
            days_to_add += 7
        result = current_date + timedelta(days=days_to_add)
        return result.strftime("%Y-%m-%d")
    except (ValueError, KeyError):
        return None


def parse_numeric_date(match: re.Match, fmt: str) -> Optional[str]:
    try:
        if fmt == "MDY":
            month, day, year = (
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        elif fmt == "YMD":
            year, month, day = (
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        else:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return None


def extract_assignee(text: str, sender: Optional[str] = None) -> str:
    mentions = MENTION_PATTERN.findall(text)
    if mentions:
        logger.debug(f"Assignee mention match found: {redact_pii(mentions[0])}")
        return mentions[0]

    name_pattern = ASSIGNEE_NAME_PATTERN.search(text)
    if name_pattern:
        candidate = name_pattern.group(1)
        logger.debug(f"Assignee name pattern candidate found: {redact_pii(candidate)}")
        invalid_names = {
            "due",
            "please",
            "urgent",
            "important",
            "asap",
            "today",
            "tomorrow",
            "everyone",
            "all",
            "team",
            "folks",
            "residents",
            "owners",
            "which",
            "what",
            "who",
            "when",
            "where",
            "why",
            "how",
            "can",
            "could",
            "will",
            "would",
            "should",
            "may",
            "might",
            "must",
            "shall",
        }
        if candidate.lower() not in invalid_names:
            return candidate

    if sender:
        return sender.split()[0] if sender.split() else "unassigned"

    return "unassigned"


def extract_assignee_nlp(text: str, sender: Optional[str] = None) -> str:
    """Extract assignee using spaCy NLP with fallback to regex."""
    if not nlp:
        return extract_assignee(text, sender)

    doc = nlp(text)

    # Look for PERSON entities
    persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
    if persons:
        logger.debug(f"spaCy PERSON entity found: {redact_pii(persons[0])}")
        return persons[0]

    # Look for nominal subjects attached to action verbs
    for token in doc:
        if token.dep_ == "nsubj" and token.head.pos_ == "VERB":
            # Filter out pronouns like "I", "we", "you" unless specific
            if token.text.lower() not in ["i", "we", "you", "they", "he", "she", "it"]:
                logger.debug(f"spaCy nsubj found: {redact_pii(token.text)}")
                return token.text

    return extract_assignee(text, sender)


def contains_action_keyword(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in ACTION_KEYWORDS)


def extract_task(text: str) -> str:
    cleaned = NEWLINE_PATTERN.sub(" ", text)
    cleaned = WHITESPACE_PATTERN.sub(" ", cleaned).strip()

    sentences = SENTENCE_SPLIT_PATTERN.split(cleaned)
    sentences = [s.strip() for s in sentences if s.strip()]

    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(keyword in sentence_lower for keyword in ACTION_KEYWORDS):
            logger.debug(
                f"Task keyword match found in sentence: '{redact_pii(sentence[:50])}...'"
            )
            return sentence.strip()

    if sentences:
        first_part = sentences[0].split(",")[0]
        if len(first_part.split()) <= 10:
            return first_part.strip()

    return cleaned[:100]


def extract_task_nlp(text: str) -> str:
    """Extract task using spaCy NLP with fallback to regex."""
    if not nlp:
        return extract_task(text)

    doc = nlp(text)

    # Find the main verb (ROOT)
    for token in doc:
        if token.dep_ == "ROOT" and token.pos_ == "VERB":
            logger.debug(f"spaCy ROOT verb found: {redact_pii(token.text)}")
            # Extract the subtree of the verb (the action phrase)
            subtree = list(token.subtree)
            # Reconstruct the phrase
            phrase = " ".join([t.text for t in subtree])
            # Clean up punctuation
            return phrase.strip(".,!?")

    return extract_task(text)


def parse_message(message_data: dict) -> Optional[dict]:
    text = message_data.get("message", "")
    sender = message_data.get("sender")
    timestamp = message_data.get("timestamp")

    if not text or not isinstance(text, str):
        return None

    expanded_text = expand_abbreviations(text)

    if not contains_action_keyword(expanded_text):
        return None

    # Use NLP functions if spaCy is available, otherwise fall back to regex
    if nlp:
        task = extract_task_nlp(expanded_text)
        assignee = extract_assignee_nlp(expanded_text, sender)
    else:
        task = extract_task(expanded_text)
        assignee = extract_assignee(expanded_text, sender)

    if not task or len(task.split()) < 1:
        return None

    deadline = extract_date(expanded_text)
    urgency = detect_urgency(expanded_text)
    priority = urgency if urgency else "Medium"
    deadline = deadline if deadline else None
    project_phase = detect_project_phase(expanded_text)

    return {
        "task": task,
        "assignee": assignee,
        "deadline": deadline,
        "priority": priority,
        "project_phase": project_phase,
        "original_message": text,
        "sender": sender,
        "timestamp": timestamp,
        "group_name": message_data.get("group_name"),
        "group_jid": message_data.get("group_jid"),
    }


def parse_messages(messages: list[dict]) -> list[dict]:
    action_items = []
    for idx, message in enumerate(messages):
        parsed = parse_message(message)
        if parsed:
            parsed["message_ref"] = idx
            action_items.append(parsed)
    return action_items

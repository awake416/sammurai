import re
from typing import Optional


def redact_pii(text: Optional[str]) -> str:
    """Redact potential PII patterns from text.

    Handles phone numbers, emails, and common token patterns.
    """
    if not text:
        return ""

    # Phone numbers: 10+ digits, or formatted like +1-234-567-8900, (234) 567-8900, 234.567.8900
    phone_pattern = (
        r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b\d{10,}\b"
    )
    redacted = re.sub(phone_pattern, "[REDACTED]", text)

    # Emails: catch full email addresses
    redacted = re.sub(r"[\w.-]+@[\w.-]+", "[REDACTED]", redacted)

    # Bearer tokens/API keys in headers
    token_pattern = r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*"
    redacted = re.sub(token_pattern, "Bearer [REDACTED]", redacted)

    return redacted

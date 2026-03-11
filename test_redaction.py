import re


def _redact_pii(text: str) -> str:
    if not text:
        return text

    # Phone numbers: 10+ digits, or formatted like +1-234-567-8900, (234) 567-8900, 234.567.8900
    phone_pattern = (
        r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b\d{10,}\b"
    )
    redacted = re.sub(phone_pattern, "[REDACTED]", text)

    # Emails
    redacted = re.sub(r"@[\w.-]+", "[REDACTED]", redacted)

    # Bearer tokens
    token_pattern = r"Bearer\s+[a-zA-Z0-9\-._~+/]+=*"
    redacted = re.sub(token_pattern, "Bearer [REDACTED]", redacted)

    return redacted


test_cases = [
    "+1-234-567-8900",
    "(234) 567-8900",
    "234.567.8900",
    "1234567890",
    "Bearer sk-1234567890abcdef",
    "my email is test@example.com",
]

for tc in test_cases:
    print(f"Original: {tc}")
    print(f"Redacted: {_redact_pii(tc)}")
    print("-" * 20)

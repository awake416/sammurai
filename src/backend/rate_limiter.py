"""Token bucket rate limiter for outbound WhatsApp messages."""

import time


class RateLimiter:
    """Simple token bucket rate limiter."""

    def __init__(self, max_per_minute: int = 10):
        self.max_per_minute = max_per_minute
        self.tokens = float(max_per_minute)
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(
            self.max_per_minute,
            self.tokens + elapsed * (self.max_per_minute / 60.0),
        )
        self.last_refill = now

    def acquire(self) -> bool:
        """Try to acquire a token. Returns True if allowed, False if rate limited."""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def wait_time(self) -> float:
        """Seconds until next token is available."""
        self._refill()
        if self.tokens >= 1.0:
            return 0.0
        deficit = 1.0 - self.tokens
        return deficit * (60.0 / self.max_per_minute)

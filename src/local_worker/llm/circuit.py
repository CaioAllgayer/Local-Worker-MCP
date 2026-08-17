"""Tiny circuit breaker so a powered-off desktop does not get probed on every call."""

from __future__ import annotations

import time
from typing import Any

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 2, cooldown_seconds: float = 60.0):
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = cooldown_seconds
        self.failures = 0
        self.opened_at: float | None = None
        self.state = STATE_CLOSED

    def allow(self) -> bool:
        if self.state == STATE_CLOSED:
            return True
        if self.state == STATE_OPEN:
            if self.opened_at is None:
                return False
            if time.monotonic() - self.opened_at >= self.cooldown_seconds:
                self.state = STATE_HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.state = STATE_CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == STATE_HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = STATE_OPEN
            self.opened_at = time.monotonic()

    def snapshot(self) -> dict[str, Any]:
        remaining = 0.0
        if self.state == STATE_OPEN and self.opened_at is not None:
            remaining = max(0.0, self.cooldown_seconds - (time.monotonic() - self.opened_at))
        return {
            "state": self.state,
            "failures": self.failures,
            "cooldown_remaining_seconds": round(remaining, 1),
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }

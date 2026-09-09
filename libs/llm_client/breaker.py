"""Circuit breaker.

When the provider is degraded, stop sending it traffic and let callers fall back to
the deterministic classifier immediately rather than paying the timeout every time.
"""

from __future__ import annotations

import time
from enum import StrEnum

from libs.common.logging import get_logger

log = get_logger("llm_client.breaker")


class BreakerState(StrEnum):
    CLOSED = "closed"  # healthy, calls pass through
    OPEN = "open"  # failing, calls are refused immediately
    HALF_OPEN = "half_open"  # probing with a single call


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, reset_timeout_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._state = BreakerState.CLOSED

    @property
    def state(self) -> BreakerState:
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self.reset_timeout_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            log.info("breaker_half_open")
        return self._state

    def allows_request(self) -> bool:
        return self.state is not BreakerState.OPEN

    def record_success(self) -> None:
        if self._state is not BreakerState.CLOSED:
            log.info("breaker_closed")
        self._failures = 0
        self._opened_at = None
        self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        # A failed probe in half-open sends us straight back to open.
        if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = time.monotonic()
            log.warning("breaker_open", failures=self._failures)

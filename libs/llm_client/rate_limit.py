"""Redis token bucket, shared across service replicas.

Horizontal scaling must not multiply the effective provider rate limit
(ARCHITECTURE.md sec. 6), so the bucket lives in Redis rather than in-process. The
refill and take are done in one Lua script to keep them atomic under concurrency.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from libs.common.logging import get_logger

log = get_logger("llm_client.rate_limit")

# KEYS[1] bucket hash; ARGV: capacity, refill_per_second, now, requested
_TAKE_TOKENS = """
local bucket = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  updated_at = now
end

tokens = math.min(capacity, tokens + (now - updated_at) * refill)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_at', now)
redis.call('EXPIRE', KEYS[1], 3600)

local retry_after = 0
if allowed == 0 and refill > 0 then
  retry_after = math.ceil((requested - tokens) / refill)
end
return {allowed, retry_after}
"""


class ScriptRunner(Protocol):
    async def eval(self, script: str, numkeys: int, *args: Any) -> Any: ...


class RateLimiterUnavailable(Exception):
    """The shared bucket could not be reached. Raised only when fail_open is False."""


class TokenBucketRateLimiter:
    def __init__(
        self,
        backend: ScriptRunner | None,
        *,
        key: str = "llm:ratelimit",
        per_minute: int = 120,
        burst: int | None = None,
        fail_open: bool = True,
    ) -> None:
        self.backend = backend
        self.key = key
        self.capacity = burst or per_minute
        self.refill_per_second = per_minute / 60.0
        # Callers that protect an expensive resource (the edge limiter) set this False
        # and degrade to a local bucket instead of admitting everything.
        self.fail_open = fail_open

    async def acquire(self, tokens: int = 1) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        if self.backend is None:
            return True, 0
        try:
            allowed, retry_after = await self.backend.eval(
                _TAKE_TOKENS,
                1,
                self.key,
                self.capacity,
                self.refill_per_second,
                time.time(),
                tokens,
            )
        except Exception as exc:  # noqa: BLE001
            if not self.fail_open:
                raise RateLimiterUnavailable(str(exc)) from exc
            log.warning("rate_limiter_unavailable_failing_open", error=str(exc))
            return True, 0
        return bool(int(allowed)), int(retry_after)


class InMemoryRateLimiter:
    """Single-process token bucket. Test double and local-dev fallback."""

    def __init__(self, *, per_minute: int = 120, burst: int | None = None) -> None:
        self.capacity = float(burst or per_minute)
        self.refill_per_second = per_minute / 60.0
        self.tokens = self.capacity
        self.updated_at = time.monotonic()

    async def acquire(self, tokens: int = 1) -> tuple[bool, int]:
        now = time.monotonic()
        self.tokens = min(
            self.capacity, self.tokens + (now - self.updated_at) * self.refill_per_second
        )
        self.updated_at = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True, 0
        deficit = tokens - self.tokens
        retry_after = int(deficit / self.refill_per_second) + 1 if self.refill_per_second else 60
        return False, retry_after

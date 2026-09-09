"""Per-user rate limiting at the edge.

Two tiers, per API_DESIGN: 100 req/min on standard endpoints and 20 req/min on
LLM-backed ones, because a model call costs orders of magnitude more than a SELECT.
The bucket lives in Redis so the limit is per user, not per replica.
"""

from __future__ import annotations

from typing import Any

from libs.common.config import Settings
from libs.common.errors import RateLimitedError
from libs.common.logging import get_logger
from libs.llm_client.rate_limit import (
    InMemoryRateLimiter,
    RateLimiterUnavailable,
    TokenBucketRateLimiter,
)

log = get_logger("gateway.rate_limit")

# Paths whose cost is a model call rather than a query.
LLM_PATH_PREFIXES = ("/assistant", "/insights/summary")


def tier_for(path: str) -> str:
    return "llm" if any(p in path for p in LLM_PATH_PREFIXES) else "standard"


class UserRateLimiter:
    """One token bucket per (user, tier).

    Shared in Redis so the limit is per user rather than per replica. If Redis is
    unreachable the limiter degrades to a per-replica bucket rather than failing open:
    a Redis outage should cost accuracy of the limit, not the limit itself. The effective
    ceiling then becomes `limit x replicas`, which is still bounded - unlike admitting
    every request, which is how a cache outage turns into a bill or an outage downstream.
    """

    def __init__(self, settings: Settings, *, redis_client: Any | None = None) -> None:
        self.settings = settings
        self.redis = redis_client
        self._local: dict[str, InMemoryRateLimiter] = {}
        self._degraded = False

    def _per_minute(self, tier: str) -> int:
        return self.settings.rate_limit_llm if tier == "llm" else self.settings.rate_limit_standard

    def _shared(self, key: str, tier: str) -> TokenBucketRateLimiter:
        return TokenBucketRateLimiter(
            self.redis, key=key, per_minute=self._per_minute(tier), fail_open=False
        )

    def _local_bucket(self, key: str, tier: str) -> InMemoryRateLimiter:
        if key not in self._local:
            self._local[key] = InMemoryRateLimiter(per_minute=self._per_minute(tier))
        return self._local[key]

    async def _acquire(self, key: str, tier: str) -> tuple[bool, int]:
        if self.redis is None:
            return await self._local_bucket(key, tier).acquire()
        try:
            result = await self._shared(key, tier).acquire()
        except RateLimiterUnavailable as exc:
            # Degrade to a per-replica bucket. Log the transition once, not per request.
            if not self._degraded:
                log.warning("rate_limiter_degraded_to_local_bucket", error=str(exc))
                self._degraded = True
            return await self._local_bucket(key, tier).acquire()
        if self._degraded:
            log.info("rate_limiter_recovered")
            self._degraded = False
        return result

    async def check(self, *, user_id: str, path: str) -> None:
        tier = tier_for(path)
        key = f"ratelimit:{tier}:{user_id}"
        allowed, retry_after = await self._acquire(key, tier)
        if not allowed:
            log.info("rate_limited", user_id=user_id, tier=tier, path=path)
            raise RateLimitedError(
                f"Rate limit exceeded for {tier} endpoints. Try again in {retry_after}s.",
                retry_after=retry_after,
            )

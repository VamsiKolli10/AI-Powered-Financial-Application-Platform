"""Shared Redis connection helper."""

from __future__ import annotations

from typing import Any

from libs.common.config import Settings, get_settings
from libs.common.logging import get_logger

log = get_logger("redis")

_client: Any | None = None


def get_redis(settings: Settings | None = None) -> Any:
    """Lazily create the shared Redis client (no connection is made until first use)."""
    global _client
    if _client is None:
        import redis.asyncio as redis

        settings = settings or get_settings()
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def check_redis() -> None:
    """Readiness probe."""
    await get_redis().ping()


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None

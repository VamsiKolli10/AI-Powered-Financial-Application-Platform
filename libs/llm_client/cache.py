"""Redis-backed response cache for LLM classifications.

Keyed on the merchant (see `libs.common.text.cache_key`), so every branch of the same
merchant shares one entry - the single biggest cost and latency win in the system.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from libs.common.logging import get_logger
from libs.common.text import cache_key

log = get_logger("llm_client.cache")


class CacheBackend(Protocol):
    """The slice of the Redis API this cache needs (tests supply an in-memory double)."""

    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    async def delete(self, *keys: str) -> Any: ...


class InMemoryCache:
    """Process-local cache backend. Test double, and a usable degraded mode."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._data[key] = value

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._data.pop(key, None)


class ClassificationCache:
    def __init__(
        self, backend: CacheBackend, *, ttl_seconds: int = 604_800, prompt_version: str = "v1"
    ) -> None:
        self.backend = backend
        self.ttl_seconds = ttl_seconds
        self.prompt_version = prompt_version
        self.hits = 0
        self.misses = 0

    def key_for(self, normalized_description: str) -> str:
        return cache_key(normalized_description, version=self.prompt_version)

    async def get(self, normalized_description: str) -> dict | None:
        key = self.key_for(normalized_description)
        try:
            raw = await self.backend.get(key)
        except Exception as exc:  # noqa: BLE001 - a cache outage must not fail the request
            log.warning("cache_read_failed", error=str(exc))
            return None
        if raw is None:
            self.misses += 1
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("cache_entry_corrupt", key=key)
            return None
        self.hits += 1
        return payload

    async def set(self, normalized_description: str, payload: dict) -> None:
        try:
            await self.backend.set(
                self.key_for(normalized_description),
                json.dumps(payload),
                ex=self.ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("cache_write_failed", error=str(exc))

    async def invalidate(self, normalized_description: str) -> None:
        """Called when a user corrects a category: the cached answer was wrong."""
        try:
            await self.backend.delete(self.key_for(normalized_description))
        except Exception as exc:  # noqa: BLE001
            log.warning("cache_delete_failed", error=str(exc))

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0

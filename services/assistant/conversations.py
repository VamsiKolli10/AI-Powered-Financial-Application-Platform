"""Conversation history.

Chat history is session state, not a financial record, so it lives in Redis under a TTL
rather than in Postgres. A lost conversation is an inconvenience; a lost transaction is
a defect - keeping them in different stores keeps that distinction honest.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from libs.common.logging import get_logger

log = get_logger("assistant.conversations")

DEFAULT_TTL_SECONDS = 60 * 60 * 24  # a day
MAX_TURNS = 20


class ConversationStore(Protocol):
    async def history(self, conversation_id: str, user_id: str) -> list[dict]: ...

    async def append(self, conversation_id: str, user_id: str, turns: list[dict]) -> None: ...


def _key(conversation_id: str, user_id: str) -> str:
    # The user id is part of the key, so one user can never read another's conversation
    # even by guessing an id.
    return f"assistant:conv:{user_id}:{conversation_id}"


class InMemoryConversationStore:
    """Process-local history: tests, and local dev without Redis."""

    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}

    async def history(self, conversation_id: str, user_id: str) -> list[dict]:
        return list(self._data.get(_key(conversation_id, user_id), []))

    async def append(self, conversation_id: str, user_id: str, turns: list[dict]) -> None:
        key = _key(conversation_id, user_id)
        self._data[key] = (self._data.get(key, []) + turns)[-MAX_TURNS:]


class RedisConversationStore:
    """Redis-backed history with a per-replica fallback.

    If Redis is unreachable, history is kept in this process instead of disappearing:
    a follow-up question still has context as long as it lands on the same replica.
    Across replicas, or after a restart, that context is lost - which is the honest
    degradation for session state and the reason Redis is in the readiness probe.
    """

    def __init__(self, redis_client: Any, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self._fallback = InMemoryConversationStore()
        self._degraded = False

    def _degrade(self, event: str, exc: Exception) -> None:
        # Log the transition, not every call, so an outage does not flood the logs.
        if not self._degraded:
            log.warning(event, error=str(exc), fallback="in_process")
            self._degraded = True

    async def history(self, conversation_id: str, user_id: str) -> list[dict]:
        try:
            raw = await self.redis.get(_key(conversation_id, user_id))
        except Exception as exc:  # noqa: BLE001 - history is best-effort
            self._degrade("history_read_failed", exc)
            return await self._fallback.history(conversation_id, user_id)
        if self._degraded:
            log.info("history_store_recovered")
            self._degraded = False
        if not raw:
            return []
        try:
            turns = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return turns if isinstance(turns, list) else []

    async def append(self, conversation_id: str, user_id: str, turns: list[dict]) -> None:
        existing = await self.history(conversation_id, user_id)
        merged = (existing + turns)[-MAX_TURNS:]
        try:
            await self.redis.set(
                _key(conversation_id, user_id), json.dumps(merged), ex=self.ttl_seconds
            )
        except Exception as exc:  # noqa: BLE001
            self._degrade("history_write_failed", exc)
            await self._fallback.append(conversation_id, user_id, turns)

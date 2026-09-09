"""Kafka consumer loop.

Runs as a background asyncio task inside a service's lifespan. Offsets are committed
only after the handler succeeds, so a crash mid-handler replays the message rather
than losing it - which is why handlers must be idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from libs.common.logging import get_logger
from libs.events.schemas import BaseEvent, Topics, parse_event

log = get_logger("events.consumer")

EventHandler = Callable[[BaseEvent], Awaitable[None]]


class KafkaEventConsumer:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        group_id: str,
        handler: EventHandler,
        topics: tuple[str, ...] = (Topics.TRANSACTIONS,),
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.handler = handler
        self.topics = topics
        self._consumer: Any | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer = AIOKafkaConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._run(), name=f"consumer-{self.group_id}")
        log.info("kafka_consumer_started", group_id=self.group_id, topics=list(self.topics))

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def _run(self) -> None:
        assert self._consumer is not None
        try:
            async for message in self._consumer:
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            log.error("consumer_loop_error", error=str(exc))

    async def _handle_message(self, message: Any) -> None:
        try:
            payload = json.loads(message.value)
        except (TypeError, ValueError):
            log.warning("event_undecodable_skipped", offset=getattr(message, "offset", None))
            await self._commit()
            return

        event = parse_event(payload)
        if event is None:
            # Unknown type: a newer producer during a rolling deploy. Skip, don't block.
            log.info("event_type_unknown_skipped", event_type=payload.get("event_type"))
            await self._commit()
            return

        try:
            await self.handler(event)
        except Exception as exc:  # noqa: BLE001
            # Do not commit: the message is redelivered rather than silently lost.
            log.error("event_handler_failed", event_type=event.event_type, error=str(exc))
            return
        await self._commit()

    async def _commit(self) -> None:
        if self._consumer is not None:
            await self._consumer.commit()

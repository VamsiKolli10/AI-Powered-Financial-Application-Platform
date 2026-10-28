"""Kafka consumer loop.

Runs as a background asyncio task inside a service's lifespan. Offsets are committed
only after the handler succeeds, so a crash mid-handler replays the message rather
than losing it - which is why handlers must be idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from aiokafka import ConsumerRebalanceListener, TopicPartition
from aiokafka.errors import CommitFailedError
from pydantic import ValidationError

from libs.common.logging import get_logger
from libs.events.schemas import BaseEvent, Topics, parse_event

log = get_logger("events.consumer")

EventHandler = Callable[[BaseEvent], Awaitable[None]]
RETRY_DELAY_SECONDS = 1.0


class KafkaEventConsumer(ConsumerRebalanceListener):
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        group_id: str,
        handler: EventHandler,
        topics: tuple[str, ...] = (Topics.TRANSACTIONS,),
        client_options: Mapping[str, Any] | None = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.handler = handler
        self.topics = topics
        self.client_options = dict(client_options or {})
        self._consumer: Any | None = None
        self._task: asyncio.Task[None] | None = None
        self._assignment_epoch = 0

    async def on_partitions_revoked(self, revoked: set[TopicPartition]) -> None:
        # aiokafka retains the old assignment while rejoining. Invalidate any
        # in-flight record explicitly so retries resume polling after revocation.
        self._assignment_epoch += 1

    async def on_partitions_assigned(self, assigned: set[TopicPartition]) -> None:
        self._assignment_epoch += 1

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer

        self._consumer = AIOKafkaConsumer(
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            **self.client_options,
        )
        self._consumer.subscribe(topics=self.topics, listener=self)
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
        while True:
            try:
                async for message in self._consumer:
                    partition = TopicPartition(message.topic, message.partition)
                    assignment_epoch = self._assignment_epoch
                    # Not committing does not rewind Kafka's read position. Hold
                    # this record until it succeeds, so a later commit cannot
                    # acknowledge a failed handler. If ownership changes, the new
                    # consumer replays it from the last committed offset.
                    while (
                        assignment_epoch == self._assignment_epoch
                        and partition in self._consumer.assignment()
                    ):
                        try:
                            if await self._handle_message(
                                message, assignment_epoch=assignment_epoch
                            ):
                                break
                        except Exception as exc:  # noqa: BLE001 - includes commit failures
                            log.error(
                                "consumer_message_error", offset=message.offset, error=str(exc)
                            )
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry transient fetch failures
                log.error("consumer_loop_error", error=str(exc))
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    async def _handle_message(self, message: Any, *, assignment_epoch: int | None = None) -> bool:
        try:
            payload = json.loads(message.value)
        except (TypeError, ValueError):
            log.warning("event_undecodable_skipped", offset=getattr(message, "offset", None))
            await self._commit(message, assignment_epoch=assignment_epoch)
            return True

        if not isinstance(payload, dict):
            log.warning("event_invalid_skipped", offset=message.offset)
            await self._commit(message, assignment_epoch=assignment_epoch)
            return True
        try:
            event = parse_event(payload)
        except ValidationError:
            # Invalid known events cannot succeed on retry. Log metadata only;
            # validation errors can include the raw financial payload.
            log.warning("event_invalid_skipped", offset=message.offset)
            await self._commit(message, assignment_epoch=assignment_epoch)
            return True
        if event is None:
            # Unknown type: a newer producer during a rolling deploy. Skip, don't block.
            log.info("event_type_unknown_skipped", event_type=payload.get("event_type"))
            await self._commit(message, assignment_epoch=assignment_epoch)
            return True

        try:
            await self.handler(event)
        except Exception as exc:  # noqa: BLE001
            # The loop retries this same record before reading another one.
            log.error("event_handler_failed", event_type=event.event_type, error=str(exc))
            return False
        await self._commit(message, assignment_epoch=assignment_epoch)
        return True

    async def _commit(self, message: Any, *, assignment_epoch: int | None = None) -> None:
        if self._consumer is not None:
            if assignment_epoch is not None and assignment_epoch != self._assignment_epoch:
                raise CommitFailedError("Partition assignment changed during processing")
            partition = TopicPartition(message.topic, message.partition)
            await self._consumer.commit({partition: message.offset + 1})

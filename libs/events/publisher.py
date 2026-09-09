"""Event publishing.

Three implementations, one interface:
  * `KafkaEventPublisher` - real aiokafka producer
  * `NullEventPublisher`  - logs and drops, used when KAFKA_ENABLED=false
  * `InMemoryEventPublisher` - records events, used by tests and the local fan-out demo

Publishing must never break the transaction that triggered it: a broker outage is
logged and swallowed, because the durable write already succeeded and the ledger is
the system of record, not the event log.
"""

from __future__ import annotations

from typing import Any, Protocol

from libs.common.logging import get_logger
from libs.events.schemas import BaseEvent, Topics

log = get_logger("events.publisher")


class EventPublisher(Protocol):
    async def publish(self, event: BaseEvent, *, topic: str = Topics.TRANSACTIONS) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class NullEventPublisher:
    """No broker configured. Events are logged so the flow is still visible locally."""

    async def publish(self, event: BaseEvent, *, topic: str = Topics.TRANSACTIONS) -> None:
        log.info("event_dropped_no_broker", topic=topic, event_type=event.event_type)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class InMemoryEventPublisher:
    """Collects published events; optionally forwards them to handlers in-process."""

    def __init__(self, handlers: list[Any] | None = None) -> None:
        self.events: list[BaseEvent] = []
        self.handlers = handlers or []

    async def publish(self, event: BaseEvent, *, topic: str = Topics.TRANSACTIONS) -> None:
        self.events.append(event)
        for handler in self.handlers:
            await handler(event)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def of_type(self, event_type: str) -> list[BaseEvent]:
        return [e for e in self.events if e.event_type == event_type]


class KafkaEventPublisher:
    def __init__(self, bootstrap_servers: str, *, client_id: str = "transactions") -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self._producer: Any | None = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            client_id=self.client_id,
            value_serializer=lambda v: v.encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            enable_idempotence=True,
            acks="all",
        )
        await self._producer.start()
        log.info("kafka_producer_started", bootstrap_servers=self.bootstrap_servers)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, event: BaseEvent, *, topic: str = Topics.TRANSACTIONS) -> None:
        if self._producer is None:
            log.warning("publish_before_start", event_type=event.event_type)
            return
        try:
            await self._producer.send_and_wait(
                topic,
                value=event.model_dump_json(),
                key=event.partition_key or None,
            )
            log.info("event_published", topic=topic, event_type=event.event_type)
        except Exception as exc:  # noqa: BLE001 - the ledger write already succeeded
            log.error(
                "event_publish_failed",
                topic=topic,
                event_type=event.event_type,
                error=str(exc),
            )

"""Independent durable enrichment and outbox loops, safe across API replicas."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.classification import Classification
from libs.common.logging import get_logger
from libs.db.models import CategorizationJob, OutboxEvent, Transaction
from libs.db.work import WorkClaim, WorkRepository
from libs.events.publisher import EventPublisher
from libs.events.schemas import parse_event
from services.transactions import categorizer, service

SessionScope = Callable[[], AbstractAsyncContextManager[AsyncSession]]
log = get_logger("transactions.workers")


class TransactionWorkers:
    def __init__(
        self,
        scope: SessionScope,
        *,
        classifier: service.Classifier | None = None,
        publisher: EventPublisher | None = None,
        anomaly_threshold: float = 0.8,
        lease_seconds: float = 120,
        poll_seconds: float = 0.5,
    ) -> None:
        self.scope = scope
        self.classifier = classifier
        self.publisher = publisher
        self.anomaly_threshold = anomaly_threshold
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self._tasks: list[asyncio.Task] = []
        self._producer_started = False

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop(self.categorize_once))]
        if self.publisher is not None:
            self._tasks.append(asyncio.create_task(self._loop(self.publish_once)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._stop_producer()

    async def _loop(self, step: Callable[[], Awaitable[bool]]) -> None:
        while True:
            try:
                if await step():
                    continue
            except Exception as exc:  # noqa: BLE001 - DB outages must not kill polling
                log.warning("worker_step_failed", error_type=type(exc).__name__)
            await asyncio.sleep(self.poll_seconds)

    async def _retry(
        self, model: type[CategorizationJob] | type[OutboxEvent], claim: WorkClaim, exc: Exception
    ) -> None:
        async with self.scope() as session:
            await WorkRepository(session, model).retry(claim, exc)
        log.warning("durable_work_retry", work_id=claim.id, error_type=type(exc).__name__)

    async def categorize_once(self) -> bool:
        async with self.scope() as session:
            claim = await WorkRepository(session, CategorizationJob).claim(
                lease_seconds=self.lease_seconds
            )
            if claim is None:
                return False
            transaction = await session.get(Transaction, claim.id)
            if transaction is None:
                return True
            description, amount = transaction.normalized_description, transaction.amount

        # No open database transaction or row lock during a provider request.
        try:
            try:
                async with asyncio.timeout(self.lease_seconds * 0.75):
                    result = (
                        await self.classifier(description, amount)
                        if self.classifier is not None
                        else categorizer.classify(description, amount)
                    )
            except Exception as exc:  # noqa: BLE001 - provider outage uses the rules engine
                log.warning("worker_provider_fallback", error_type=type(exc).__name__)
                result = categorizer.classify(description, amount)

            async def classified(_description: str, _amount: object) -> Classification:
                return result

            async with self.scope() as session:
                repo = WorkRepository(session, CategorizationJob)
                if not await repo.owns(claim):
                    return True
                transaction = await service.categorize_transaction(
                    session, claim.id, classifier=classified
                )
                if transaction is not None:
                    events = await service.build_categorization_events(
                        session, transaction, anomaly_threshold=self.anomaly_threshold
                    )
                    for event in events:
                        existing = await session.scalar(
                            select(OutboxEvent.id).where(
                                OutboxEvent.transaction_id == transaction.id,
                                OutboxEvent.event_type == event.event_type,
                            )
                        )
                        if existing is None:
                            session.add(
                                OutboxEvent(
                                    id=event.event_id,
                                    transaction_id=transaction.id,
                                    event_type=event.event_type,
                                    partition_key=event.partition_key,
                                    payload=event.model_dump(mode="json"),
                                )
                            )
                            await session.flush()
                # The enrichment, its events, and job completion commit together.
                await repo.complete(claim)
        except Exception as exc:  # noqa: BLE001 - leave failed work recoverable
            await self._retry(CategorizationJob, claim, exc)
        return True

    async def publish_once(self) -> bool:
        if self.publisher is None:
            return False
        async with self.scope() as session:
            claim = await WorkRepository(session, OutboxEvent).claim(
                lease_seconds=self.lease_seconds
            )
            if claim is None:
                return False
            record = await session.get(OutboxEvent, claim.id)
            if record is None:
                return True
            payload = record.payload
        try:
            async with asyncio.timeout(self.lease_seconds * 0.75):
                if not self._producer_started:
                    await self.publisher.start()
                    self._producer_started = True
                event = parse_event(payload)
                if event is None:
                    raise ValueError("Unknown outbox event type")
                await self.publisher.publish(event)
            async with self.scope() as session:
                repo = WorkRepository(session, OutboxEvent)
                if await repo.owns(claim):
                    await repo.complete(claim)
        except Exception as exc:  # noqa: BLE001 - no acknowledgement means no completion
            await self._stop_producer()
            await self._retry(OutboxEvent, claim, exc)
        return True

    async def _stop_producer(self) -> None:
        if self.publisher is not None:
            try:
                async with asyncio.timeout(5):
                    await self.publisher.stop()
            except Exception as exc:  # noqa: BLE001 - polling can reconnect on next attempt
                log.warning("producer_cleanup_failed", error_type=type(exc).__name__)
            self._producer_started = False
